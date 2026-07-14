from __future__ import annotations

import json

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install MCP dependencies with: pip install -e '.[mcp]'") from exc

from .collector import run_collection
from .config import Settings
from .exporter import export_archive
from .queries import fiction_history, latest_source, new_entrants

mcp = FastMCP("Royal Road Longitudinal Lab")
settings = Settings()


@mcp.tool()
def collect_snapshot(enrich_details: bool = True) -> str:
    """Collect all Royal Road panel sources and persist a single coherent UTC run."""
    return json.dumps(run_collection(settings, enrich_details), indent=2)


@mcp.tool()
def get_latest_source(source_name: str) -> str:
    """Return the latest stored ranking or discovery page for one configured source."""
    return json.dumps(latest_source(settings.db_path, source_name), indent=2)


@mcp.tool()
def get_new_entrants(source_name: str) -> str:
    """Return fiction IDs present now but absent from the prior source snapshot."""
    return json.dumps(new_entrants(settings.db_path, source_name), indent=2)


@mcp.tool()
def get_fiction_history(fiction_id: str, limit: int = 500) -> str:
    """Return longitudinal public metric observations for one fiction."""
    return json.dumps(fiction_history(settings.db_path, fiction_id, limit), indent=2)


@mcp.tool()
def export_archive_tool() -> str:
    """Create a ZIP containing SQLite, raw snapshots, CSV tables and validation reports."""
    return str(export_archive(settings))


if __name__ == "__main__":
    mcp.run()
