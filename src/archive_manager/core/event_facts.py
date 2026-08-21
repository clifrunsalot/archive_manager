"""Persisted, ingest-time facts for manifest-backed events."""

import json
import os
from pathlib import Path
from typing import Any

from archive_manager.domain.automotive_parser import extract_automotive_facts, extract_performed_services
from archive_manager.domain.domain_parsers import get_parser
from archive_manager.core.encryption import decrypt_bytes, encrypt_bytes
from archive_manager.domain.fact_validation import validate_event_facts
from archive_manager.domain.arithmetic import reconcile_line_totals
from archive_manager.paths import PROJECT_ROOT

EVENT_FACTS_PATH = Path(
    os.environ.get("EVENT_FACTS_PATH", PROJECT_ROOT / "data" / ".event_facts" / "facts.json")
)


def extract_event_facts(event_id: str, event_type: str, pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract facts once from ordered page text and retain page provenance."""
    texts = [str(page.get("text", "")) for page in pages]
    if event_type == "automotive_service":
        parsed = extract_automotive_facts(texts)
        services = extract_performed_services(texts)
        facts = {
            "event_id": event_id,
            "domain": event_type,
            "vin": parsed["vin"],
            "service_date": parsed["service_date"],
            "total_charges": parsed["total_charges"],
            "arithmetic": reconcile_line_totals(parsed["line_totals"], parsed["total_charges"]),
            "services_performed": services,
            "fields": [],
            "warnings": parsed["warnings"],
            "source_pages": [page.get("page") for page in pages],
        }
        if facts["arithmetic"]["status"] == "mismatch":
            facts["warnings"].append("line totals do not equal declared total charges")
        facts["validation"] = validate_event_facts(facts).to_dict()
        return facts

    parsed = get_parser(event_type).parse(texts)
    facts = {
        "event_id": event_id,
        "domain": parsed.domain,
        "vin": None,
        "service_date": None,
        "total_charges": None,
        "services_performed": [],
        "fields": [
            {
                "name": field.name,
                "value": field.value,
                "page": field.page,
                "confidence": field.confidence,
            }
            for field in parsed.fields
        ],
        "warnings": parsed.warnings,
        "source_pages": [page.get("page") for page in pages],
    }
    facts["validation"] = validate_event_facts(facts).to_dict()
    return facts


def load_event_facts(path: Path = EVENT_FACTS_PATH) -> dict[str, dict[str, Any]]:
    """Load persisted event facts, supporting encrypted and legacy JSON."""
    if not path.exists():
        return {}
    try:
        data = json.loads(decrypt_bytes(path.read_bytes()).decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_event_facts(facts: dict[str, dict[str, Any]], path: Path = EVENT_FACTS_PATH):
    """Atomically persist event facts using the configured metadata encryption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    serialized = json.dumps(facts, indent=2, sort_keys=True).encode("utf-8")
    temporary_path.write_bytes(encrypt_bytes(serialized))
    temporary_path.replace(path)