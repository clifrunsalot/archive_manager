"""Domain-neutral metadata models for grouped archive events."""

from dataclasses import asdict, dataclass, field
from typing import Any


SUPPORTED_EVENT_TYPES = {
    "automotive_service",
    "tax",
    "medical",
    "insurance",
    "investment",
    "retirement",
    "banking_statement",
    "general_document",
}


@dataclass(frozen=True)
class PageMetadata:
    """Ordering and provenance metadata for one uploaded page."""

    source_filename: str
    page_number: int | None = None
    page_count: int | None = None


@dataclass
class EventManifest:
    """A user- or tool-created grouping of documents belonging to one event."""

    event_id: str
    event_type: str = "general_document"
    subject_ref: str | None = None
    pages: list[PageMetadata] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if self.event_type not in SUPPORTED_EVENT_TYPES:
            raise ValueError(f"Unsupported event_type: {self.event_type}")
        self._validate_pages()

    def _validate_pages(self):
        filenames = [page.source_filename for page in self.pages]
        if len(filenames) != len(set(filenames)):
            raise ValueError("An event cannot contain duplicate source filenames")
        page_numbers = [page.page_number for page in self.pages if page.page_number is not None]
        if len(page_numbers) != len(set(page_numbers)):
            raise ValueError("An event cannot contain duplicate page numbers")
        page_counts = {page.page_count for page in self.pages if page.page_count is not None}
        if len(page_counts) > 1:
            raise ValueError("All pages in an event must agree on page_count")

    def page_for(self, source_filename: str) -> PageMetadata | None:
        """Return metadata for a source filename, if it belongs to this event."""
        return next(
            (page for page in self.pages if page.source_filename == source_filename),
            None,
        )

    def ordered_pages(self) -> list[PageMetadata]:
        """Return pages in known order, leaving unknown-order pages last."""
        return sorted(
            self.pages,
            key=lambda page: (
                page.page_number is None,
                page.page_number if page.page_number is not None else 0,
                page.source_filename.casefold(),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the manifest using only JSON-compatible values."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventManifest":
        """Build a manifest from a JSON-compatible dictionary."""
        pages = [PageMetadata(**page) for page in data.get("pages", [])]
        return cls(
            event_id=data["event_id"],
            event_type=data.get("event_type", "general_document"),
            subject_ref=data.get("subject_ref"),
            pages=pages,
            metadata=dict(data.get("metadata", {})),
        )