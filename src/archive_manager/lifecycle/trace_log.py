"""Unified, stage-oriented trace logging for archive processing."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
import uuid

from archive_manager.paths import PROJECT_ROOT

TRACE_LOG_PATH = Path(os.environ.get("ARCHIVE_TRACE_LOG", PROJECT_ROOT / "logs" / "archive_trace.jsonl"))


def new_run_id(prefix: str = "run") -> str:
    """Create a short correlation ID shared by one script invocation."""
    return f"{prefix}_{uuid.uuid4().hex}"


def trace_event(
    run_id: str,
    stage: str,
    boundary: str,
    *,
    status: str = "info",
    **details: Any,
):
    """Append one structured trace event with a visible stage boundary."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "boundary": boundary,
        "stage": stage,
        "status": status,
        "details": details,
    }
    TRACE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRACE_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n")
