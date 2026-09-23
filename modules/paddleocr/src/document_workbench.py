#!/usr/bin/env python3
"""Loopback-only document workbench for the bundled PaddleOCR gateway."""

from __future__ import annotations

import argparse
import base64
import json
import locale
import os
import shutil
import subprocess
import threading
import time
import uuid
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote


SUPPORTED_OPTIONS = {
    "useDocOrientationClassify",
    "useDocUnwarping",
    "useTextlineOrientation",
    "useSealRecognition",
    "useTableRecognition",
    "useFormulaRecognition",
    "useChartRecognition",
    "useRegionDetection",
    "formatBlockContent",
    "layoutThreshold",
    "layoutNms",
    "layoutUnclipRatio",
    "layoutMergeBboxesMode",
    "textDetLimitSideLen",
    "textDetLimitType",
    "textDetThresh",
    "textDetBoxThresh",
    "textDetUnclipRatio",
    "textRecScoreThresh",
    "markdownIgnoreLabels",
}
PDF_SUFFIX = ".pdf"
MAX_TREE_ENTRIES = 20_000
MAX_ASSET_BYTES = 512 << 20


def import_pymupdf():
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        import fitz

        return fitz


class MarkdownTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag.lower() == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            value = " ".join("".join(self._cell).split()).replace("|", "\\|")
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def html_table_to_markdown(value: str) -> str:
    parser = MarkdownTableParser()
    parser.feed(value)
    if not parser.rows:
        return value
    width = max(len(row) for row in parser.rows)
    rows = [row + [""] * (width - len(row)) for row in parser.rows]
    output = ["| " + " | ".join(rows[0]) + " |"]
    output.append("| " + " | ".join("---" for _ in range(width)) + " |")
    output.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(output)


def block_to_markdown(block: dict[str, Any]) -> str:
    content = str(block.get("block_content") or "").strip()
    if not content:
        return ""
    label = str(block.get("block_label") or "").lower()
    if "<table" in content.lower():
        return html_table_to_markdown(content)
    if content.startswith("#"):
        return content
    if label in {"doc_title", "document_title"}:
        return f"# {content}"
    if label in {"paragraph_title", "title", "section_title"}:
        return f"## {content}"
    return content


