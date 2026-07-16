from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SourceSpec:
    name: str
    url: str
    family: str
    expected_count: int | None = None
    is_ranked: bool = True
    is_rs: bool = False


HOME_URL = "https://www.royalroad.com/home"

SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("rs_main", "https://www.royalroad.com/fictions/rising-stars", "rising_stars", 50, True, True),
    SourceSpec("rs_fantasy", "https://www.royalroad.com/fictions/rising-stars?genre=fantasy", "rising_stars", 50, True, True),
    SourceSpec("rs_action", "https://www.royalroad.com/fictions/rising-stars?genre=action", "rising_stars", 50, True, True),
    SourceSpec("rs_adventure", "https://www.royalroad.com/fictions/rising-stars?genre=adventure", "rising_stars", 50, True, True),
    SourceSpec("rs_drama", "https://www.royalroad.com/fictions/rising-stars?genre=drama", "rising_stars", 50, True, True),
    SourceSpec("rs_psychological", "https://www.royalroad.com/fictions/rising-stars?genre=psychological", "rising_stars", 50, True, True),
    SourceSpec("newest", "https://www.royalroad.com/fictions/new", "discovery", None, True, False),
    SourceSpec("latest_updates", "https://www.royalroad.com/fictions/latest-updates", "discovery", None, True, False),
    SourceSpec("weekly_popular", "https://www.royalroad.com/fictions/weekly-popular", "comparison", None, True, False),
    SourceSpec("active_popular", "https://www.royalroad.com/fictions/active-popular", "comparison", None, True, False),
)

EXPOSURE_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        "latest_updates_live",
        "https://www.royalroad.com/fictions/latest-updates",
        "organic_exposure",
        20,
        True,
        False,
    ),
    SourceSpec(
        "newest_live",
        "https://www.royalroad.com/fictions/new",
        "organic_exposure",
        20,
        True,
        False,
    ),
)

HOME_EXPOSURE_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("home_latest_updates", HOME_URL, "organic_exposure", None, True, False),
    SourceSpec("home_rising_stars", HOME_URL, "organic_exposure", None, True, False),
)

CATALOG_BACKFILL_SOURCE = SourceSpec(
    "catalog_backfill",
    "https://www.royalroad.com/fictions/new",
    "catalog",
    None,
    True,
    False,
)
SOURCE_MAP = {
    source.name: source
    for source in (
        *SOURCES,
        *EXPOSURE_SOURCES,
        *HOME_EXPOSURE_SOURCES,
        CATALOG_BACKFILL_SOURCE,
    )
}
RS_SOURCES = tuple(source.name for source in SOURCES if source.is_rs)
DEFAULT_USER_AGENT = (
    "RoyalRoadLongitudinalLab/0.4.0 "
    "(non-commercial public-page research; github.com/LNothing0010)"
)


def _env_text(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


def _env_float(name: str, default: float, minimum: float) -> float:
    raw = os.getenv(name)
    value = default if raw in (None, "") else float(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    value = default if raw in (None, "") else int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of 1/0, true/false, yes/no, on/off")


@dataclass(frozen=True)
class Settings:
    # default_factory is deliberate: environment values are read when Settings is
    # instantiated, not once at module import time.
    user_agent: str = field(default_factory=lambda: _env_text("RR_USER_AGENT", DEFAULT_USER_AGENT))
    min_delay_seconds: float = field(
        default_factory=lambda: _env_float("RR_MIN_DELAY_SECONDS", 2.5, 1.0)
    )
    timeout_seconds: float = field(
        default_factory=lambda: _env_float("RR_TIMEOUT_SECONDS", 45.0, 5.0)
    )
    db_path: Path = field(default_factory=lambda: Path(_env_text("RR_DB_PATH", "data/rrlab.sqlite")))
    raw_dir: Path = field(default_factory=lambda: Path(_env_text("RR_RAW_DIR", "data/raw")))
    report_dir: Path = field(default_factory=lambda: Path(_env_text("RR_REPORT_DIR", "reports")))
    detail_limit_per_run: int = field(
        default_factory=lambda: _env_int("RR_DETAIL_LIMIT_PER_RUN", 40, 0)
    )
    detail_refresh_hours: int = field(
        default_factory=lambda: _env_int("RR_DETAIL_REFRESH_HOURS", 12, 1)
    )
    new_fiction_detail_hours: int = field(
        default_factory=lambda: _env_int("RR_NEW_FICTION_DETAIL_HOURS", 3, 1)
    )
    newest_max_pages: int = field(
        default_factory=lambda: _env_int("RR_NEWEST_MAX_PAGES", 25, 2)
    )
    frontier_overlap_pages: int = field(
        default_factory=lambda: _env_int("RR_FRONTIER_OVERLAP_PAGES", 1, 0)
    )
    frontier_anchor_limit: int = field(
        default_factory=lambda: _env_int("RR_FRONTIER_ANCHOR_LIMIT", 100, 20)
    )
    catalog_state_path: Path = field(
        default_factory=lambda: Path(_env_text("RR_CATALOG_STATE_PATH", "data/catalog_state.json"))
    )
    backfill_pages_per_run: int = field(
        default_factory=lambda: _env_int("RR_BACKFILL_PAGES_PER_RUN", 75, 1)
    )
    backfill_overlap_pages: int = field(
        default_factory=lambda: _env_int("RR_BACKFILL_OVERLAP_PAGES", 3, 0)
    )
    save_raw_html: bool = field(default_factory=lambda: _env_bool("RR_SAVE_RAW_HTML"))
    browser_fallback: bool = field(
        default_factory=lambda: _env_bool("RR_USE_BROWSER_FALLBACK")
    )
