"""
Multi-provider AI fallback layer.

Tries providers in order until one succeeds:
  1. Gemini (google-genai, vision-capable)
  2. Groq (OpenAI-compatible REST)
  3. OpenRouter (OpenAI-compatible REST, free models)
  4. Hugging Face (Inference API REST)
  5. Cerebras (OpenAI-compatible REST)

Each provider is optional — only configured keys are tried.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import aiohttp

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_FALLBACK_MODELS,
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_URL,
    CEREBRAS_API_KEY,
    CEREBRAS_MODEL,
    CEREBRAS_URL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    OPENROUTER_URL,
    HUGGINGFACE_API_KEY,
)

log = logging.getLogger("vyrion.ai_providers")

# ── Gemini client (lazy) ──────────────────────────────────────────────────────

_genai_client = None
_genai_types = None

def _get_gemini():
    global _genai_client, _genai_types
    if _genai_client is not None:
        return _genai_client, _genai_types
    if not GEMINI_API_KEY:
        return None, None
    try:
        from google import genai
        from google.genai import types as genai_types
        _genai_client = genai.Client(api_key=GEMINI_API_KEY)
        _genai_types = genai_types
        return _genai_client, _genai_types
    except Exception as e:
        log.warning("google-genai unavailable: %s", e)
        return None, None


# ── OpenAI-compatible REST helpers (Groq, OpenRouter, Cerebras) ────────────────

async def _openai_compat_chat(
    url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.8,
    max_tokens: int = 1200,
    timeout: int = 30,
) -> str:
    """Call an OpenAI-compatible /chat/completions endpoint."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("%s returned %d: %s", url, resp.status, body[:300])
                    return ""
                data = await resp.json()
                return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
    except Exception as e:
        log.warning("%s request failed: %s", url, e)
        return ""


# ── Hugging Face Inference API ─────────────────────────────────────────────────

HF_URL = "https://api-inference.huggingface.co/models/{model}"
HF_MODEL = "meta-llama/Llama-3.2-3B-Instruct"

