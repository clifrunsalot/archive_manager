"""Validation and provenance for persisted EventFacts."""

from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class ValidationReport:
    """Machine-readable validation status for one EventFacts record."""

    status: str
    warnings: list[str]
    checked_fields: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_event_facts(facts: dict[str, Any]) -> ValidationReport:
    """Validate formats and required relationships without guessing values."""
    warnings = list(facts.get("warnings", [])) if isinstance(facts.get("warnings", []), list) else []
    checked = []
    if facts.get("domain"):
        checked.append("domain")
    if facts.get("vin"):
        checked.append("vin")
        if len(str(facts["vin"])) != 17:
            warnings.append("VIN format is not 17 characters")
    if facts.get("service_date"):
        checked.append("service_date")
    if facts.get("total_charges"):
        checked.append("total_charges")
        try:
            if Decimal(str(facts["total_charges"])) < 0:
                warnings.append("total charges cannot be negative")
        except InvalidOperation:
            warnings.append("total charges is not a valid decimal")
    if facts.get("fields"):
        checked.append("fields")
    status = "needs_review" if warnings else "validated"
    return ValidationReport(status, sorted(set(warnings)), checked)