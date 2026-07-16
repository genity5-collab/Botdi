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

log = logging.getLogger("nexus.ai_providers")

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
    # Build a conversational prompt from messages
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

    # Last user message gets image parts appended
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
    # 1. Gemini (supports vision)
    if GEMINI_API_KEY:
        text = await _gemini_generate(
            system_prompt, messages,
            temperature=temperature, max_tokens=max_tokens,
            image_parts=image_parts,
        )
        if text:
            return text

    # Build OpenAI-style messages for REST providers
    rest_messages = [{"role": "system", "content": system_prompt}] + messages

    # 2. Groq
    if GROQ_API_KEY:
        text = await _openai_compat_chat(
            GROQ_URL, GROQ_API_KEY, GROQ_MODEL, rest_messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        if text:
            return text

    # 3. OpenRouter
    if OPENROUTER_API_KEY:
        text = await _openai_compat_chat(
            OPENROUTER_URL, OPENROUTER_API_KEY, OPENROUTER_MODEL, rest_messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        if text:
            return text

    # 4. Hugging Face
    if HUGGINGFACE_API_KEY:
        text = await _huggingface_chat(
            rest_messages, temperature=temperature, max_tokens=max_tokens,
        )
        if text:
            return text

    # 5. Cerebras
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
    """
    Call Gemini with function-calling support. Returns the raw response object
    or None if Gemini is not available.
    """
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
