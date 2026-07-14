from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import DetailSnapshot, FictionObservation, ReleaseObservation, SourceSnapshot

SCHEMA_VERSION = 2

SCHEMA = r"""
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp_utc TEXT NOT NULL UNIQUE,
  collector_version TEXT NOT NULL,
  started_utc TEXT NOT NULL,
  completed_utc TEXT,
  status TEXT NOT NULL,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS source_snapshot (
  run_id INTEGER NOT NULL,
  source_name TEXT NOT NULL,
  source_family TEXT NOT NULL,
  source_url TEXT NOT NULL,
  expected_count INTEGER,
  observed_count INTEGER NOT NULL,
  complete INTEGER,
  http_status INTEGER,
  fetch_seconds REAL,
  raw_json_path TEXT,
  raw_html_path TEXT,
  warnings_json TEXT NOT NULL,
  PRIMARY KEY(run_id, source_name),
  FOREIGN KEY(run_id) REFERENCES run(run_id)
);
CREATE TABLE IF NOT EXISTS fiction (
  fiction_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  author TEXT,
  first_seen_utc TEXT NOT NULL,
  last_seen_utc TEXT NOT NULL,
  first_seen_source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listing_membership (
  run_id INTEGER NOT NULL,
  source_name TEXT NOT NULL,
  fiction_id TEXT NOT NULL,
  rank INTEGER,
  PRIMARY KEY(run_id, source_name, fiction_id),
  UNIQUE(run_id, source_name, rank),
  FOREIGN KEY(run_id) REFERENCES run(run_id),
  FOREIGN KEY(fiction_id) REFERENCES fiction(fiction_id)
);
CREATE TABLE IF NOT EXISTS metric_observation (
  run_id INTEGER NOT NULL,
  source_name TEXT NOT NULL,
  fiction_id TEXT NOT NULL,
  followers INTEGER,
  total_views INTEGER,
  average_views REAL,
  favorites INTEGER,
  page_count INTEGER,
  chapter_count INTEGER,
  word_count INTEGER,
  word_count_estimate INTEGER,
  word_count_source TEXT,
  rating_count INTEGER,
  rating_average REAL,
  review_count INTEGER,
  comment_count INTEGER,
  first_chapter_utc TEXT,
  last_update_utc TEXT,
  PRIMARY KEY(run_id, source_name, fiction_id),
  FOREIGN KEY(run_id) REFERENCES run(run_id),
  FOREIGN KEY(fiction_id) REFERENCES fiction(fiction_id)
);
CREATE TABLE IF NOT EXISTS metadata_observation (
  run_id INTEGER NOT NULL,
  source_name TEXT NOT NULL,
  fiction_id TEXT NOT NULL,
  fiction_type TEXT,
  status TEXT,
  genres_json TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  content_warnings_json TEXT NOT NULL,
  cover_url TEXT,
  blurb_hash TEXT,
  blurb_text TEXT,
  schedule_text TEXT,
  marketing_urls_json TEXT NOT NULL,
  PRIMARY KEY(run_id, source_name, fiction_id),
  FOREIGN KEY(run_id) REFERENCES run(run_id),
  FOREIGN KEY(fiction_id) REFERENCES fiction(fiction_id)
);
CREATE TABLE IF NOT EXISTS release_event (
  fiction_id TEXT NOT NULL,
  chapter_key TEXT NOT NULL,
  chapter_id TEXT,
  chapter_title TEXT NOT NULL,
  chapter_url TEXT,
  published_utc TEXT,
  first_observed_utc TEXT NOT NULL,
  last_observed_utc TEXT NOT NULL,
  source_name TEXT NOT NULL,
  date_precision TEXT NOT NULL,
  PRIMARY KEY(fiction_id, chapter_key),
  FOREIGN KEY(fiction_id) REFERENCES fiction(fiction_id)
);
CREATE TABLE IF NOT EXISTS list_transition (
  run_id INTEGER NOT NULL,
  source_name TEXT NOT NULL,
  fiction_id TEXT NOT NULL,
  transition_type TEXT NOT NULL,
  prior_rank INTEGER,
  current_rank INTEGER,
  rank_delta INTEGER,
  PRIMARY KEY(run_id, source_name, fiction_id, transition_type)
);
CREATE TABLE IF NOT EXISTS list_cutoff (
  run_id INTEGER NOT NULL,
  source_name TEXT NOT NULL,
  cutoff_rank INTEGER NOT NULL,
  fiction_id TEXT,
  followers INTEGER,
  total_views INTEGER,
  page_count INTEGER,
  chapter_count INTEGER,
  PRIMARY KEY(run_id, source_name)
);
CREATE TABLE IF NOT EXISTS metric_delta (
  run_id INTEGER NOT NULL,
  fiction_id TEXT NOT NULL,
  horizon_hours INTEGER NOT NULL,
  prior_run_id INTEGER,
  elapsed_hours REAL,
  follower_delta INTEGER,
  view_delta INTEGER,
  favorite_delta INTEGER,
  rating_count_delta INTEGER,
  chapter_delta INTEGER,
  page_delta INTEGER,
  PRIMARY KEY(run_id, fiction_id, horizon_hours)
);
CREATE TABLE IF NOT EXISTS intervention_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  fiction_id TEXT NOT NULL,
  event_utc TEXT,
  event_type TEXT NOT NULL,
  evidence_url TEXT,
  confidence TEXT NOT NULL,
  notes TEXT,
  created_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_registry (
  version TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL,
  status TEXT NOT NULL,
  parameters_json TEXT NOT NULL,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS methodology_change (
  change_id INTEGER PRIMARY KEY AUTOINCREMENT,
  changed_utc TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  component TEXT NOT NULL,
  description TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_membership_history ON listing_membership(fiction_id, source_name, run_id);
CREATE INDEX IF NOT EXISTS idx_metric_history ON metric_observation(fiction_id, run_id);
CREATE INDEX IF NOT EXISTS idx_release_fiction_date ON release_event(fiction_id, published_utc);
"""


