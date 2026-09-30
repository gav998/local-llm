from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from gsm_gost import GostGsmPipeline
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

SERVER_NAME = "gost-gsm-helper"
SERVER_CONFIG = Path(__file__).resolve().with_name("server.json")
DEFAULT_SERVER_CONFIG: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8765,
    "path": "/mcp",
}
mcp = MCPServer(SERVER_NAME)


def server_config() -> dict[str, Any]:
    configured: dict[str, Any] = {}
    if SERVER_CONFIG.is_file():
        loaded = json.loads(SERVER_CONFIG.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded, dict):
            raise ValueError("server.json must contain a JSON object")
        configured = loaded
    result = {**DEFAULT_SERVER_CONFIG, **configured}
    host = str(result["host"])
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("GOST GSM MCP must listen on a loopback host")
    port = int(result["port"])
    if not 1 <= port <= 65535:
        raise ValueError("server port must be between 1 and 65535")
    path = str(result["path"])
    if not path.startswith("/") or path == "/":
        raise ValueError("server path must start with '/' and must not be root")
    return {"host": host, "port": port, "path": path.rstrip("/")}


def server_url(config: dict[str, Any]) -> str:
    host = f"[{config['host']}]" if ":" in config["host"] else config["host"]
    return f"http://{host}:{config['port']}{config['path']}"


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    config = server_config()
    return JSONResponse(
        {
            "status": "ok",
            "server": SERVER_NAME,
            "transport": "streamable-http",
            "mcp_url": server_url(config),
            "default_project_root": str(project(None).root),
        }
    )


def project(project_root: str | None) -> GostGsmPipeline:
    configured = project_root or os.environ.get("GOST_GSM_PROJECT_ROOT")
    if not configured:
        configured = str(Path(__file__).resolve().parent / "jobs" / "default")
    return GostGsmPipeline(configured)


@mcp.tool()
def scan_source(source_path: str, project_root: str | None = None) -> dict[str, Any]:
    """Inspect OCR HTML/Markdown tables without creating or changing a job."""
    return project(project_root).scan_source(source_path)


