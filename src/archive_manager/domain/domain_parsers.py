"""Pluggable, conservative parsers for domain-specific event metadata."""

from dataclasses import dataclass, field
import re
from typing import Protocol


@dataclass(frozen=True)
class ExtractedField:
    """A field extracted from OCR text with its source page and confidence."""

    name: str
    value: str
    page: int | None = None
    confidence: float = 0.0


@dataclass
class ParseResult:
    """Domain parser output that remains safe when fields are missing."""

    domain: str
    fields: list[ExtractedField] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def field(self, name: str) -> ExtractedField | None:
        return next((field for field in self.fields if field.name == name), None)


class DomainParser(Protocol):
    domain: str

    def parse(self, pages: list[str]) -> ParseResult:
        """Parse page text without inventing values."""


LABEL_PATTERNS = {
    "tax": {
        "tax_year": r"(?:tax\s+year|year)\s*[:#]?\s*(20\d{2})",
        "form_type": r"\b(form\s+(?:1040|1040-SR|1099|W-2|Schedule\s+[A-Z]))\b",
        "total_income": r"(?:total\s+income|adjusted\s+gross\s+income|agi)\s*[:#]?\s*\$?([0-9,]+(?:\.\d{2})?)",
    },
    "insurance": {
        "policy_number": r"(?:policy|pol(?:icy)?\s*(?:no|number))\s*[:#]?\s*([A-Z0-9-]{4,})",
        "effective_date": r"(?:effective|coverage\s+starts?)\s*[:#]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        "expiration_date": r"(?:expiration|expires?|coverage\s+ends?)\s*[:#]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        "premium": r"(?:premium|policy\s+premium)\s*[:#]?\s*\$?([0-9,]+(?:\.\d{2})?)",
    },
    "investment": {
        "statement_date": r"(?:statement\s+date|as\s+of)\s*[:#]?\s*([A-Z0-9 ,/-]{6,})",
        "account_type": r"(?:account\s+type|type\s+of\s+account)\s*[:#]?\s*([A-Z0-9 /()-]+)",
        "account_value": r"(?:account\s+value|market\s+value|ending\s+value)\s*[:#]?\s*\$?([0-9,]+(?:\.\d{2})?)",
    },
    "retirement": {
        "account_type": r"(?:account\s+type|plan\s+type)\s*[:#]?\s*([A-Z0-9 /()-]+)",
        "statement_date": r"(?:statement\s+date|as\s+of)\s*[:#]?\s*([A-Z0-9 ,/-]{6,})",
        "account_value": r"(?:account\s+value|ending\s+balance|market\s+value)\s*[:#]?\s*\$?([0-9,]+(?:\.\d{2})?)",
    },
    "banking_statement": {
        "statement_period": r"(?:statement\s+period|period\s+ending)\s*[:#]?\s*([A-Z0-9 ,/-]{6,})",
        "account_type": r"(?:account\s+type|type)\s*[:#]?\s*([A-Z][A-Z0-9 /()-]{2,})",
        "ending_balance": r"(?:ending\s+balance|closing\s+balance|available\s+balance)\s*[:#]?\s*\$?([0-9,]+(?:\.\d{2})?)",
    },
    "medical": {
        "visit_date": r"(?:visit|encounter|service)\s+date\s*[:#]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        "provider": r"(?:provider|physician|clinician)\s*[:#]?\s*([A-Z][A-Z ,.'-]{2,})",
    },
}


class LabeledDomainParser:
    """Extract explicitly labeled non-transaction fields for one domain."""

    def __init__(self, domain: str):
        self.domain = domain

    def parse(self, pages: list[str]) -> ParseResult:
        result = ParseResult(self.domain)
        patterns = LABEL_PATTERNS[self.domain]
        for page_number, text in enumerate(pages, start=1):
            for field_name, pattern in patterns.items():
                match = re.search(pattern, text, re.IGNORECASE)
                if not match or result.field(field_name):
                    continue
                value = " ".join(match.group(1).split()).strip(" .,")
                if value:
                    result.fields.append(
                        ExtractedField(field_name, value, page_number, confidence=0.85)
                    )
        return result


class GeneralDocumentParser:
    """Fallback parser that returns no sensitive inferred fields."""

    domain = "general_document"

    def parse(self, pages: list[str]) -> ParseResult:
        return ParseResult(self.domain)


PARSER_REGISTRY: dict[str, DomainParser] = {
    domain: LabeledDomainParser(domain)
    for domain in LABEL_PATTERNS
}
PARSER_REGISTRY["general_document"] = GeneralDocumentParser()


def get_parser(domain: str) -> DomainParser:
    """Return the parser for a manifest domain, falling back safely."""
    return PARSER_REGISTRY.get(domain, PARSER_REGISTRY["general_document"])