def utc_text(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


class Storage:
    def __init__(self, db_path: Path, raw_dir: Path):
        self.db_path = db_path
        self.raw_dir = raw_dir
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
            count = conn.execute("SELECT COUNT(*) FROM methodology_change").fetchone()[0]
            if count == 0:
                conn.execute(
                    "INSERT INTO methodology_change(changed_utc,schema_version,component,description) VALUES(?,?,?,?)",
                    (utc_text(datetime.now(timezone.utc)), SCHEMA_VERSION, "initial_schema", "Longitudinal panel schema v2 created"),
                )
            conn.commit()

    def begin_run(self, timestamp: datetime, version: str) -> int:
        self.init()
        ts = utc_text(timestamp)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO run(timestamp_utc,collector_version,started_utc,status) VALUES(?,?,?,?)",
                (ts, version, utc_text(datetime.now(timezone.utc)), "running"),
            )
            conn.commit()
            return int(conn.execute("SELECT run_id FROM run WHERE timestamp_utc=?", (ts,)).fetchone()[0])

    def finish_run(self, run_id: int, status: str = "complete", notes: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE run SET completed_utc=?,status=?,notes=? WHERE run_id=?",
                (utc_text(datetime.now(timezone.utc)), status, notes, run_id),
            )
            conn.commit()

    def _raw_paths(self, timestamp: datetime, source_name: str) -> tuple[Path, Path]:
        date_dir = self.raw_dir / timestamp.strftime("%Y/%m/%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}_{source_name}"
        return date_dir / f"{stem}.json.gz", date_dir / f"{stem}.html.gz"

    def _upsert_fiction(self, conn: sqlite3.Connection, observation: FictionObservation) -> None:
        ts = utc_text(observation.observed_utc)
        conn.execute(
            """
            INSERT INTO fiction(fiction_id,title,url,author,first_seen_utc,last_seen_utc,first_seen_source)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(fiction_id) DO UPDATE SET
              title=excluded.title,
              url=excluded.url,
              author=COALESCE(excluded.author,fiction.author),
              last_seen_utc=excluded.last_seen_utc
            """,
            (observation.fiction_id, observation.title, observation.url, observation.author, ts, ts, observation.source_name),
        )

    def _persist_observation(self, conn: sqlite3.Connection, run_id: int, observation: FictionObservation) -> None:
        self._upsert_fiction(conn, observation)
        if observation.rank is not None:
            conn.execute(
                "INSERT OR REPLACE INTO listing_membership(run_id,source_name,fiction_id,rank) VALUES(?,?,?,?)",
                (run_id, observation.source_name, observation.fiction_id, observation.rank),
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO metric_observation(
              run_id,source_name,fiction_id,followers,total_views,average_views,favorites,page_count,
              chapter_count,word_count,word_count_estimate,word_count_source,rating_count,rating_average,
              review_count,comment_count,first_chapter_utc,last_update_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, observation.source_name, observation.fiction_id, observation.followers,
                observation.total_views, observation.average_views, observation.favorites,
                observation.page_count, observation.chapter_count, observation.word_count,
                observation.word_count_estimate, observation.word_count_source, observation.rating_count,
                observation.rating_average, observation.review_count, observation.comment_count,
                utc_text(observation.first_chapter_utc), utc_text(observation.last_update_utc),
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO metadata_observation(
              run_id,source_name,fiction_id,fiction_type,status,genres_json,tags_json,
              content_warnings_json,cover_url,blurb_hash,blurb_text,schedule_text,marketing_urls_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, observation.source_name, observation.fiction_id, observation.fiction_type,
                observation.status, json.dumps(observation.genres, ensure_ascii=False),
                json.dumps(observation.tags, ensure_ascii=False),
                json.dumps(observation.content_warnings, ensure_ascii=False), observation.cover_url,
                observation.blurb_hash, observation.blurb_text, observation.schedule_text,
                json.dumps(observation.marketing_urls, ensure_ascii=False),
            ),
        )

    def _persist_release(self, conn: sqlite3.Connection, release: ReleaseObservation) -> None:
        chapter_key = release.chapter_id or hashlib_key(release.chapter_title, release.chapter_url, utc_text(release.published_utc))
        conn.execute(
            """
            INSERT INTO release_event(
              fiction_id,chapter_key,chapter_id,chapter_title,chapter_url,published_utc,
              first_observed_utc,last_observed_utc,source_name,date_precision
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fiction_id,chapter_key) DO UPDATE SET
              chapter_title=excluded.chapter_title,
              chapter_url=COALESCE(excluded.chapter_url,release_event.chapter_url),
              published_utc=COALESCE(excluded.published_utc,release_event.published_utc),
              last_observed_utc=excluded.last_observed_utc,
              date_precision=CASE WHEN release_event.date_precision='unknown' THEN excluded.date_precision ELSE release_event.date_precision END
            """,
            (
                release.fiction_id, chapter_key, release.chapter_id, release.chapter_title,
                release.chapter_url, utc_text(release.published_utc), utc_text(release.observed_utc),
                utc_text(release.observed_utc), release.source_name, release.date_precision,
            ),
        )

    def persist_source(self, run_id: int, snapshot: SourceSnapshot, raw_html: str | None = None) -> Path:
        raw_json_path, raw_html_path = self._raw_paths(snapshot.run_timestamp_utc, snapshot.source_name)
        payload = snapshot.model_dump_json(indent=2)
        with gzip.open(raw_json_path, "wt", encoding="utf-8") as handle:
            handle.write(payload)
        html_saved: str | None = None
        if raw_html is not None:
            with gzip.open(raw_html_path, "wt", encoding="utf-8") as handle:
                handle.write(raw_html)
            html_saved = str(raw_html_path)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO source_snapshot(
                  run_id,source_name,source_family,source_url,expected_count,observed_count,complete,
                  http_status,fetch_seconds,raw_json_path,raw_html_path,warnings_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id, snapshot.source_name, snapshot.source_family, snapshot.source_url,
                    snapshot.expected_count, snapshot.observed_count,
                    None if snapshot.complete is None else int(snapshot.complete), snapshot.http_status,
                    snapshot.fetch_seconds, str(raw_json_path), html_saved,
                    json.dumps(snapshot.warnings, ensure_ascii=False),
                ),
            )
            for observation in snapshot.observations:
                self._persist_observation(conn, run_id, observation)
            for release in snapshot.releases:
                self._persist_release(conn, release)
            conn.commit()
        return raw_json_path

    def persist_detail(self, run_id: int, detail: DetailSnapshot, raw_html: str | None = None) -> Path:
        source_name = f"detail_{detail.observation.fiction_id}"
        raw_json_path, raw_html_path = self._raw_paths(detail.run_timestamp_utc, source_name)
        with gzip.open(raw_json_path, "wt", encoding="utf-8") as handle:
            handle.write(detail.model_dump_json(indent=2))
        if raw_html is not None:
            with gzip.open(raw_html_path, "wt", encoding="utf-8") as handle:
                handle.write(raw_html)
        with self.connect() as conn:
            self._persist_observation(conn, run_id, detail.observation)
            for release in detail.releases:
                self._persist_release(conn, release)
            conn.commit()
        return raw_json_path

    def detail_candidates(self, run_id: int, limit: int, refresh_hours: int, new_hours: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                WITH current_rs AS (
                  SELECT DISTINCT fiction_id FROM listing_membership lm
                  JOIN source_snapshot ss ON ss.run_id=lm.run_id AND ss.source_name=lm.source_name
                  WHERE lm.run_id=? AND ss.source_family='rising_stars'
                ),
                current_new AS (
                  SELECT DISTINCT fiction_id FROM listing_membership WHERE run_id=? AND source_name='newest'
                ),
                last_detail AS (
                  SELECT fiction_id, MAX(r.timestamp_utc) AS last_detail_utc
                  FROM metric_observation mo JOIN run r USING(run_id)
                  WHERE mo.source_name='fiction_detail' GROUP BY fiction_id
                ),
                ever_rs AS (
                  SELECT DISTINCT lm.fiction_id FROM listing_membership lm
                  JOIN source_snapshot ss ON ss.run_id=lm.run_id AND ss.source_name=lm.source_name
                  WHERE ss.source_family='rising_stars'
                )
                SELECT f.fiction_id,f.url,f.first_seen_utc,ld.last_detail_utc,
                       CASE
                         WHEN cr.fiction_id IS NOT NULL THEN 100
                         WHEN cn.fiction_id IS NOT NULL THEN 90
                         WHEN er.fiction_id IS NOT NULL THEN 75
                         ELSE 40
                       END AS priority
                FROM fiction f
                LEFT JOIN current_rs cr USING(fiction_id)
                LEFT JOIN current_new cn USING(fiction_id)
                LEFT JOIN ever_rs er USING(fiction_id)
                LEFT JOIN last_detail ld USING(fiction_id)
                WHERE ld.last_detail_utc IS NULL
                   OR (cr.fiction_id IS NOT NULL AND julianday('now')-julianday(ld.last_detail_utc) >= ?/24.0)
                   OR (cn.fiction_id IS NOT NULL AND julianday('now')-julianday(ld.last_detail_utc) >= ?/24.0)
                   OR (cr.fiction_id IS NULL AND cn.fiction_id IS NULL AND julianday('now')-julianday(ld.last_detail_utc) >= 1.0)
                ORDER BY priority DESC, COALESCE(ld.last_detail_utc,'') ASC, f.first_seen_utc DESC
                LIMIT ?
                """,
                (run_id, run_id, refresh_hours, new_hours, limit),
            ).fetchall()
            return [dict(row) for row in rows]


def hashlib_key(*parts: str | None) -> str:
    import hashlib
    return hashlib.sha256("|".join(part or "" for part in parts).encode("utf-8")).hexdigest()[:24]