def normalize_asset_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if (
        not value.strip()
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise ValueError(f"Некорректный путь изображения Markdown: {value!r}")
    return path.as_posix()


def parse_ocr_bundle(value: str) -> tuple[list[str], list[str]]:
    pages: list[str] = []
    assets: list[str] = []
    for line in value.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        result = payload.get("result", {})
        markdown = result.get("markdown") or {}
        markdown_text = markdown.get("text")
        if isinstance(markdown_text, list):
            markdown_text = "\n\n".join(str(part) for part in markdown_text)
        if isinstance(markdown_text, str):
            pages.append(markdown_text.strip())
            for asset in markdown.get("assets") or []:
                normalized = normalize_asset_path(str(asset))
                if normalized not in assets:
                    assets.append(normalized)
            continue

        layouts = result.get("layoutParsingResults", [])
        blocks: list[dict[str, Any]] = []
        for layout in layouts:
            candidate = layout.get("prunedResult", {}).get("parsing_res_list", [])
            if isinstance(candidate, list):
                blocks.extend(item for item in candidate if isinstance(item, dict))
        parts = [block_to_markdown(block) for block in blocks]
        pages.append("\n\n".join(part for part in parts if part).strip())
    if not pages:
        raise RuntimeError("PaddleOCR returned an empty result")
    return pages, assets


def parse_ocr_jsonl(value: str) -> list[str]:
    pages, _ = parse_ocr_bundle(value)
    return pages


def join_markdown(pages: list[str]) -> str:
    return (
        "\n\n".join(
            f"<!-- page: {index} -->\n\n{page.strip()}"
            for index, page in enumerate(pages, start=1)
        ).rstrip()
        + "\n"
    )


class Workspace:
    def __init__(self) -> None:
        self._root: Path | None = None
        self._lock = threading.Lock()

    @property
    def root(self) -> Path | None:
        with self._lock:
            return self._root

    def select(self, value: str) -> Path:
        root = Path(value).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Выбранный путь не является каталогом")
        with self._lock:
            self._root = root
        return root

    def resolve(self, relative: str, *, pdf_only: bool = False) -> Path:
        root = self.root
        if root is None:
            raise ValueError("Сначала выберите рабочий каталог")
        candidate = (root / relative).resolve(strict=True)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Путь выходит за пределы рабочего каталога") from exc
        if not candidate.is_file():
            raise ValueError("Документ не найден")
        if pdf_only and candidate.suffix.lower() != PDF_SUFFIX:
            raise ValueError("Эта операция доступна только для PDF")
        return candidate


def build_tree(root: Path) -> list[dict[str, Any]]:
    count = 0

    def visit(folder: Path) -> list[dict[str, Any]]:
        nonlocal count
        result: list[dict[str, Any]] = []
        try:
            entries = sorted(
                folder.iterdir(),
                key=lambda item: (not item.is_dir(), item.name.casefold()),
            )
        except OSError:
            return result
        for item in entries:
            count += 1
            if count > MAX_TREE_ENTRIES:
                raise ValueError(
                    f"В каталоге больше {MAX_TREE_ENTRIES} элементов; выберите меньший каталог"
                )
            relative = item.relative_to(root).as_posix()
            if item.is_dir() and not item.is_symlink():
                result.append(
                    {
                        "name": item.name,
                        "path": relative,
                        "kind": "directory",
                        "children": visit(item),
                    }
                )
            elif item.is_file():
                result.append(
                    {
                        "name": item.name,
                        "path": relative,
                        "kind": "pdf" if item.suffix.lower() == PDF_SUFFIX else "file",
                    }
                )
        return result

    return visit(root)


class ResultStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.index_path = root / "index.json"
        self._lock = threading.Lock()
        root.mkdir(parents=True, exist_ok=True)
        try:
            self._index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._index: dict[str, str] = {}

    @staticmethod
    def _key(source: Path) -> str:
        value = str(source.resolve())
        return value.casefold() if os.name == "nt" else value

    def get(self, source: Path) -> dict[str, Any] | None:
        with self._lock:
            record_name = self._index.get(self._key(source))
            if not record_name:
                return None
            try:
                return json.loads((self.root / record_name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None

    def put(
        self,
        source: Path,
        pages: list[str],
        options: dict[str, Any],
        assets: dict[str, bytes] | None = None,
    ) -> dict[str, Any]:
        key = self._key(source)
        with self._lock:
            record_name = self._index.get(key) or f"{uuid.uuid4().hex}.json"
            existing: dict[str, Any] = {}
            try:
                existing = json.loads(
                    (self.root / record_name).read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                pass
            asset_records = existing.get("assets", [])
            if assets is not None:
                bucket = Path(record_name).stem
                asset_root = self.root / "assets" / bucket
                if asset_root.exists():
                    shutil.rmtree(asset_root)
                asset_records = []
                for relative, content in assets.items():
                    normalized = normalize_asset_path(relative)
                    destination = asset_root.joinpath(*PurePosixPath(normalized).parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(content)
                    asset_records.append(
                        {
                            "path": normalized,
                            "stored": destination.relative_to(self.root).as_posix(),
                        }
                    )
            record = {
                "source": str(source),
                "pages": pages,
                "options": options,
                "assets": asset_records,
                "updated": time.time(),
            }
            temporary = self.root / f"{record_name}.tmp"
            temporary.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.root / record_name)
            self._index[key] = record_name
            index_temporary = self.index_path.with_suffix(".json.tmp")
            index_temporary.write_text(
                json.dumps(self._index, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(index_temporary, self.index_path)
            return record

    def export_assets(self, source: Path, destination_root: Path) -> list[Path]:
        record = self.get(source) or {}
        exported: list[Path] = []
        for asset in record.get("assets") or []:
            normalized = normalize_asset_path(str(asset.get("path") or ""))
            stored = (self.root / str(asset.get("stored") or "")).resolve(strict=True)
            try:
                stored.relative_to(self.root.resolve())
            except ValueError as exc:
                raise ValueError("Путь сохранённого изображения повреждён") from exc
            destination = destination_root.joinpath(*PurePosixPath(normalized).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(
                f".{destination.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                shutil.copyfile(stored, temporary)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            exported.append(destination)
        return exported


def powershell_dialog(script: str) -> str | None:
    if os.name != "nt":
        raise RuntimeError("Стандартный системный диалог доступен только в Windows")
    executable = (
        Path(os.environ["SystemRoot"])
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [
            str(executable),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-STA",
            "-EncodedCommand",
            encoded,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
    )
    if completed.returncode:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        detail = completed.stderr.decode(encoding, errors="replace").strip()
        if not detail:
            detail = completed.stdout.decode(encoding, errors="replace").strip()
        raise RuntimeError(
            f"Системный диалог PowerShell завершился с кодом "
            f"{completed.returncode}: {detail or 'причина не сообщена'}"
        )
    output = completed.stdout.decode("ascii", errors="strict").strip().splitlines()
    if not output or not output[-1].strip():
        return None
    return base64.b64decode(output[-1].strip()).decode("utf-8")


def choose_directory() -> str | None:
    if os.name != "nt":
        raise RuntimeError("Стандартный системный диалог доступен только в Windows")

    import ctypes
    from ctypes import wintypes

    class BrowseInfo(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", ctypes.c_void_p),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BrowseInfo)]
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoUninitialize.argtypes = []

    initialized = ole32.CoInitializeEx(None, 0x2) >= 0
    display_name = ctypes.create_unicode_buffer(260)
    selected_path = ctypes.create_unicode_buffer(32_768)
    dialog = BrowseInfo(
        None,
        None,
        ctypes.cast(display_name, wintypes.LPWSTR),
        "Выберите рабочий каталог документов",
        0x0001 | 0x0040 | 0x0200,
        None,
        0,
        0,
    )
    item_id = None
    try:
        item_id = shell32.SHBrowseForFolderW(ctypes.byref(dialog))
        if not item_id:
            return None
        if not shell32.SHGetPathFromIDListW(
            item_id, ctypes.cast(selected_path, wintypes.LPWSTR)
        ):
            raise RuntimeError("Windows не вернула путь выбранного каталога")
        return selected_path.value
    finally:
        if item_id:
            ole32.CoTaskMemFree(item_id)
        if initialized:
            ole32.CoUninitialize()


def choose_save_file(initial: Path, filter_value: str) -> str | None:
    directory_b64 = base64.b64encode(str(initial.parent).encode("utf-8")).decode(
        "ascii"
    )
    name_b64 = base64.b64encode(initial.name.encode("utf-8")).decode("ascii")
    filter_b64 = base64.b64encode(filter_value.encode("utf-8")).decode("ascii")
    return powershell_dialog(
        "Add-Type -AssemblyName System.Windows.Forms;"
        f"$dir=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{directory_b64}'));"
        f"$name=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{name_b64}'));"
        f"$filter=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{filter_b64}'));"
        "$d=New-Object System.Windows.Forms.SaveFileDialog;"
        "$d.InitialDirectory=$dir;$d.FileName=$name;$d.Filter=$filter;$d.OverwritePrompt=$true;"
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){"
        "$b=[Text.Encoding]::UTF8.GetBytes($d.FileName);"
        "[Console]::Out.WriteLine([Convert]::ToBase64String($b))}"
    )


def embed_markdown(source: Path, destination: Path, markdown: str) -> None:
    pymupdf = import_pymupdf()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.{uuid.uuid4().hex}.tmp.pdf")
    attachment_name = f"{source.stem}.ocr.md"
    try:
        with pymupdf.open(source) as document:
            if attachment_name in document.embfile_names():
                document.embfile_del(attachment_name)
            document.embfile_add(
                attachment_name,
                markdown.encode("utf-8"),
                filename=attachment_name,
                ufilename=attachment_name,
                desc="PaddleOCR Markdown",
            )
            document.save(temporary, garbage=4, deflate=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


class OcrTasks:
    def __init__(self, gateway_url: str, token: str, store: ResultStore) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.token = token
        self.store = store
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def submit(self, source: Path, options: dict[str, Any]) -> str:
        task_id = uuid.uuid4().hex
        with self._lock:
            self._tasks[task_id] = {"state": "queued", "source": str(source)}
        threading.Thread(
            target=self._run,
            args=(task_id, source, options),
            daemon=True,
            name=f"ocr-ui-{task_id[:8]}",
        ).start()
        return task_id

    def status(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            return dict(self._tasks[task_id])

    def _set(self, task_id: str, **values: Any) -> None:
        with self._lock:
            self._tasks[task_id].update(values)

    def _run(self, task_id: str, source: Path, options: dict[str, Any]) -> None:
        import httpx

        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            self._set(task_id, state="submitting")
            with httpx.Client(timeout=httpx.Timeout(120.0, read=600.0)) as client:
                with source.open("rb") as stream:
                    response = client.post(
                        f"{self.gateway_url}/api/v2/ocr/jobs",
                        headers=headers,
                        data={
                            "model": "PP-StructureV3",
                            "optionalPayload": json.dumps(options),
                        },
                        files={"file": (source.name, stream, "application/pdf")},
                    )
                response.raise_for_status()
                job_id = response.json()["data"]["jobId"]
                self._set(task_id, state="running", gateway_job_id=job_id)
                deadline = time.monotonic() + 6 * 60 * 60
                while time.monotonic() < deadline:
                    status = client.get(
                        f"{self.gateway_url}/api/v2/ocr/jobs/{job_id}", headers=headers
                    )
                    status.raise_for_status()
                    data = status.json()["data"]
                    if data["state"] == "done":
                        result = client.get(
                            f"{self.gateway_url}/api/v2/ocr/jobs/{job_id}/result"
                        )
                        result.raise_for_status()
                        pages, asset_paths = parse_ocr_bundle(result.text)
                        assets: dict[str, bytes] = {}
                        total_asset_bytes = 0
                        for asset_path in asset_paths:
                            asset_response = client.get(
                                f"{self.gateway_url}/api/v2/ocr/jobs/{job_id}/assets/"
                                + quote(asset_path, safe="/")
                            )
                            asset_response.raise_for_status()
                            total_asset_bytes += len(asset_response.content)
                            if total_asset_bytes > MAX_ASSET_BYTES:
                                raise RuntimeError(
                                    "Изображения Markdown превышают локальный лимит 512 МБ"
                                )
                            assets[asset_path] = asset_response.content
                        record = self.store.put(source, pages, options, assets)
                        self._set(task_id, state="done", result=record)
                        return
                    if data["state"] == "failed":
                        raise RuntimeError(data.get("errorMsg") or "OCR job failed")
                    time.sleep(1)
                raise TimeoutError("OCR did not finish within six hours")
        except Exception as exc:
            self._set(task_id, state="failed", error=str(exc))


def create_app(
    workspace: Workspace,
    store: ResultStore,
    tasks: OcrTasks,
    html_path: Path,
):
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse, Response

    app = FastAPI(title="PaddleOCR document workbench")

    def failure(exc: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return html_path.read_text(encoding="utf-8")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ready",
            "workspace": str(workspace.root) if workspace.root else None,
        }

    @app.post("/api/workspace/dialog")
    def workspace_dialog() -> dict[str, Any]:
        try:
            selected = choose_directory()
            if selected is None:
                return {"cancelled": True}
            root = workspace.select(selected)
            return {"root": str(root), "tree": build_tree(root)}
        except Exception as exc:
            raise failure(exc) from exc

    @app.post("/api/workspace")
    def set_workspace(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            root = workspace.select(str(payload.get("path") or ""))
            return {"root": str(root), "tree": build_tree(root)}
        except Exception as exc:
            raise failure(exc) from exc

    @app.get("/api/tree")
    def tree() -> dict[str, Any]:
        root = workspace.root
        if root is None:
            return {"root": None, "tree": []}
        try:
            return {"root": str(root), "tree": build_tree(root)}
        except Exception as exc:
            raise failure(exc) from exc

    @app.get("/api/document")
    def document(path: str = Query(...)) -> dict[str, Any]:
        try:
            source = workspace.resolve(path, pdf_only=True)
            result = store.get(source)
            pymupdf = import_pymupdf()
            with pymupdf.open(source) as pdf:
                page_count = pdf.page_count
            return {
                "path": path,
                "name": source.name,
                "pageCount": page_count,
                "result": result,
            }
        except Exception as exc:
            raise failure(exc) from exc

    @app.get("/api/pdf/page/{page_number}")
    def pdf_page(page_number: int, path: str = Query(...)) -> Response:
        try:
            source = workspace.resolve(path, pdf_only=True)
            pymupdf = import_pymupdf()
            with pymupdf.open(source) as pdf:
                if page_number < 1 or page_number > pdf.page_count:
                    raise ValueError("Страница PDF не найдена")
                page = pdf.load_page(page_number - 1)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4), alpha=False)
                png = pixmap.tobytes("png")
            return Response(
                png,
                media_type="image/png",
                headers={"Cache-Control": "private, max-age=300"},
            )
        except Exception as exc:
            raise failure(exc) from exc

    @app.post("/api/ocr")
    def start_ocr(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = workspace.resolve(str(payload.get("path") or ""), pdf_only=True)
            raw_options = payload.get("options") or {}
            if not isinstance(raw_options, dict):
                raise ValueError("options должен быть JSON-объектом")
            unknown = set(raw_options) - SUPPORTED_OPTIONS
            if unknown:
                raise ValueError(f"Неподдерживаемые параметры: {sorted(unknown)}")
            if raw_options.get("useTableRecognition") is False:
                raise ValueError("Распознавание таблиц обязательно для этого профиля")
            return {"taskId": tasks.submit(source, raw_options)}
        except Exception as exc:
            raise failure(exc) from exc

    @app.get("/api/ocr/{task_id}")
    def ocr_status(task_id: str) -> dict[str, Any]:
        try:
            return tasks.status(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Задание не найдено") from exc

    @app.post("/api/save/markdown")
    def save_markdown(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = workspace.resolve(str(payload.get("path") or ""), pdf_only=True)
            pages = payload.get("pages")
            if not isinstance(pages, list) or not all(
                isinstance(page, str) for page in pages
            ):
                raise ValueError("Нет Markdown-текста для сохранения")
            markdown = join_markdown(pages)
            mode = payload.get("mode", "sidecar")
            initial = source.with_name(source.name + ".ocr.md")
            if mode == "dialog":
                chosen = choose_save_file(
                    initial, "Markdown (*.md)|*.md|Все файлы (*.*)|*.*"
                )
                if chosen is None:
                    return {"cancelled": True}
                destination = Path(chosen)
            elif mode == "sidecar":
                destination = initial
            else:
                raise ValueError("Неизвестный режим сохранения")
            destination.write_text(markdown, encoding="utf-8", newline="\n")
            exported = store.export_assets(source, destination.parent)
            store.put(source, pages, (store.get(source) or {}).get("options", {}))
            return {"path": str(destination), "exportedImages": len(exported)}
        except Exception as exc:
            raise failure(exc) from exc

    @app.post("/api/save/pdf")
    def save_pdf(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = workspace.resolve(str(payload.get("path") or ""), pdf_only=True)
            pages = payload.get("pages")
            if not isinstance(pages, list) or not all(
                isinstance(page, str) for page in pages
            ):
                raise ValueError("Нет Markdown-текста для встраивания")
            mode = payload.get("mode", "copy")
            if mode == "replace":
                destination = source
            elif mode == "copy":
                initial = source.with_name(source.stem + ".ocr.pdf")
                chosen = choose_save_file(initial, "PDF (*.pdf)|*.pdf")
                if chosen is None:
                    return {"cancelled": True}
                destination = Path(chosen)
            else:
                raise ValueError("Неизвестный режим сохранения PDF")
            embed_markdown(source, destination, join_markdown(pages))
            store.put(source, pages, (store.get(source) or {}).get("options", {}))
            return {"path": str(destination)}
        except Exception as exc:
            raise failure(exc) from exc

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9400)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:9399")
    parser.add_argument("--token", required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("The document workbench may bind only to 127.0.0.1")
    html_path = Path(__file__).with_name("document_workbench.html")
    if not html_path.is_file():
        raise SystemExit(f"Workbench HTML is missing: {html_path}")
    workspace = Workspace()
    store = ResultStore(args.data_root / "workbench")
    tasks = OcrTasks(args.gateway_url, args.token, store)
    app = create_app(workspace, store, tasks, html_path)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
