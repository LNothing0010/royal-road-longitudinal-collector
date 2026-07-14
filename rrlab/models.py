from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class ReleaseObservation(BaseModel):
    fiction_id: str
    chapter_id: str | None = None
    chapter_title: str
    chapter_url: str | None = None
    published_utc: datetime | None = None
    observed_utc: datetime
    source_name: str
    date_precision: str = "unknown"


class FictionObservation(BaseModel):
    observed_utc: datetime
    source_name: str
    source_family: str
    rank: int | None = Field(default=None, ge=1)
    fiction_id: str
    title: str
    url: str
    author: str | None = None
    fiction_type: str | None = None
    status: str | None = None
    followers: int | None = None
    total_views: int | None = None
    average_views: float | None = None
    favorites: int | None = None
    page_count: int | None = None
    chapter_count: int | None = None
    word_count: int | None = None
    word_count_estimate: int | None = None
    word_count_source: str | None = None
    rating_count: int | None = None
    rating_average: float | None = None
    review_count: int | None = None
    comment_count: int | None = None
    first_chapter_utc: datetime | None = None
    last_update_utc: datetime | None = None
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cover_url: str | None = None
    blurb_text: str | None = None
    blurb_hash: str | None = None
    schedule_text: str | None = None
    marketing_urls: list[str] = Field(default_factory=list)
    content_warnings: list[str] = Field(default_factory=list)


class SourceSnapshot(BaseModel):
    run_timestamp_utc: datetime
    source_name: str
    source_family: str
    source_url: str
    expected_count: int | None = None
    observed_count: int
    complete: bool | None = None
    observations: list[FictionObservation] = Field(default_factory=list)
    releases: list[ReleaseObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    http_status: int | None = None
    fetch_seconds: float | None = None


class DetailSnapshot(BaseModel):
    run_timestamp_utc: datetime
    observation: FictionObservation
    releases: list[ReleaseObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
