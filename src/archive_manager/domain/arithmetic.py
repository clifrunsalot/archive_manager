"""Currency reconciliation helpers that never replace declared totals."""

from decimal import Decimal, InvalidOperation
from typing import Any


def reconcile_line_totals(line_totals: dict[str, Any], declared_total: Any) -> dict[str, Any]:
    """Compare parsed line totals with a declared invoice total."""
    if not line_totals or declared_total is None:
        return {"status": "not_checked", "line_sum": None, "declared_total": declared_total}
    try:
        line_sum = sum(Decimal(str(value)) for value in line_totals.values())
        declared = Decimal(str(declared_total))
    except (InvalidOperation, TypeError, ValueError):
        return {"status": "needs_review", "line_sum": None, "declared_total": declared_total}
    difference = declared - line_sum
    return {
        "status": "matched" if abs(difference) <= Decimal("0.01") else "mismatch",
        "line_sum": f"{line_sum:.2f}",
        "declared_total": f"{declared:.2f}",
        "difference": f"{difference:.2f}",
    }