async def _huggingface_chat(
    messages: list[dict],
    *,
    temperature: float = 0.8,
    max_tokens: int = 1200,
) -> str:
    if not HUGGINGFACE_API_KEY:
        return ""
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    convo_parts = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "User" if m["role"] == "user" else "Assistant"
        convo_parts.append(f"{role}: {m['content']}")
    convo_parts.append("Assistant:")

    prompt = ""
    if system_parts:
        prompt += system_parts[0] + "\n\n"
    prompt += "\n".join(convo_parts)

    headers = {
        "Authorization": f"Bearer {HUGGINGFACE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": prompt,
        "parameters": {
            "temperature": temperature,
            "max_new_tokens": max_tokens,
            "return_full_text": False,
        },
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                HF_URL.format(model=HF_MODEL),
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("HuggingFace returned %d: %s", resp.status, body[:300])
                    return ""
                data = await resp.json()
                if isinstance(data, list) and data:
                    return (data[0].get("generated_text", "") or "").strip()
                return ""
    except Exception as e:
        log.warning("HuggingFace request failed: %s", e)
        return ""


# ── Gemini generate ────────────────────────────────────────────────────────────

async def _gemini_generate(
    system_prompt: str,
    messages: list[dict],
    *,
    temperature: float = 0.8,
    max_tokens: int = 1200,
    image_parts: list[tuple[bytes, str]] | None = None,
) -> str:
    client, gt = _get_gemini()
    if client is None or gt is None:
        return ""

    contents: list = []
    for m in messages[-30:]:
        role = "user" if m["role"] == "user" else "model"
        contents.append(gt.Content(role=role, parts=[gt.Part.from_text(text=m["content"])]))

    user_parts = [gt.Part.from_text(text=messages[-1]["content"] if messages else "")]
    for data, mime in (image_parts or []):
        user_parts.append(gt.Part.from_bytes(data=data, mime_type=mime))
    if contents and contents[-1].role == "user":
        contents[-1] = gt.Content(role="user", parts=user_parts)
    else:
        contents.append(gt.Content(role="user", parts=user_parts))

    models_to_try = [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]
    for model_id in models_to_try:
        try:
            resp = await asyncio.to_thread(
                client.models.generate_content,
                model=model_id,
                contents=contents,
                config=gt.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            text = (resp.text or "").strip()
            if text:
                return text
        except Exception as e:
            log.warning("Gemini model %s failed: %s", model_id, e)
            continue
    return ""


# ── Public API: generate with full fallback chain ─────────────────────────────

async def generate(
    system_prompt: str,
    messages: list[dict],
    *,
    temperature: float = 0.8,
    max_tokens: int = 1200,
    image_parts: list[tuple[bytes, str]] | None = None,
) -> str:
    """
    Generate a text reply using the first provider that succeeds.
    Providers are tried in order: Gemini → Groq → OpenRouter → HuggingFace → Cerebras.
    """
    if GEMINI_API_KEY:
        text = await _gemini_generate(
            system_prompt, messages,
            temperature=temperature, max_tokens=max_tokens,
            image_parts=image_parts,
        )
        if text:
            return text

    rest_messages = [{"role": "system", "content": system_prompt}] + messages

    if GROQ_API_KEY:
        text = await _openai_compat_chat(
            GROQ_URL, GROQ_API_KEY, GROQ_MODEL, rest_messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        if text:
            return text

    if OPENROUTER_API_KEY:
        text = await _openai_compat_chat(
            OPENROUTER_URL, OPENROUTER_API_KEY, OPENROUTER_MODEL, rest_messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        if text:
            return text

    if HUGGINGFACE_API_KEY:
        text = await _huggingface_chat(
            rest_messages, temperature=temperature, max_tokens=max_tokens,
        )
        if text:
            return text

    if CEREBRAS_API_KEY:
        text = await _openai_compat_chat(
            CEREBRAS_URL, CEREBRAS_API_KEY, CEREBRAS_MODEL, rest_messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        if text:
            return text

    return "I hit a snag reaching the AI. Try again in a moment."


# ── Gemini function-calling (for subagent) ─────────────────────────────────────

async def gemini_function_call(
    system_prompt: str,
    contents: list,
    tools: list,
) -> Any:
    """Call Gemini with function-calling support."""
    client, gt = _get_gemini()
    if client is None or gt is None:
        return None
    config = gt.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=tools,
    )
    return await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=contents,
        config=config,
    )


def get_genai_types():
    """Return genai_types module or None."""
    _, gt = _get_gemini()
    return gt


def is_gemini_available() -> bool:
    c, _ = _get_gemini()
    return c is not None


# ── OpenAI-compatible function calling (Groq, OpenRouter, Cerebras) ────────────

async def openai_function_call(
    system_prompt: str,
    messages: list[dict],
    tools_json: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 1200,
) -> dict | None:
    """
    Call an OpenAI-compatible /chat/completions endpoint with tool calling.
    Tries Groq → OpenRouter → Cerebras (whichever keys are configured).

    Returns a dict with:
      'tool_calls': list of {'name': str, 'arguments': dict} or None
      'content': str (text reply if no tool calls)
    or None if no provider is available / all fail.
    """
    rest_messages = [{"role": "system", "content": system_prompt}] + messages
    openai_tools = [{"type": "function", "function": t} for t in tools_json]

    providers = []
    if GROQ_API_KEY:
        providers.append((GROQ_URL, GROQ_API_KEY, GROQ_MODEL))
    if OPENROUTER_API_KEY:
        providers.append((OPENROUTER_URL, OPENROUTER_API_KEY, OPENROUTER_MODEL))
    if CEREBRAS_API_KEY:
        providers.append((CEREBRAS_URL, CEREBRAS_API_KEY, CEREBRAS_MODEL))

    for url, key, model in providers:
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": rest_messages,
            "tools": openai_tools,
            "tool_choice": "auto",
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.warning("function-call %s returned %d: %s", url, resp.status, body[:300])
                        continue
                    data = await resp.json()
                    choice = data.get("choices", [{}])[0]
                    msg = choice.get("message", {})
                    tool_calls_raw = msg.get("tool_calls")
                    content = msg.get("content") or ""

                    if tool_calls_raw:
                        parsed_calls = []
                        for tc in tool_calls_raw:
                            fn = tc.get("function", {})
                            fn_name = fn.get("name", "")
                            fn_args_str = fn.get("arguments", "{}")
                            try:
                                fn_args = json.loads(fn_args_str) if isinstance(fn_args_str, str) else fn_args_str
                            except json.JSONDecodeError:
                                fn_args = {}
                            parsed_calls.append({"name": fn_name, "arguments": fn_args})
                        return {"tool_calls": parsed_calls, "content": ""}

                    return {"tool_calls": None, "content": content.strip()}
        except Exception as e:
            log.warning("function-call %s failed: %s", url, e)
            continue

    return None


# ── Text-based function calling fallback (works with ANY model) ───────────────

async def text_function_call(
    system_prompt: str,
    messages: list[dict],
    tools_json: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 1200,
) -> dict | None:
    """
    Text-based function calling fallback for models without native tool calling.
    Injects tool schemas into the system prompt and parses JSON from the response.

    Returns a dict with:
      'tool_calls': list of {'name': str, 'arguments': dict} or None
      'content': str (text reply if no tool calls)
    or None if no provider is available / all fail.
    """
    tool_descriptions = "\n".join(
        f"- {t['name']}: {t['description']}\n  params: {json.dumps(t['parameters'])}"
        for t in tools_json
    )

    injected_system = (
        f"{system_prompt}\n\n"
        "You have access to these functions:\n"
        f"{tool_descriptions}\n\n"
        "To call a function, respond with ONLY a JSON code block like:\n"
        '```json\n[{"name": "function_name", "arguments": {"key": "value"}}]\n```\n'
        "You can call multiple functions at once by putting them in the JSON array.\n"
        "After functions are executed, you will receive their results and can continue.\n"
        "If no function is needed, respond normally with text.\n"
    )

    rest_messages = [{"role": "system", "content": injected_system}] + messages

    text = ""
    if GROQ_API_KEY:
        text = await _openai_compat_chat(
            GROQ_URL, GROQ_API_KEY, GROQ_MODEL, rest_messages,
            temperature=temperature, max_tokens=max_tokens,
        )
    if not text and OPENROUTER_API_KEY:
        text = await _openai_compat_chat(
            OPENROUTER_URL, OPENROUTER_API_KEY, OPENROUTER_MODEL, rest_messages,
            temperature=temperature, max_tokens=max_tokens,
        )
    if not text and CEREBRAS_API_KEY:
        text = await _openai_compat_chat(
            CEREBRAS_URL, CEREBRAS_API_KEY, CEREBRAS_MODEL, rest_messages,
            temperature=temperature, max_tokens=max_tokens,
        )
    if not text and HUGGINGFACE_API_KEY:
        text = await _huggingface_chat(
            rest_messages, temperature=temperature, max_tokens=max_tokens,
        )

    if not text:
        return None

    json_blocks = re.findall(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
    if not json_blocks:
        bare = re.match(r'\s*(\[.*?\])\s*$', text, re.DOTALL)
        if bare:
            json_blocks = [bare.group(1)]

    if json_blocks:
        try:
            calls = json.loads(json_blocks[0])
            if isinstance(calls, list) and calls and isinstance(calls[0], dict) and "name" in calls[0]:
                parsed = []
                for c in calls:
                    parsed.append({"name": c.get("name", ""), "arguments": c.get("arguments", {})})
                return {"tool_calls": parsed, "content": ""}
        except json.JSONDecodeError:
            pass

    return {"tool_calls": None, "content": text.strip()}


def is_any_provider_available() -> bool:
    """Check if at least one AI provider is configured."""
    return bool(GEMINI_API_KEY or GROQ_API_KEY or OPENROUTER_API_KEY or CEREBRAS_API_KEY or HUGGINGFACE_API_KEY)
