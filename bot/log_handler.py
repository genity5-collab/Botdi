"""
Rolling JSON log handler — keeps the last MAX_LINES log entries in
bot/data/recent_logs.json so the dashboard can display them.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from collections import deque

MAX_LINES = 200
_LOG_FILE = Path(__file__).parent / "data" / "recent_logs.json"
_LOG_FILE.parent.mkdir(exist_ok=True)

_buffer: deque[dict] = deque(maxlen=MAX_LINES)


class JsonFileHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        _buffer.append(entry)
        try:
            _LOG_FILE.write_text(json.dumps({"entries": list(_buffer)}, indent=2))
        except Exception:
            pass


def setup_logging() -> None:
    """Configure root logging and attach the JSON handler."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    install()


def install() -> None:
    """Attach the JSON handler to the root logger."""
    handler = JsonFileHandler()
    handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
