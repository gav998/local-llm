from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import office
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

SERVER_NAME = "portable-office-tools"
SERVER_CONFIG = Path(__file__).resolve().with_name("server.json")
DEFAULT_SERVER_CONFIG: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8766,
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
        raise ValueError("Office MCP must listen on a loopback host")
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
        }
    )


@mcp.tool()
def inspect_word(path: str, max_items: int = 200) -> dict[str, Any]:
    """Read paragraphs and tables from a DOCX file without changing it."""
    return office.inspect_docx(Path(path), max_items)


@mcp.tool()
def inspect_excel(
    path: str,
    sheet: str | None = None,
    cell_range: str | None = None,
    max_rows: int = 100,
    max_columns: int = 30,
    data_only: bool = False,
) -> dict[str, Any]:
    """Read worksheets, cell values, and formulas from an XLSX or XLSM file."""
    return office.inspect_xlsx(
        Path(path), sheet, cell_range, max_rows, max_columns, data_only
    )


@mcp.tool()
def replace_word_text(
    input_path: str,
    output_path: str,
    old: str,
    new: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Replace DOCX text and verify a separately saved result by default."""
    return office.replace_docx(
        Path(input_path), Path(output_path), old, new, overwrite
    )


@mcp.tool()
def set_excel_cells(
    input_path: str,
    output_path: str,
    sheet: str,
    assignments: dict[str, Any],
    overwrite: bool = False,
) -> dict[str, Any]:
    """Set XLSX/XLSM cells and verify a separately saved result by default."""
    return office.set_xlsx_values(
        Path(input_path), Path(output_path), sheet, assignments, overwrite
    )


@mcp.tool()
def create_word(
    output_path: str,
    title: str | None = None,
    paragraphs: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create and verify a simple DOCX document."""
    return office.new_docx(Path(output_path), title, paragraphs or [], overwrite)


@mcp.tool()
def create_excel(
    output_path: str,
    sheet: str = "Sheet1",
    rows: list[list[Any]] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create and verify a simple XLSX workbook from arrays of cell values."""
    return office.new_xlsx_values(Path(output_path), sheet, rows or [], overwrite)


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
                },
                ensure_ascii=False,
            )
        )
        return 0
    print(
        f"[Office MCP] Starting Streamable HTTP at {server_url(config)}. "
        f"Health: http://{config['host']}:{config['port']}/health.",
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
        print("[Office MCP] Stopped.", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
