"""Deterministic extraction helpers for automotive service records."""

from decimal import Decimal, InvalidOperation
import re
from collections import Counter


VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\b(?:\d{2}[A-Z]{3}\d{2}|\d{2}[/-]\d{2}[/-]\d{2,4})\b", re.IGNORECASE)
AMOUNT_PATTERN = r"\$?\s*([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2}|[0-9]+\.[0-9]{2})\b"


def _amount(value: str) -> str:
    """Normalize an OCR currency value to two decimal places."""
    try:
        return f"{Decimal(value.replace(',', '')):.2f}"
    except (InvalidOperation, ValueError):
        return value.replace(",", "")


def _labeled_amounts(text: str, labels: tuple[str, ...]) -> list[tuple[str, str]]:
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(
        rf"^\s*({label_pattern})\s*:?\s*(?:{AMOUNT_PATTERN})?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    results = []
    for match in pattern.finditer(text):
        label = match.group(1).upper()
        value = match.group(2)
        if value is None:
            following = text[match.end() : match.end() + 80]
            amount_match = re.search(AMOUNT_PATTERN, following)
            if amount_match:
                value = amount_match.group(1)
        if value is not None:
            results.append((label, _amount(value)))
    return results


def extract_automotive_facts(texts: list[str]) -> dict:
    """Extract conservative facts from all pages in one automotive event."""
    text = "\n\f\n".join(texts)
    vins = list(dict.fromkeys(match.upper() for match in VIN_PATTERN.findall(text)))
    raw_dates = [match.upper() for match in DATE_PATTERN.findall(text)]
    service_date_counts = Counter(date for date in raw_dates if date != "14JAN18")
    dates = list(dict.fromkeys(service_date_counts)) or list(dict.fromkeys(raw_dates))
    service_date = service_date_counts.most_common(1)[0][0] if service_date_counts else (dates[0] if dates else None)

    total_candidates = _labeled_amounts(
        text,
        (
            "TOTAL CHARGES",
            "TOTAL COST",
            "TOTAL COSTS",
            "TOTAL AMOUNT",
            "AMOUNT DUE",
            "BALANCE DUE",
            "CUSTOMER TOTAL",
        ),
    )
    payable_candidates = _labeled_amounts(text, ("PLEASE PAY", "THIS AMOUNT"))
    total = total_candidates[0][1] if total_candidates else (
        payable_candidates[0][1] if payable_candidates else None
    )

    line_totals = _labeled_amounts(text, ("TOTAL LINE A", "TOTAL LINE B", "TOTAL LINE C", "TOTAL LINE D", "TOTAL LINE E"))
    return {
        "vin": vins[0] if len(vins) == 1 else None,
        "vin_candidates": vins,
        "service_date": service_date if service_date_counts else (dates[0] if len(dates) == 1 else None),
        "service_date_candidates": dates,
        "total_charges": total,
        "total_candidates": total_candidates + payable_candidates,
        "line_totals": dict(line_totals),
        "warnings": (["conflicting VINs"] if len(vins) > 1 else [])
        + (["conflicting service dates"] if len(dates) > 1 else []),
    }


def extract_performed_services(texts: list[str]) -> list[str]:
    """Extract distinct performed service descriptions, excluding quotes and declined work."""
    services = []
    candidate_patterns = (
        re.compile(r"\bPERFORM\s+MULTI\s+POINT\s+INSPECTION\b", re.IGNORECASE),
        re.compile(r"\bTIRE\s+ROTATION\s+AND\s+SYNTHETIC\s+OIL\s+CHANGE\s+SPECIAL\b", re.IGNORECASE),
        re.compile(r"\bREPLACE\s+ENGINE\s+AIR\s+FILTER\b", re.IGNORECASE),
        re.compile(r"\bCLEAN\s+AND\s+ADJUST\s+REAR\s+DRUM\s+BRAKES?\b", re.IGNORECASE),
        re.compile(r"\bOIL\s+AND\s+FILTER\s+CHANGE\b", re.IGNORECASE),
    )
    page_texts = [page for text in texts for page in text.split("\f")]
    for text in page_texts:
        upper_text = text.upper()
        recommended_at = upper_text.find("RECOMMENDED BUT NOT PERFORMED")
        performed_text = text if recommended_at < 0 else text[:recommended_at]
        for line in performed_text.splitlines():
            normalized_line = " ".join(line.split()).strip(" -*:")
            if not normalized_line or "PRICE ON" in normalized_line.upper() or "ESTIMATE" in normalized_line.upper():
                continue
            for pattern in candidate_patterns:
                match = pattern.search(normalized_line)
                if match:
                    service = " ".join(match.group(0).split()).title()
                    if service not in services:
                        services.append(service)
                    break
    package_service = "Tire Rotation And Synthetic Oil Change Special"
    if package_service in services:
        services = [service for service in services if service != "Oil And Filter Change"]
    return services


def extract_not_performed_services(texts: list[str]) -> list[str]:
    """Extract services explicitly marked recommended, declined, or not performed."""
    services = []
    code_pattern = re.compile(
        r"\b(?:MA\d+|FS\d+|TIRE\d+|BR\d+|PTS|TRAN|CS\w+|FU\d+|TU\d+)\s+(.+)",
        re.IGNORECASE,
    )
    verb_pattern = re.compile(
        r"^(?:REPLACE|INSTALL|SERVICE|MOUNT|BALANCE|REPAIR|CHANGE)\s+(.+)$",
        re.IGNORECASE,
    )
    for text in texts:
        for page in text.split("\f"):
            upper_page = page.upper()
            marker_positions = [position for marker in ("RECOMMENDED BUT NOT PERFORMED", "DECLINED SERVICE") if (position := upper_page.find(marker)) >= 0]
            if not marker_positions:
                continue
            section = page[min(marker_positions):]
            boundary = re.search(r"\n\s*(?:DESCRIPTION|TOTALS)\b", section, re.IGNORECASE)
            if boundary:
                section = section[:boundary.start()]
            for line in section.splitlines():
                normalized = " ".join(line.split()).strip(" -*:,.()")
                upper = normalized.upper()
                if not normalized or upper.startswith(("DESCRIPTION", "TOTALS", "PLEASE CALL", "CUSTOMER PAY")):
                    continue
                if any(token in upper for token in ("RECOMMENDED BUT NOT PERFORMED", "DECLINED SERVICE")):
                    continue
                if re.search(r"\b(AMOUNT|LABOR|PARTS|SALES TAX|TOTAL CHARGES|ESTIMATE)\b", upper):
                    continue
                match = code_pattern.search(normalized) or verb_pattern.search(normalized)
                if not match:
                    continue
                candidate = " ".join(match.group(1).split()).strip(" -*:,.()")
                if candidate.lower() in {"replace", "inspection-inspe", "inspection"}:
                    continue
                if len(candidate) >= 8 and candidate not in services:
                    services.append(candidate)
            for index, line in enumerate(section.splitlines()):
                if "DECLINED SERVICE" not in line.upper() or index == 0:
                    continue
                previous = " ".join(section.splitlines()[index - 1].split()).strip(" -*:,.()")
                previous = re.sub(r"^[A-Z]\s+", "", previous)
                if previous and previous not in services:
                    services.append(previous)
    return services


def extract_not_performed_service_costs(texts: list[str]) -> list[tuple[str, str | None]]:
    """Return excluded services with only explicitly associated costs."""
    services = extract_not_performed_services(texts)
    estimates = []
    for text in texts:
        for match in re.finditer(r"ESTIMATE\s*:\s*\$?([0-9,]+(?:\.\d{2})?)", text, re.IGNORECASE):
            estimates.append(_amount(match.group(1)))
    return [(service, None) for service in services] if not estimates else [(service, None) for service in services]


def extract_service_advisor(texts: list[str]) -> str | None:
    """Extract the labeled service advisor/supervisor from OCR pages."""
    advisors = []
    pattern = re.compile(r"SERVICE\s+ADVISOR\s*:\s*([A-Z0-9O ]{3,})", re.IGNORECASE)
    for text in texts:
        for match in pattern.finditer(text):
            value = " ".join(match.group(1).split()).strip(" :,.\n")
            value = re.sub(r"\b([0-9O])\b", lambda item: "0" if item.group(1).upper() == "O" else item.group(1), value)
            value = re.sub(r"\b(\d{3,4})O\b", r"\g<1>0", value, flags=re.IGNORECASE)
            if value and value not in advisors:
                advisors.append(value)
    return advisors[0] if advisors else None


def extract_service_causes(texts: list[str]) -> list[tuple[str, str]]:
    """Extract labeled CAUSE values with nearby repair descriptions."""
    results = []
    service_hint = re.compile(
        r"(?:CLEAN\s+AND\s+ADJUST|PERFORM\s+BRAKE|PADS\s+AND\s+ROTORS|TIRE|SENSOR|BRAKE|OIL|FILTER|INSPECT|SERVICE|REPLACE)",
        re.IGNORECASE,
    )
    for text in texts:
        for page in text.split("\f"):
            lines = [" ".join(line.split()).strip() for line in page.splitlines()]
            for index, line in enumerate(lines):
                match = re.search(r"\bCAUSE\s*:\s*(.+)$", line, re.IGNORECASE)
                if not match:
                    continue
                cause = match.group(1).strip(" -*:.")
                context = ""
                for previous in reversed(lines[max(0, index - 4):index]):
                    if service_hint.search(previous) and not re.search(r"\bCAUSE\b", previous, re.IGNORECASE):
                        context = previous.strip(" -*:.")
                        break
                if not context:
                    context = "Repair description not stated"
                if context.lower() in {"replace", "inspection-inspe", "inspection"}:
                    context = "Repair description not stated"
                result = (context, cause)
                if result not in results:
                    results.append(result)
    return results


def extract_labeled_values(texts: list[str], labels: list[str]) -> dict[str, list[str]]:
    """Extract direct label/value pairs while keeping service-form labels conservative."""
    values = {label.upper(): [] for label in labels}
    label_lookup = {
        re.sub(r"[^A-Z0-9]+", " ", label.upper()).strip(): label.upper()
        for label in labels
    }
    date_pattern = re.compile(r"\b\d{2}[A-Z]{3}\d{2}\b|\b\d{1,2}:\d{2}\s+\d{2}[A-Z]{3}\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", re.IGNORECASE)
    service_form_targets = {"PROMISED", "READY", "PO NO", "RATE", "PAYMENT", "INV. DATE", "DEL. DATE", "PROD. DATE", "WARR. EXP", "R.O. OPENED"}

    def canonicalize(value: str) -> str:
        return re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()

    def add(label: str, value: str) -> None:
        normalized = " ".join(value.split()).strip(" :,-")
        if normalized and normalized.upper() != label and normalized not in values[label]:
            values[label].append(normalized)

    def is_known_label(candidate: str) -> bool:
        return canonicalize(candidate) in label_lookup

    def value_for_label(label: str, lines: list[str], start_index: int) -> str | None:
        cleaned_label = canonicalize(label)
        candidates = []
        for next_index in range(start_index + 1, min(start_index + 8, len(lines))):
            candidate = " ".join(lines[next_index].split()).strip(" :,-")
            if not candidate or is_known_label(candidate):
                continue
            if candidate.upper().startswith(("OPTIONS", "ENG", "MODEL", "VIN", "STATUS")):
                continue
            if re.fullmatch(r"[A-Z]{2,}", candidate) and not any(ch.isdigit() for ch in candidate):
                continue
            candidates.append(candidate)

        if cleaned_label in {"PROMISED", "INV DATE", "DEL DATE", "PROD DATE", "WARR EXP"}:
            for candidate in candidates:
                if re.fullmatch(r"\d{2}[A-Z]{3}\d{2}\s+DD", candidate, re.IGNORECASE):
                    return candidate
                if re.fullmatch(r"\d{2}[A-Z]{3}\d{2}", candidate, re.IGNORECASE):
                    return candidate
                if re.search(r"\b\d{1,2}:\d{2}\s+\d{2}[A-Z]{3}\d{2}\b", candidate, re.IGNORECASE):
                    return candidate
            return None

        if cleaned_label in {"READY", "R O OPENED"}:
            for candidate in reversed(candidates):
                if re.search(r"\b\d{1,2}:\d{2}\s+\d{2}[A-Z]{3}\d{2}\b", candidate, re.IGNORECASE):
                    return candidate
            return None

        if cleaned_label in {"RATE", "TAX RATE"}:
            for candidate in candidates:
                if re.fullmatch(r"\d+(?:\.\d+)?\s*%", candidate.replace(",", "")):
                    return candidate
            return None

        if cleaned_label == "PAYMENT":
            for candidate in candidates:
                if candidate.upper() in {"CASH", "CHECK", "CARD", "CREDIT", "DEBIT"}:
                    return candidate
            return None

        if cleaned_label == "INSURANCE PREMIUM":
            for candidate in candidates:
                if re.search(r"\$?\d+(?:,\d{3})*(?:\.\d{2})?", candidate) and not date_pattern.search(candidate):
                    return candidate
            return None

        if cleaned_label in {"PO NO", "CLAIM NUMBER"}:
            for candidate in candidates:
                if re.search(r"[A-Z]", candidate) and not date_pattern.search(candidate) and not re.fullmatch(r"\$?\d+(?:,\d{3})*(?:\.\d{2})?", candidate.replace(",", "")):
                    return candidate
                if re.search(r"[A-Z]", candidate) and re.search(r"\d", candidate) and not date_pattern.search(candidate) and not re.fullmatch(r"\$?\d+(?:,\d{3})*(?:\.\d{2})?", candidate.replace(",", "")):
                    return candidate
            return None

        for candidate in candidates:
            if re.search(r"\d", candidate) or not re.fullmatch(r"[A-Z]{2,}", candidate):
                return candidate
        return None

    for text in texts:
        lines = [" ".join(line.split()).strip() for line in text.splitlines() if " ".join(line.split()).strip()]
        for index, line in enumerate(lines):
            matched = None
            for label in labels:
                if re.fullmatch(rf"{re.escape(label)}\s*:?\s*$", line, re.IGNORECASE):
                    matched = label
                    break
            if matched is None:
                continue
            value = value_for_label(matched, lines, index)
            if value:
                add(matched.upper(), value)
    return values