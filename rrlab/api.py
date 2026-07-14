from __future__ import annotations

try:
    from fastapi import FastAPI, HTTPException
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install API dependencies with: pip install -e '.[api]'") from exc

from .collector import collect
from .config import SOURCE_MAP, Settings
from .exporter import export_archive
from .queries import fiction_history, latest_source, new_entrants

app = FastAPI(title="Royal Road Longitudinal Lab", version="0.3.0")
settings = Settings()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "sources": list(SOURCE_MAP)}


@app.post("/collect")
async def collect_now(enrich_details: bool = True) -> dict:
    return await collect(settings, enrich_details)


@app.get("/latest/{source_name}")
def latest(source_name: str) -> list[dict]:
    if source_name not in SOURCE_MAP:
        raise HTTPException(404, "unknown source")
    return latest_source(settings.db_path, source_name)


@app.get("/entrants/{source_name}")
def entrants(source_name: str) -> list[dict]:
    if source_name not in SOURCE_MAP:
        raise HTTPException(404, "unknown source")
    return new_entrants(settings.db_path, source_name)


@app.get("/fiction/{fiction_id}/history")
def history(fiction_id: str, limit: int = 500) -> list[dict]:
    return fiction_history(settings.db_path, fiction_id, limit)


@app.post("/export")
def export() -> dict:
    return {"path": str(export_archive(settings))}
