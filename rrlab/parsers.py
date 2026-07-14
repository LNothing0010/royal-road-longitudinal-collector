from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .config import SourceSpec
from .models import DetailSnapshot, FictionObservation, ReleaseObservation, SourceSnapshot

BASE = "https://www.royalroad.com"
FICTION_RE = re.compile(r"/fiction/(\d+)(?:/[^/?#]+)?")
CHAPTER_RE = re.compile(r"/chapter/(\d+)(?:/[^/?#]+)?")
NUMBER_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kKmMbB]?)")
DATE_RE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b", re.I)
ABSOLUTE_DATE_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"\d{1,2},\s+\d{4}(?:\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)?",
    re.I,
)
MARKETING_HOSTS = (
    "patreon.com", "ko-fi.com", "buymeacoffee.com", "discord.gg", "discord.com",
    "amazon.com", "amazon.co.uk", "backerkit.com", "subscribestar", "itch.io",
)


def parse_number(text: str | None) -> int | None:
    if not text:
        return None
    match = NUMBER_RE.search(text.replace("\u202f", " "))
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    factor = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[match.group(2).lower()]
    return int(value * factor)


def metric_from_text(text: str, labels: tuple[str, ...]) -> int | None:
    # Detail pages generally use "Label: value" while listing cards use "value Label".
    # Prefer label-first to avoid accidentally taking the preceding metric's value.
    for label in labels:
        escaped = re.escape(label)
        match = re.search(rf"\b{escaped}\s*[:\-]\s*([\d,.]+\s*[kKmMbB]?)", text, re.I)
        if match:
            return parse_number(match.group(1))
    for label in labels:
        escaped = re.escape(label)
        match = re.search(rf"([\d,.]+\s*[kKmMbB]?)\s*{escaped}\b", text, re.I)
        if match:
            return parse_number(match.group(1))
    return None


