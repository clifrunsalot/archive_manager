"""Audit records for destructive archive operations."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from archive_manager.paths import PROJECT_ROOT


def record_mutation_audit(user: str, action: str, event_ids: list[str], status: str = "completed") -> None:
    path = Path(os.environ.get("AUDIT_LOG_PATH", PROJECT_ROOT / "logs" / "query_audit.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": user,
            "action": action,
            "status": status,
            "event_ids": sorted(set(event_ids)),
        }, sort_keys=True) + "\n")