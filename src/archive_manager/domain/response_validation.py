"""Validation gate for model-generated record summaries."""

import re
from typing import Any


def validate_summary(summary: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Detect contradictions and prohibited work in a model summary."""
    warnings = []
    total = facts.get("total_charges")
    if total and f"{float(total):.2f}" not in summary.replace(",", ""):
        warnings.append("summary does not contain the validated total charges")
    service_date = facts.get("service_date")
    if service_date and str(service_date).upper() not in summary.upper() and str(service_date) not in summary:
        warnings.append("summary does not contain the validated service date")
    vin = facts.get("vin")
    if vin and str(vin).upper() not in summary.upper():
        warnings.append("summary does not contain the validated VIN")
    if re.search(r"recommended but not performed|declined service|declined work", summary, re.IGNORECASE):
        warnings.append("summary mentions excluded or declined work")
    return {"status": "invalid" if warnings else "valid", "warnings": warnings}