def float_metric_from_text(text: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        match = re.search(rf"\b{re.escape(label)}\s*[:\-]?\s*(\d+(?:\.\d+)?)", text, re.I)
        if match:
            return float(match.group(1))
    return None


def parse_datetime_text(text: str | None, base: datetime) -> tuple[datetime | None, str]:
    if not text:
        return None, "unknown"
    cleaned = " ".join(text.split()).strip()
    relative = re.search(
        r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>seconds?|minutes?|hours?|days?|weeks?|months?)\s+ago",
        cleaned,
        re.I,
    )
    if relative:
        value = float(relative.group("value"))
        unit = relative.group("unit").lower()
        if unit.startswith("second"):
            delta = timedelta(seconds=value)
        elif unit.startswith("minute"):
            delta = timedelta(minutes=value)
        elif unit.startswith("hour"):
            delta = timedelta(hours=value)
        elif unit.startswith("day"):
            delta = timedelta(days=value)
        elif unit.startswith("week"):
            delta = timedelta(weeks=value)
        else:
            delta = timedelta(days=value * 30)
        return (base - delta).astimezone(timezone.utc), "relative"

    iso_candidate = cleaned.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc), "absolute"
    except ValueError:
        pass

    formats = (
        "%A, %B %d, %Y %I:%M:%S %p",
        "%A, %B %d, %Y %I:%M %p",
        "%A, %B %d, %Y",
        "%B %d, %Y %I:%M:%S %p",
        "%B %d, %Y",
        "%b %d, %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc), "absolute"
        except ValueError:
            continue
    match = ABSOLUTE_DATE_RE.search(cleaned) or DATE_RE.search(cleaned)
    if match and match.group(0) != cleaned:
        return parse_datetime_text(match.group(0), base)
    return None, "unknown"


def _find_cards(soup: BeautifulSoup) -> list[Tag]:
    selectors = (
        ".fiction-list-item",
        ".fiction-list-item.row",
        "div[data-fiction-id]",
        "article.fiction-list-item",
    )
    for selector in selectors:
        cards = [card for card in soup.select(selector) if _fiction_anchor(card)]
        if cards:
            return cards

    # Fallback: climb from unique heading fiction links to a substantial ancestor.
    cards: list[Tag] = []
    seen: set[str] = set()
    for anchor in soup.select('h2 a[href*="/fiction/"], h3 a[href*="/fiction/"]'):
        match = FICTION_RE.search(anchor.get("href", ""))
        if not match or match.group(1) in seen:
            continue
        node: Tag | None = anchor
        chosen: Tag | None = None
        for _ in range(8):
            if not isinstance(node, Tag) or not isinstance(node.parent, Tag):
                break
            node = node.parent
            text = " ".join(node.get_text(" ", strip=True).split())
            fiction_links = {m.group(1) for a in node.select('a[href*="/fiction/"]') if (m := FICTION_RE.search(a.get("href", "")))}
            if 60 <= len(text) <= 12_000 and len(fiction_links) == 1:
                chosen = node
                if len(text) >= 150:
                    break
        if chosen:
            seen.add(match.group(1))
            cards.append(chosen)
    return cards


def _fiction_anchor(card: Tag) -> Tag | None:
    return card.select_one('h2 a[href*="/fiction/"], h3 a[href*="/fiction/"], a.font-red-sunglo[href*="/fiction/"], a[href*="/fiction/"]')


def _extract_taxonomy(card: Tag) -> tuple[list[str], list[str]]:
    genres: list[str] = []
    tags: list[str] = []
    seen: set[str] = set()
    for anchor in card.select("a"):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        if not text or text in seen:
            continue
        href = anchor.get("href", "").lower()
        classes = " ".join(anchor.get("class", [])).lower()
        if "genre=" in href or "/fictions/search" in href and "genre" in href:
            genres.append(text)
            seen.add(text)
        elif "tagsadd" in href or "tag" in classes or anchor.find_parent(class_=re.compile("tag", re.I)):
            tags.append(text)
            seen.add(text)
    # Royal Road frequently renders genres and tags identically. Keep a merged tag set too.
    if not genres and not tags:
        for anchor in card.select(".tags a, .fiction-tag, .label"):
            text = " ".join(anchor.get_text(" ", strip=True).split())
            if text and text not in seen:
                tags.append(text)
                seen.add(text)
    return genres, tags


def _extract_status(text: str) -> tuple[str | None, str | None]:
    fiction_type = None
    status = None
    for candidate in ("Original", "Fan Fiction", "Translated"):
        if re.search(rf"\b{re.escape(candidate)}\b", text, re.I):
            fiction_type = candidate
            break
    for candidate in ("ONGOING", "COMPLETED", "HIATUS", "STUB", "DROPPED"):
        if re.search(rf"\b{candidate}\b", text, re.I):
            status = candidate.lower()
            break
    return fiction_type, status


def _extract_release_links(card: Tag, fiction_id: str, source_name: str, observed: datetime) -> list[ReleaseObservation]:
    releases: list[ReleaseObservation] = []
    seen: set[str] = set()
    for anchor in card.select('a[href*="/chapter/"]'):
        href = anchor.get("href", "")
        chapter_match = CHAPTER_RE.search(href)
        chapter_id = chapter_match.group(1) if chapter_match else None
        key = chapter_id or href
        if not key or key in seen:
            continue
        seen.add(key)
        container = anchor.parent if isinstance(anchor.parent, Tag) else card
        nearby = " ".join(container.get_text(" ", strip=True).split())
        time_el = container.select_one("time") if isinstance(container, Tag) else None
        time_text = None
        if time_el:
            time_text = time_el.get("title") or time_el.get("datetime") or time_el.get_text(" ", strip=True)
        if not time_text:
            title = anchor.get_text(" ", strip=True)
            time_text = nearby.replace(title, "", 1).strip()
        published, precision = parse_datetime_text(time_text, observed)
        releases.append(ReleaseObservation(
            fiction_id=fiction_id,
            chapter_id=chapter_id,
            chapter_title=anchor.get_text(" ", strip=True),
            chapter_url=urljoin(BASE, href),
            published_utc=published,
            observed_utc=observed,
            source_name=source_name,
            date_precision=precision,
        ))
    return releases


def parse_listing_html(html: str, spec: SourceSpec, observed: datetime, http_status: int | None = None, fetch_seconds: float | None = None) -> SourceSnapshot:
    soup = BeautifulSoup(html, "lxml")
    cards = _find_cards(soup)
    observations: list[FictionObservation] = []
    releases: list[ReleaseObservation] = []
    seen: set[str] = set()

    for card in cards:
        anchor = _fiction_anchor(card)
        if not anchor:
            continue
        href = anchor.get("href", "")
        match = FICTION_RE.search(href)
        if not match or match.group(1) in seen:
            continue
        fiction_id = match.group(1)
        seen.add(fiction_id)
        text = " ".join(card.get_text(" ", strip=True).split())
        fiction_type, status = _extract_status(text)
        genres, tags = _extract_taxonomy(card)
        author_el = card.select_one('a[href*="/profile/"]')
        followers = metric_from_text(text, ("followers", "follower"))
        views = metric_from_text(text, ("total views", "views", "view"))
        pages = metric_from_text(text, ("pages", "page"))
        chapters = metric_from_text(text, ("chapters", "chapter"))
        date_match = DATE_RE.search(text)
        last_update, _ = parse_datetime_text(date_match.group(0), observed) if date_match else (None, "unknown")
        observation = FictionObservation(
            observed_utc=observed,
            source_name=spec.name,
            source_family=spec.family,
            rank=len(observations) + 1 if spec.is_ranked else None,
            fiction_id=fiction_id,
            title=" ".join(anchor.get_text(" ", strip=True).split()),
            url=urljoin(BASE, href),
            author=author_el.get_text(" ", strip=True) if author_el else None,
            fiction_type=fiction_type,
            status=status,
            followers=followers,
            total_views=views,
            page_count=pages,
            chapter_count=chapters,
            word_count_estimate=pages * 275 if pages is not None else None,
            word_count_source="estimated_from_pages_275" if pages is not None else None,
            last_update_utc=last_update,
            genres=genres,
            tags=tags,
        )
        observations.append(observation)
        releases.extend(_extract_release_links(card, fiction_id, spec.name, observed))

    complete: bool | None = None
    warnings: list[str] = []
    if spec.expected_count is not None:
        complete = len(observations) == spec.expected_count
        if not complete:
            warnings.append(f"expected={spec.expected_count}; observed={len(observations)}")
    if not observations:
        warnings.append("no fiction cards parsed")
    return SourceSnapshot(
        run_timestamp_utc=observed,
        source_name=spec.name,
        source_family=spec.family,
        source_url=spec.url,
        expected_count=spec.expected_count,
        observed_count=len(observations),
        complete=complete,
        observations=observations,
        releases=releases,
        warnings=warnings,
        http_status=http_status,
        fetch_seconds=fetch_seconds,
    )


def _meta_content(soup: BeautifulSoup, *selectors: str) -> str | None:
    for selector in selectors:
        el = soup.select_one(selector)
        if el and el.get("content"):
            return str(el.get("content")).strip()
    return None


def _extract_schedule(text: str) -> str | None:
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    candidates = [line for line in lines if re.search(r"\b(update|release|chapter).{0,80}(daily|weekly|mon|tue|wed|thu|fri|sat|sun|week|day)", line, re.I)]
    if not candidates:
        return None
    return " | ".join(candidates[:4])[:1000]


def _extract_marketing_urls(soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        if any(host in href.lower() for host in MARKETING_HOSTS):
            absolute = urljoin(BASE, href)
            if absolute not in urls:
                urls.append(absolute)
    return urls


def _extract_detail_taxonomy(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    # Restrict to the main fiction header area when possible.
    roots = soup.select(".fiction-info, .fic-header, .fiction-page, main") or [soup]
    genres: list[str] = []
    tags: list[str] = []
    seen: set[str] = set()
    for root in roots[:1]:
        for anchor in root.select("a"):
            text = " ".join(anchor.get_text(" ", strip=True).split())
            href = anchor.get("href", "").lower()
            if not text or text in seen:
                continue
            if "genre=" in href:
                genres.append(text)
                seen.add(text)
            elif "tagsadd" in href or "tag=" in href:
                tags.append(text)
                seen.add(text)
    return genres, tags


def _extract_chapter_releases(soup: BeautifulSoup, fiction_id: str, observed: datetime) -> list[ReleaseObservation]:
    releases: list[ReleaseObservation] = []
    seen: set[str] = set()
    for anchor in soup.select('a[href*="/chapter/"]'):
        href = anchor.get("href", "")
        match = CHAPTER_RE.search(href)
        if not match or match.group(1) in seen:
            continue
        chapter_id = match.group(1)
        seen.add(chapter_id)
        row = anchor.find_parent("tr") or anchor.parent
        time_el = row.select_one("time") if isinstance(row, Tag) else None
        date_text = None
        if time_el:
            unix_value = time_el.get("unixtime") or time_el.get("data-timestamp")
            if unix_value and str(unix_value).isdigit():
                published = datetime.fromtimestamp(int(unix_value), tz=timezone.utc)
                precision = "unix"
            else:
                date_text = time_el.get("title") or time_el.get("datetime") or time_el.get_text(" ", strip=True)
                published, precision = parse_datetime_text(date_text, observed)
        else:
            row_text = " ".join(row.get_text(" ", strip=True).split()) if isinstance(row, Tag) else ""
            date_match = ABSOLUTE_DATE_RE.search(row_text) or DATE_RE.search(row_text)
            date_text = date_match.group(0) if date_match else row_text.replace(anchor.get_text(" ", strip=True), "", 1)
            published, precision = parse_datetime_text(date_text, observed)
        releases.append(ReleaseObservation(
            fiction_id=fiction_id,
            chapter_id=chapter_id,
            chapter_title=" ".join(anchor.get_text(" ", strip=True).split()),
            chapter_url=urljoin(BASE, href),
            published_utc=published,
            observed_utc=observed,
            source_name="fiction_detail",
            date_precision=precision,
        ))
    releases.sort(key=lambda item: item.published_utc or datetime.min.replace(tzinfo=timezone.utc))
    return releases


def parse_detail_html(html: str, url: str, observed: datetime) -> DetailSnapshot:
    soup = BeautifulSoup(html, "lxml")
    canonical = _meta_content(soup, 'meta[property="og:url"]') or url
    fiction_match = FICTION_RE.search(canonical) or FICTION_RE.search(url)
    if not fiction_match:
        raise ValueError(f"Cannot identify fiction ID from {url}")
    fiction_id = fiction_match.group(1)
    title = _meta_content(soup, 'meta[property="og:title"]')
    if title:
        title = re.sub(r"\s*\|\s*Royal Road\s*$", "", title).strip()
    else:
        title_el = soup.select_one("h1")
        title = title_el.get_text(" ", strip=True) if title_el else f"fiction-{fiction_id}"
    author_el = soup.select_one('h1 ~ * a[href*="/profile/"], a[href*="/profile/"]')
    page_text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    compact = " ".join(page_text.split())
    fiction_type, status = _extract_status(compact)
    genres, tags = _extract_detail_taxonomy(soup)
    releases = _extract_chapter_releases(soup, fiction_id, observed)
    first_chapter = releases[0].published_utc if releases else None
    last_update = releases[-1].published_utc if releases else None
    pages = metric_from_text(compact, ("pages", "page"))
    chapters = metric_from_text(compact, ("chapters", "chapter")) or (len(releases) if releases else None)
    word_count = metric_from_text(compact, ("words", "word count"))
    blurb = _meta_content(soup, 'meta[property="og:description"]', 'meta[name="description"]')
    blurb_hash = hashlib.sha256(blurb.encode("utf-8")).hexdigest() if blurb else None
    warnings = [li.get_text(" ", strip=True) for li in soup.select(".fiction-warning li, .warning-list li")]
    cover_url = _meta_content(soup, 'meta[property="og:image"]')
    observation = FictionObservation(
        observed_utc=observed,
        source_name="fiction_detail",
        source_family="detail",
        fiction_id=fiction_id,
        title=title,
        url=canonical,
        author=author_el.get_text(" ", strip=True) if author_el else None,
        fiction_type=fiction_type,
        status=status,
        followers=metric_from_text(compact, ("followers", "follower")),
        total_views=metric_from_text(compact, ("total views",)),
        average_views=metric_from_text(compact, ("average views",)),
        favorites=metric_from_text(compact, ("favorites", "favorite")),
        page_count=pages,
        chapter_count=chapters,
        word_count=word_count,
        word_count_estimate=word_count if word_count is not None else (pages * 275 if pages is not None else None),
        word_count_source="royalroad_visible" if word_count is not None else ("estimated_from_pages_275" if pages is not None else None),
        rating_count=metric_from_text(compact, ("ratings", "rating")),
        rating_average=float_metric_from_text(compact, ("overall score", "rating average")),
        review_count=metric_from_text(compact, ("reviews", "review")),
        comment_count=metric_from_text(compact, ("comments", "comment")),
        first_chapter_utc=first_chapter,
        last_update_utc=last_update,
        genres=genres,
        tags=tags,
        cover_url=cover_url,
        blurb_text=blurb,
        blurb_hash=blurb_hash,
        schedule_text=_extract_schedule(page_text),
        marketing_urls=_extract_marketing_urls(soup),
        content_warnings=warnings,
    )
    snapshot_warnings: list[str] = []
    if not releases:
        snapshot_warnings.append("no chapter release rows parsed")
    return DetailSnapshot(run_timestamp_utc=observed, observation=observation, releases=releases, warnings=snapshot_warnings)
