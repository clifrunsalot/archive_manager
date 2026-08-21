"""Typed query intent planning for deterministic and generative handlers."""

from dataclasses import dataclass
import re
from typing import Literal


QueryIntent = Literal[
    "source_inventory",
    "service_date_inventory",
    "service_advisor_inventory",
    "repair_cause_inventory",
    "label_values_inventory",
    "total_charges",
    "total_charges_inventory",
    "performed_services",
    "performed_services_inventory",
    "not_performed_services_inventory",
    "multi_event_summary",
    "broad_scope",
    "free_text",
]

# Recognized non-text renderings for inventory-style answers. "markdown_table" is the
# default for generic table/column requests; "ascii_table" is only used when a question
# explicitly asks for plain ASCII. flowchart/sequence_diagram/component_diagram render
# Mermaid diagrams.
OUTPUT_FORMATS = ("text", "markdown_table", "ascii_table", "flowchart", "sequence_diagram", "component_diagram")


@dataclass(frozen=True)
class QueryPlan:
    """The normalized scope and handler selected for one user question."""

    intent: QueryIntent
    requires_llm: bool
    date_text: str | None = None
    include_total_cost: bool = False
    include_grand_total: bool = False
    output_format: str = "text"
    sort_by_cost: bool = False
    cost_first: bool = False
    scope: str = "query"
    group_by: str | None = None
    sort_direction: str | None = None
    requested_fields: tuple[str, ...] = ()


DATE_PATTERN = (
    r"\b\d{1,2}\s+[a-z]{3,9}\s+20\d{2}\b"
    r"|\b[a-z]{3,9}\s+\d{1,2},?\s+20\d{2}\b"
    r"|\b\d{2}[a-z]{3}\d{2}\b"
    r"|\b\d{1,2}[/-]\d{1,2}[/-]20\d{2}\b"
    r"|\b20\d{2}[/-]\d{1,2}[/-]\d{1,2}\b"
)


