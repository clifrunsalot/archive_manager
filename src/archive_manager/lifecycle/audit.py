"""Privacy-conscious operational audit logging."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from archive_manager.paths import PROJECT_ROOT

AUDIT_LOG_PATH = Path(os.environ.get("AUDIT_LOG_PATH", PROJECT_ROOT / "logs" / "query_audit.jsonl"))


def record_query_audit(
    question: str,
    *,
    hit_count: int = 0,
    event_ids: list[str] | None = None,
    outcome: str = "completed",
):
    """Append query metadata without storing the raw question or answer."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user": os.environ.get("ARCHIVE_AUDIT_USER", "local-user"),
        "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "hit_count": hit_count,
        "event_ids": sorted(set(event_ids or [])),
        "outcome": outcome,
    }
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")