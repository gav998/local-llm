#!/usr/bin/env python3
"""Submit dropped documents to the local PP-StructureV3 queue and export Markdown."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx


SUPPORTED_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".tif",
    ".tiff",
    ".bmp",
}
TERMINAL_STATES = {"done", "failed"}


def output_path_for(source: Path) -> Path:
    return source.with_suffix(".md")


def normalized_asset_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if (
        not value.strip()
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise ValueError(f"Некорректный путь ресурса в результате OCR: {value!r}")
    return path


def response_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return response.text.strip() or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("errorMsg")
        if detail:
            return str(detail)
    return response.text.strip() or f"HTTP {response.status_code}"


def require_success(response: httpx.Response, action: str) -> None:
    if response.is_error:
        raise RuntimeError(f"{action}: {response_detail(response)}")


def submit_job(client: httpx.Client, base_url: str, token: str, source: Path) -> str:
    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    with source.open("rb") as document:
        response = client.post(
            f"{base_url}/api/v2/ocr/jobs",
            headers={"Authorization": f"Bearer {token}"},
            data={"model": "PP-StructureV3", "optionalPayload": "{}"},
            files={"file": (source.name, document, media_type)},
        )
    require_success(response, f"Не удалось поставить {source.name} в очередь")
    try:
        job_id = response.json()["data"]["jobId"]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "PaddleOCR вернул некорректный идентификатор задачи"
        ) from exc
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError("PaddleOCR вернул пустой идентификатор задачи")
    return job_id


def wait_for_job(
    client: httpx.Client, base_url: str, token: str, job_id: str
) -> dict[str, Any]:
    shown_state = ""
    while True:
        response = client.get(
            f"{base_url}/api/v2/ocr/jobs/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        require_success(response, "Не удалось получить состояние OCR-задачи")
        try:
            state = response.json()["data"]
            state_name = state["state"]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "PaddleOCR вернул некорректное состояние задачи"
            ) from exc
        if state_name != shown_state:
            labels = {"queued": "в очереди", "running": "распознавание"}
            print(f"  {labels.get(state_name, state_name)}...", flush=True)
            shown_state = state_name
        if state_name in TERMINAL_STATES:
            if state_name == "failed":
                message = state.get("errorMsg") or "OCR завершился с ошибкой"
                raise RuntimeError(str(message))
            return state
        time.sleep(1)


def fetch_result_records(
    client: httpx.Client, base_url: str, job_id: str
) -> list[dict[str, Any]]:
    response = client.get(f"{base_url}/api/v2/ocr/jobs/{job_id}/result")
    require_success(response, "Не удалось получить результат OCR")
    records: list[dict[str, Any]] = []
    try:
        for line in response.text.splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("result line is not an object")
                records.append(value)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("PaddleOCR вернул повреждённый результат") from exc
    if not records:
        raise RuntimeError("PaddleOCR вернул пустой результат")
    return records


def fallback_markdown(result: dict[str, Any]) -> str:
    blocks: list[str] = []
    for layout in result.get("layoutParsingResults") or []:
        pruned = layout.get("prunedResult") or {}
        for block in pruned.get("parsing_res_list") or []:
            content = block.get("block_content")
            if content:
                blocks.append(str(content).strip())
    return "\n\n".join(blocks)


def export_markdown(
    client: httpx.Client,
    base_url: str,
    records: list[dict[str, Any]],
    output: Path,
    job_id: str,
) -> None:
    asset_root = output.with_name(output.stem + "_assets")
    asset_prefix = asset_root.name
    pages: list[str] = []
    downloaded: set[str] = set()

    for record in records:
        result = record.get("result") or {}
        markdown = result.get("markdown") or {}
        text = markdown.get("text")
        if not isinstance(text, str):
            text = fallback_markdown(result)
        for raw_asset in markdown.get("assets") or []:
            asset = normalized_asset_path(str(raw_asset))
            asset_key = asset.as_posix()
            local_reference = quote(f"{asset_prefix}/{asset_key}", safe="/")
            text = text.replace(asset_key, local_reference)
            if asset_key in downloaded:
                continue
            encoded = quote(asset_key, safe="/")
            response = client.get(
                f"{base_url}/api/v2/ocr/jobs/{job_id}/assets/{encoded}"
            )
            require_success(response, f"Не удалось получить ресурс {asset_key}")
            destination = asset_root.joinpath(*asset.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_asset = destination.with_name(destination.name + ".tmp")
            try:
                temporary_asset.write_bytes(response.content)
                temporary_asset.replace(destination)
            finally:
                temporary_asset.unlink(missing_ok=True)
            downloaded.add(asset_key)
        pages.append(text.strip())

    rendered = "\n\n".join(page for page in pages if page).rstrip() + "\n"
    temporary = output.with_name(output.name + ".tmp")
    try:
        temporary.write_text(rendered, encoding="utf-8", newline="\n")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def recognize_document(
    client: httpx.Client, base_url: str, token: str, source: Path
) -> Path:
    source = source.expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"Это не файл: {source}")
    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        formats = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(
            "Неподдерживаемый формат "
            f"{source.suffix or '(без расширения)'}; нужны {formats}"
        )

    output = output_path_for(source)
    print(f"[OCR] {source}", flush=True)
    job_id = submit_job(client, base_url, token, source)
    print(f"  задача {job_id[:12]} поставлена в очередь", flush=True)
    wait_for_job(client, base_url, token, job_id)
    records = fetch_result_records(client, base_url, job_id)
    export_markdown(client, base_url, records, output, job_id)
    print(f"[ГОТОВО] {output}", flush=True)
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("documents", nargs="+")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base_url = args.url.rstrip("/")
    timeout = httpx.Timeout(120, connect=15)
    try:
        with httpx.Client(timeout=timeout) as client:
            for document in args.documents:
                recognize_document(client, base_url, args.token, Path(document))
    except KeyboardInterrupt:
        print("OCR прерван пользователем.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