def _normalized(question: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", question.lower()).strip()


def _output_format_from_question(normalized: str) -> str:
    """Detect a requested table/diagram rendering, shared across inventory intents."""
    if re.search(r"\bsequence\s+diagram\b", normalized):
        return "sequence_diagram"
    if re.search(r"\bcomponent\s+diagram\b", normalized):
        return "component_diagram"
    if re.search(r"\bflow\s*chart\b|\bprocess\s+(?:diagram|flow)\b", normalized):
        return "flowchart"
    if re.search(r"\bascii\s+table\b|copy\s+into\s+another\s+text\s+file", normalized):
        return "ascii_table"
    if re.search(r"\btables?\b|\bcolumns?\b", normalized):
        return "markdown_table"
    return "text"


def _wants_grand_total(normalized: str) -> bool:
    """Detect a request to sum per-date totals into one combined figure."""
    return bool(
        re.search(
            r"\badd(?:ed)?\s+(?:them|it)?\s*up\b|\bcombined\b|\bgrand\s+total\b|\bsum(?:med)?\b|\bfor\s+all\b",
            normalized,
        )
    )


def plan_query(question: str) -> QueryPlan:
    """Classify a question once, prioritizing precise deterministic intents."""
    normalized = _normalized(question)
    date_match = re.search(DATE_PATTERN, question, re.IGNORECASE)
    has_date = date_match is not None

    if re.search(r"\b(not\s+performed|declined|recommended)\b", normalized) and re.search(
        r"\b(service|services|repair|repairs|work|files?|events?)\b", normalized
    ):
        return QueryPlan(
            "not_performed_services_inventory",
            False,
            output_format=_output_format_from_question(normalized),
            sort_by_cost=bool(re.search(r"\border(?:ed)?\s+by\s+cost|cost.*lowest|lowest.*cost", normalized)),
            scope="all_events",
            group_by="service_date",
            sort_direction="ascending",
            requested_fields=("services_not_performed",),
        )
    if re.search(r"\b(service\s+advisor|advisor|supervisor)\b", normalized) and re.search(
        r"\b(each|every|all|date|dates|service)\b", normalized
    ):
        return QueryPlan("service_advisor_inventory", False, output_format=_output_format_from_question(normalized), scope="all_events", group_by="service_date", requested_fields=("service_advisor",))
    if re.search(r"\b(cause|causes|caused|reason|reasons)\b", normalized) and re.search(
        r"\b(repair|repairs|service|services|problem|problems|work)\b", normalized
    ):
        return QueryPlan("repair_cause_inventory", False, date_text=date_match.group(0) if has_date else None, output_format=_output_format_from_question(normalized), scope="all_events", group_by="service_date", requested_fields=("repair", "cause"))
    if re.search(r"\b(display|show|state|list|identify|extract|get|find|return|capture)\b", normalized) and re.search(
        r"\blabels?\b", normalized
    ) and re.search(r"\b(values?|associated)\b", normalized):
        return QueryPlan(
            "label_values_inventory",
            False,
            date_text=date_match.group(0) if has_date else None,
            scope="all_events" if not has_date else "matching_date",
            group_by="event",
            requested_fields=tuple(re.findall(r"\b(?:promised|ready|invoice|payment|delivery|rate|po|date)\b", normalized)),
        )
    if re.search(r"\b(file|files|document|documents)\b", normalized) and re.search(
        r"\b(name|names|list|listing|processed|indexed)\b", normalized
    ):
        return QueryPlan("source_inventory", False, scope="matching_sources", requested_fields=("source_filename",))
    if has_date and re.search(r"\b(total charges?|total cost|amount due|balance due)\b", normalized):
        return QueryPlan("total_charges", False, date_match.group(0))
    total_language = r"\b(total\s+(?:charges?|costs?)|charges?|costs?|amounts?\s+due|balances?\s+due)\b"
    if re.search(r"\b(each|every|all|oldest|recent|chronological|by\s+date)\b", normalized) and re.search(
        r"\b(service|services|repair|repairs|work|events?)\b", normalized
    ) and re.search(r"\b(performed|actually|completed)\b", normalized):
        return QueryPlan(
            "performed_services_inventory",
            False,
            include_total_cost=bool(re.search(r"\b(total\s+)?costs?|total\s+charges?\b", normalized)),
            output_format=_output_format_from_question(normalized),
            sort_by_cost=bool(re.search(r"\border(?:ed)?\s+by\s+cost|cost.*lowest|lowest.*cost", normalized)),
            cost_first=bool(re.search(r"cost\s+in\s+the\s+left(?:\s+most|most)?\s+column|left(?:\s+most|most)\s+column.*cost", normalized)),
        )
    if re.search(r"\b(each|every|all)\b", normalized) and re.search(
        r"\b(summarize|summary|service records?|documents?|files?)\b", normalized
    ):
        return QueryPlan("multi_event_summary", True, scope="all_events", group_by="event")
    if re.search(total_language, normalized) and re.search(
        r"\b(per|by|from\s+oldest|oldest|recent|chronological|events?|service\s+date)\b",
        normalized,
    ) and re.search(r"\b(car|service|repair|invoice|archive|record(?:s|ed)?)\b", normalized):
        return QueryPlan(
            "total_charges_inventory",
            False,
            output_format=_output_format_from_question(normalized),
            include_grand_total=_wants_grand_total(normalized),
        )
    if re.search(r"\b(total charges?|total cost|amount due|balance due)\b", normalized) and re.search(
        r"\b(each|every|all)\b", normalized
    ) and re.search(r"\b(service|repair|record(?:s|ed)?|invoice|archive)\b", normalized):
        return QueryPlan(
            "total_charges_inventory",
            False,
            output_format=_output_format_from_question(normalized),
            include_grand_total=_wants_grand_total(normalized),
        )
    if has_date and re.search(r"\b(repair|repairs|service|services|performed|work)\b", normalized):
        return QueryPlan("performed_services", False, date_match.group(0))
    if re.search(r"\b(service|repair|maintenance|car)\b", normalized) and re.search(
        r"\b(date|dates|when)\b", normalized
    ) and re.search(r"\b(saved|stored|archive|records?|documents?|service)\b", normalized):
        return QueryPlan("service_date_inventory", False)
    if normalized in {
        "tell me about the archive",
        "what does the archive say",
        "summarize the archive",
        "summarize everything",
        "summarize all documents",
        "what is in the archive",
    }:
        return QueryPlan("broad_scope", False)
    return QueryPlan("free_text", True)