@mcp.tool()
def initialize_project(
    source_path: str,
    project_root: str | None = None,
    force: bool = False,
    brand_table_ids: list[str] | None = None,
    purpose_table_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Create manifests for a job. force=true replaces only generated job artifacts."""
    return project(project_root).initialize(
        source_path=source_path,
        force=force,
        brand_table_ids=brand_table_ids,
        purpose_table_ids=purpose_table_ids,
    )


@mcp.tool()
def get_progress(project_root: str | None = None) -> dict[str, Any]:
    """Return exact row/cell progress and current review-flag counts."""
    return project(project_root).progress()


@mcp.tool()
def get_next_batch(
    project_root: str | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Preview the next contiguous batch without processing it."""
    return project(project_root).next_batch(batch_size)


@mcp.tool()
def process_next_batch(
    project_root: str | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Parse, link, match purpose, stage, and QC the next batch."""
    return project(project_root).process_next_batch(batch_size)


@mcp.tool()
def process_batches(
    project_root: str | None = None,
    batch_size: int | None = None,
    max_batches: int = 1,
    stop_on_flags: bool = False,
) -> dict[str, Any]:
    """Process several sequential batches, stopping on failure and optionally on flags."""
    return project(project_root).process_batches(batch_size, max_batches, stop_on_flags)


@mcp.tool()
def list_review_flags(
    project_root: str | None = None,
    limit: int = 50,
    categories: list[str] | None = None,
    row_id: str | None = None,
) -> dict[str, Any]:
    """List traceable warnings and unresolved mappings."""
    return project(project_root).list_review_flags(limit, categories, row_id)


@mcp.tool()
def get_row_detail(row_id: str, project_root: str | None = None) -> dict[str, Any]:
    """Show source, parsed items, matches, relations, overrides, and flags for one row."""
    return project(project_root).row_detail(row_id)


@mcp.tool()
def find_purpose_candidates(
    project_root: str | None = None,
    item_id: str | None = None,
    brand: str | None = None,
    normative_document: str | None = None,
    group: str | None = None,
    subgroup: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Rank source purpose rows for an item or an explicitly supplied brand."""
    return project(project_root).find_purpose_candidates(
        item_id=item_id,
        brand=brand,
        normative_document=normative_document,
        group=group,
        subgroup=subgroup,
        limit=limit,
    )


@mcp.tool()
def set_item_override(
    batch_id: str,
    item_id: str,
    project_root: str | None = None,
    brand: str | None = None,
    normative_document: str | None = None,
    clear_normative_document: bool = False,
    source_ref: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Correct a reviewed parsed brand/ND while retaining raw OCR and provenance."""
    return project(project_root).set_item_override(
        batch_id=batch_id,
        item_id=item_id,
        brand=brand,
        normative_document=normative_document,
        clear_normative_document=clear_normative_document,
        source_ref=source_ref,
        note=note,
    )


@mcp.tool()
def set_purpose_override(
    batch_id: str,
    item_id: str,
    project_root: str | None = None,
    purpose_row_id: str | None = None,
    purpose_text: str | None = None,
    source_ref: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Apply a reviewed purpose value, then re-stage and re-run QC."""
    return project(project_root).set_purpose_override(
        batch_id=batch_id,
        item_id=item_id,
        purpose_row_id=purpose_row_id,
        purpose_text=purpose_text,
        source_ref=source_ref,
        note=note,
    )


@mcp.tool()
def add_relation_override(
    batch_id: str,
    main_item_id: str,
    related_item_id: str,
    relation_type: str,
    project_root: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Add a reviewed same-row relation, then re-stage and re-run QC."""
    return project(project_root).add_relation_override(
        batch_id=batch_id,
        main_item_id=main_item_id,
        related_item_id=related_item_id,
        relation_type=relation_type,
        note=note,
    )


@mcp.tool()
def clear_override(
    batch_id: str,
    project_root: str | None = None,
    item_id: str | None = None,
    main_item_id: str | None = None,
    related_item_id: str | None = None,
    relation_type: str | None = None,
    clear_item_value: bool = False,
) -> dict[str, Any]:
    """Remove a purpose or matching relation override and re-run staging/QC."""
    return project(project_root).clear_override(
        batch_id=batch_id,
        item_id=item_id,
        main_item_id=main_item_id,
        related_item_id=related_item_id,
        relation_type=relation_type,
        clear_item_value=clear_item_value,
    )


@mcp.tool()
def validate_final(
    project_root: str | None = None,
    allow_partial: bool = False,
) -> dict[str, Any]:
    """Check completeness, unresolved ambiguity, and conflicting final values."""
    return project(project_root).validate_final(allow_partial)


@mcp.tool()
def export_final_csv(
    project_root: str | None = None,
    allow_partial: bool = False,
    allow_blockers: bool = False,
) -> dict[str, Any]:
    """Write the three semicolon-delimited UTF-8 CSV files after final validation."""
    return project(project_root).export_final_csv(allow_partial, allow_blockers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--path")
    args = parser.parse_args(argv)
    config = server_config()
    if args.host is not None:
        config["host"] = args.host
    if args.port is not None:
        config["port"] = args.port
    if args.path is not None:
        config["path"] = args.path
    # Reuse validation for command-line overrides without writing configuration.
    host = str(config["host"])
    port = int(config["port"])
    path = str(config["path"])
    if host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("--host must be a loopback host")
    if not 1 <= port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not path.startswith("/") or path == "/":
        parser.error("--path must start with '/' and must not be root")
    config = {"host": host, "port": port, "path": path.rstrip("/")}
    if args.self_check:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "server": SERVER_NAME,
                    "transport": "streamable-http",
                    "mcp_url": server_url(config),
                    "default_project_root": str(project(None).root),
                },
                ensure_ascii=False,
            )
        )
        return 0
    print(
        f"[GOST GSM MCP] Starting Streamable HTTP at {server_url(config)}. "
        f"Health: http://{config['host']}:{config['port']}/health. "
        f"Job directory: {project(None).root}",
        file=sys.stderr,
        flush=True,
    )
    try:
        mcp.run(
            "streamable-http",
            host=config["host"],
            port=config["port"],
            streamable_http_path=config["path"],
        )
    except KeyboardInterrupt:
        print("[GOST GSM MCP] Stopped.", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
