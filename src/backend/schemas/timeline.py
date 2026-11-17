"""Timeline items: reviewed events paired with dates, places, and people, each cited."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .extraction import CitationReference

DateStatus = Literal["resolved", "needs_anchor", "ambiguous", "unparsed", "none"]
Precision = Literal["day", "week", "month", "quarter", "year"]
Role = Literal["event", "date", "location", "participant"]
ReviewStatus = Literal["suggested", "confirmed", "rejected"]


class MentionRef(BaseModel):
    """An accepted mention that supports a timeline item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    mention_id: UUID
    role: Role
    label: str
    text: str
    citation: CitationReference


class TimelineItemDraft(BaseModel):
    """Computed from accepted mentions; stored as a suggestion until a person reviews it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    document_id: UUID
    title: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    # Exact slice of the preserved region text, with offsets into that region.
    sentence_text: str
    sentence_start: int = Field(ge=0)
    sentence_end: int = Field(gt=0)
    date_text: str | None = None
    date_status: DateStatus
    start_date: date | None = None
    end_date: date | None = None
    precision: Precision | None = None
    flags: tuple[str, ...] = ()
    anchor_date: date | None = None
    refs: tuple[MentionRef, ...]

    @model_validator(mode="after")
    def validate_shape(self):
        if len(self.sentence_text) != self.sentence_end - self.sentence_start:
            raise ValueError("Sentence length must match its offsets")
        if (self.date_status == "resolved") != (self.start_date is not None):
            raise ValueError("Only resolved dates carry a start date")
        if (self.start_date is None) != (self.end_date is None) or (
                self.start_date is not None and self.end_date < self.start_date):
            raise ValueError("Date ranges must be ordered")
        if self.start_date is not None and self.precision is None:
            raise ValueError("Resolved dates need a precision")
        if sum(ref.role == "event" for ref in self.refs) != 1:
            raise ValueError("Each item has exactly one event")
        return self


class TimelineBuild(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[TimelineItemDraft, ...]
    # Accepted dates that no event shares a sentence with: counted, never invented into events.
    unattached_dates: int = 0


class TimelineItem(BaseModel):
    """Stored item, including a reviewer's decision and any corrections."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: UUID
    document_id: UUID
    title: str
    page_number: int
    sentence_text: str
    sentence_start: int
    sentence_end: int
    date_text: str | None = None
    date_status: DateStatus
    start_date: date | None = None
    end_date: date | None = None
    precision: Precision | None = None
    flags: list[str] = []
    anchor_date: date | None = None
    refs: list[MentionRef] = []
    review_status: ReviewStatus
    corrected_title: str | None = None
    corrected_start: date | None = None
    corrected_end: date | None = None
    corrected_precision: Precision | None = None
    stale: bool = False
    reviewed_at: datetime | None = None
    document_title: str | None = None

    @property
    def effective_start(self) -> date | None:
        return self.corrected_start or self.start_date


class TimelinePage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[TimelineItem]
    undated: int = 0


class TimelineReview(BaseModel):
    """Confirm or reject an item, optionally correcting its title or date."""

    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus
    title: str | None = Field(default=None, min_length=1, max_length=300)
    start: date | None = None
    end: date | None = None
    precision: Precision | None = None

    @model_validator(mode="after")
    def validate_correction(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("Give both a start and an end date, or neither")
        if self.start is not None:
            if self.end < self.start:
                raise ValueError("The end date cannot precede the start date")
            if self.precision is None:
                raise ValueError("A corrected date needs a precision")
        if self.status != "confirmed" and (self.title or self.start):
            raise ValueError("Only a confirmed item can be corrected")
        return self


class BuildResult(BaseModel):
    documents: int
    items: int
    undated: int
    unattached_dates: int
