#!/usr/bin/env python3
"""Portable loopback-only structured PDF digitizer backed by PaddleOCR and llama.cpp."""

from __future__ import annotations

import argparse
import base64
import json
import locale
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

PDF_SUFFIX = ".pdf"
MAX_PDF_BYTES = 1 << 30
DEFAULT_PROMPT = (
    "Исправь только очевидные ошибки OCR в русском тексте, убери переносы слов "
    "между строками и лишние пробелы. Не добавляй факты. Верни только исправленный текст."
)
REGION_KINDS = {"field", "table_header", "table_rows", "group_value"}
OBJECT_KINDS = {"field", "table"}


def import_pymupdf():
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        import fitz

        return fitz


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def clean_text(value: str) -> str:
    value = re.sub(r"```(?:text)?\s*|```", "", value, flags=re.I)
    return "\n".join(line.rstrip() for line in value.strip().splitlines()).strip()


def flatten_ocr_result(value: str) -> str:
    parts: list[str] = []
    for line in value.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        result = payload.get("result") or {}
        markdown = result.get("markdown") or {}
        text = markdown.get("text")
        if isinstance(text, list):
            text = "\n".join(str(item) for item in text)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
            continue
        for layout in result.get("layoutParsingResults") or []:
            blocks = (layout.get("prunedResult") or {}).get("parsing_res_list") or []
            for block in blocks:
                content = block.get("block_content") if isinstance(block, dict) else None
                if content:
                    parts.append(str(content).strip())
    if not parts:
        raise RuntimeError("PaddleOCR вернул пустой результат")
    return clean_text("\n".join(parts))


def normalized_cuts(values: Any) -> list[float]:
    cuts = [0.0, 1.0]
    if isinstance(values, list):
        for value in values:
            number = float(value)
            if 0.0 < number < 1.0:
                cuts.append(round(number, 6))
    return sorted(set(cuts))


def validate_rect(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("У области отсутствует прямоугольник")
    rect = {key: float(value.get(key, -1)) for key in ("x", "y", "w", "h")}
    if (
        rect["x"] < 0
        or rect["y"] < 0
        or rect["w"] <= 0
        or rect["h"] <= 0
        or rect["x"] + rect["w"] > 1.000001
        or rect["y"] + rect["h"] > 1.000001
    ):
        raise ValueError("Координаты области должны находиться внутри страницы")
    return rect


def sidecar_path(source: Path) -> Path:
    return source.with_name(source.name + ".digitizer.json")


def template_path(source: Path) -> Path:
    return source.with_name("digitizer.templates.json")


def new_document(source: Path, page_count: int) -> dict[str, Any]:
    now = time.time()
    return {
        "schema": 1,
        "source": str(source),
        "pageCount": page_count,
        "templateId": None,
        "objects": [],
        "data": {},
        "created": now,
        "updated": now,
    }


def materialize(document: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for obj in document.get("objects") or []:
        key = str(obj.get("key") or obj.get("name") or obj.get("id") or "field")
        if obj.get("type") == "field":
            data[key] = str((obj.get("result") or {}).get("value") or "")
            continue
        result = obj.get("result") or {}
        headers = [str(item) for item in result.get("columns") or []]
        rows = [list(row) for row in result.get("rows") or [] if isinstance(row, list)]
        width = max([len(headers), *(len(row) for row in rows)], default=0)
        if len(headers) < width:
            headers.extend(f"Столбец {index + 1}" for index in range(len(headers), width))
        normalized_rows: list[dict[str, str]] = []
        for row in rows:
            row.extend([""] * (width - len(row)))
            normalized_rows.append(
                {headers[index]: str(row[index]) for index in range(width)}
            )
        data[key] = {"columns": headers, "rows": normalized_rows}
    document["data"] = data
    document["updated"] = time.time()
    return document


def rebuild_table_result(obj: dict[str, Any]) -> None:
    """Serialize all recognized table fragments in page/markup order."""
    indexed = list(enumerate(obj.get("regions") or []))
    regions = [
        region
        for _index, region in sorted(
            indexed, key=lambda item: (int(item[1].get("page", 0)), item[0])
        )
        if region.get("status") in {"recognized", "modified"}
    ]
    headers: list[str] = []
    group_columns: list[str] = []
    for region in regions:
        if region.get("kind") == "table_header" and region.get("output"):
            headers = [str(value) for value in region["output"][0]]
        elif region.get("kind") == "group_value":
            name = str((region.get("properties") or {}).get("column") or "Группа")
            if name not in group_columns:
                group_columns.append(name)
    active_groups: dict[str, str] = {}
    rows: list[list[str]] = []
    for region in regions:
        if region.get("kind") == "group_value":
            name = str((region.get("properties") or {}).get("column") or "Группа")
            active_groups[name] = str(region.get("output") or "")
        elif region.get("kind") == "table_rows":
            for row in region.get("output") or []:
                rows.append(
                    [active_groups.get(name, "") for name in group_columns]
                    + [str(value) for value in row]
                )
    obj["result"] = {
        "columns": group_columns + headers,
        "rows": rows,
        "groups": active_groups,
    }


class SourceRegistry:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.workspace: Path | None = None
        self.index_path = data_root / "remote-sources.json"
        self._lock = threading.Lock()
        data_root.mkdir(parents=True, exist_ok=True)
        try:
            self.remote_index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.remote_index: dict[str, str] = {}

    def set_workspace(self, value: str) -> Path:
        root = Path(value).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Рабочий путь не является каталогом")
        self.workspace = root
        return root

    def resolve(self, value: str) -> Path:
        value = unquote(value.strip())
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"}:
            return self._download(value)
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            if self.workspace is None:
                raise ValueError("Для относительного пути сначала выберите каталог")
            candidate = self.workspace / candidate
        source = candidate.resolve(strict=True)
        if not source.is_file() or source.suffix.lower() != PDF_SUFFIX:
            raise ValueError("Источник должен быть существующим PDF-файлом")
        return source

    def _download(self, url: str) -> Path:
        import httpx

        with self._lock:
            relative = self.remote_index.get(url)
            if relative:
                existing = (self.data_root / relative).resolve()
                if existing.is_file():
                    return existing
            bucket = self.data_root / "blob-cache" / uuid.uuid4().hex
            bucket.mkdir(parents=True)
            name = Path(urlparse(url).path).name or "document.pdf"
            if not name.lower().endswith(PDF_SUFFIX):
                name += PDF_SUFFIX
            destination = bucket / name
            total = 0
            with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_PDF_BYTES:
                            raise ValueError("PDF из blob-хранилища превышает 1 ГБ")
                        output.write(chunk)
            with destination.open("rb") as downloaded:
                signature = downloaded.read(5)
            if signature != b"%PDF-":
                destination.unlink(missing_ok=True)
                raise ValueError("Blob-хранилище вернуло не PDF")
            self.remote_index[url] = destination.relative_to(self.data_root).as_posix()
            atomic_json(self.index_path, self.remote_index)
            return destination


class DocumentStore:
    def __init__(self) -> None:
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    def lock(self, source: Path) -> threading.RLock:
        key = str(source).casefold() if os.name == "nt" else str(source)
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    def load(self, source: Path) -> dict[str, Any]:
        with self.lock(source):
            pymupdf = import_pymupdf()
            with pymupdf.open(source) as pdf:
                page_count = pdf.page_count
            path = sidecar_path(source)
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                document = new_document(source, page_count)
            document["source"] = str(source)
            document["pageCount"] = page_count
            return materialize(document)

    def save(self, source: Path, document: dict[str, Any]) -> dict[str, Any]:
        with self.lock(source):
            if document.get("schema") != 1:
                raise ValueError("Поддерживается только схема документа 1")
            document["source"] = str(source)
            normalized = materialize(document)
            atomic_json(sidecar_path(source), normalized)
            return normalized

    def mutate(
        self, source: Path, callback: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]:
        with self.lock(source):
            document = self.load(source)
            callback(document)
            return self.save(source, document)


class PaddleClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def recognize(self, image: bytes) -> str:
        import httpx

        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(timeout=httpx.Timeout(120.0, read=600.0)) as client:
            response = client.post(
                f"{self.url}/api/v2/ocr/jobs",
                headers=headers,
                data={
                    "model": "PP-StructureV3",
                    "optionalPayload": json.dumps(
                        {
                            "useTableRecognition": True,
                            "useFormulaRecognition": False,
                            "useRegionDetection": False,
                            "formatBlockContent": True,
                        }
                    ),
                },
                files={"file": ("region.png", image, "image/png")},
            )
            response.raise_for_status()
            job_id = response.json()["data"]["jobId"]
            deadline = time.monotonic() + 60 * 60
            while time.monotonic() < deadline:
                status = client.get(
                    f"{self.url}/api/v2/ocr/jobs/{job_id}", headers=headers
                )
                status.raise_for_status()
                payload = status.json()["data"]
                if payload["state"] == "done":
                    result = client.get(
                        f"{self.url}/api/v2/ocr/jobs/{job_id}/result",
                        headers=headers,
                    )
                    result.raise_for_status()
                    return flatten_ocr_result(result.text)
                if payload["state"] == "failed":
                    raise RuntimeError(payload.get("errorMsg") or "Ошибка PaddleOCR")
                time.sleep(0.5)
        raise TimeoutError("PaddleOCR не завершил область за один час")


class LlamaClient:
    def __init__(self, url: str, api_key: str = "") -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key

    def process(self, text: str, prompt: str) -> str:
        import httpx

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        with httpx.Client(
            timeout=httpx.Timeout(30.0, read=600.0), headers=headers
        ) as client:
            models = client.get(f"{self.url}/models")
            models.raise_for_status()
            model = models.json().get("data", [{}])[0].get("id", "local-model")
            response = client.post(
                f"{self.url}/chat/completions",
                json={
                    "model": model,
                    "temperature": 0,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Ты исправляешь OCR. Ответ должен содержать только итоговый текст.",
                        },
                        {"role": "user", "content": f"{prompt}\n\nOCR:\n{text}"},
                    ],
                },
            )
            response.raise_for_status()
            return clean_text(response.json()["choices"][0]["message"]["content"])


def render_crop(source: Path, page_number: int, rect: dict[str, float]) -> bytes:
    pymupdf = import_pymupdf()
    with pymupdf.open(source) as pdf:
        if page_number < 1 or page_number > pdf.page_count:
            raise ValueError("Страница области не существует")
        page = pdf.load_page(page_number - 1)
        bounds = page.rect
        clip = pymupdf.Rect(
            bounds.x0 + rect["x"] * bounds.width,
            bounds.y0 + rect["y"] * bounds.height,
            bounds.x0 + (rect["x"] + rect["w"]) * bounds.width,
            bounds.y0 + (rect["y"] + rect["h"]) * bounds.height,
        )
        # Rasterization deliberately ignores any embedded PDF text layer.
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), clip=clip, alpha=False)
        return pixmap.tobytes("png")


def cell_rect(
    rect: dict[str, float], x0: float, x1: float, y0: float, y1: float
) -> dict[str, float]:
    return {
        "x": rect["x"] + x0 * rect["w"],
        "y": rect["y"] + y0 * rect["h"],
        "w": (x1 - x0) * rect["w"],
        "h": (y1 - y0) * rect["h"],
    }


class ExtractionTasks:
    def __init__(self, store: DocumentStore, paddle: PaddleClient, llama: LlamaClient):
        self.store = store
        self.paddle = paddle
        self.llama = llama
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def submit(self, source: Path, object_id: str, region_id: str) -> str:
        task_id = uuid.uuid4().hex
        with self._lock:
            self._tasks[task_id] = {"state": "queued", "regionId": region_id}
        self._set_region(source, object_id, region_id, status="queued", error=None)
        threading.Thread(
            target=self._run,
            args=(task_id, source, object_id, region_id),
            daemon=True,
            name=f"digitizer-{task_id[:8]}",
        ).start()
        return task_id

    def status(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            return dict(self._tasks[task_id])

    def _set_task(self, task_id: str, **values: Any) -> None:
        with self._lock:
            self._tasks[task_id].update(values)

    def _locate(
        self, document: dict[str, Any], object_id: str, region_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        obj = next(
            (item for item in document.get("objects") or [] if item.get("id") == object_id),
            None,
        )
        if obj is None:
            raise ValueError("Объект разметки не найден")
        region = next(
            (item for item in obj.get("regions") or [] if item.get("id") == region_id),
            None,
        )
        if region is None:
            raise ValueError("Область разметки не найдена")
        return obj, region

    def _set_region(
        self, source: Path, object_id: str, region_id: str, **values: Any
    ) -> None:
        def mutate(document: dict[str, Any]) -> None:
            _obj, region = self._locate(document, object_id, region_id)
            region.update(values)

        self.store.mutate(source, mutate)

    def _recognize(self, source: Path, page: int, rect: dict[str, float], region: dict[str, Any]) -> str:
        text = self.paddle.recognize(render_crop(source, page, rect))
        prompt = str((region.get("properties") or {}).get("prompt") or "").strip()
        if (region.get("properties") or {}).get("useLlm"):
            text = self.llama.process(text, prompt or DEFAULT_PROMPT)
        return text

    def _run(self, task_id: str, source: Path, object_id: str, region_id: str) -> None:
        try:
            self._set_task(task_id, state="running")
            self._set_region(source, object_id, region_id, status="running")
            snapshot = self.store.load(source)
            obj, region = self._locate(snapshot, object_id, region_id)
            kind = str(region.get("kind"))
            if kind not in REGION_KINDS:
                raise ValueError("Неизвестный тип области")
            page = int(region.get("page", 0))
            rect = validate_rect(region.get("rect"))
            output: Any
            if kind in {"field", "group_value"}:
                output = self._recognize(source, page, rect, region)
            else:
                grid = region.get("grid") or {}
                x_cuts = normalized_cuts(grid.get("columns"))
                y_cuts = [0.0, 1.0] if kind == "table_header" else normalized_cuts(grid.get("rows"))
                output = []
                for y0, y1 in zip(y_cuts, y_cuts[1:]):
                    row: list[str] = []
                    for x0, x1 in zip(x_cuts, x_cuts[1:]):
                        row.append(
                            self._recognize(
                                source, page, cell_rect(rect, x0, x1, y0, y1), region
                            )
                        )
                    output.append(row)

            def save_output(document: dict[str, Any]) -> None:
                target_obj, target = self._locate(document, object_id, region_id)
                target.update(
                    {"status": "recognized", "output": output, "error": None, "updated": time.time()}
                )
                result = target_obj.setdefault("result", {})
                if kind == "field":
                    result["value"] = output
                else:
                    rebuild_table_result(target_obj)

            document = self.store.mutate(source, save_output)
            self._set_task(task_id, state="done", document=document)
        except Exception as exc:
            try:
                self._set_region(
                    source, object_id, region_id, status="error", error=str(exc)
                )
            finally:
                self._set_task(task_id, state="failed", error=str(exc))


def choose_pdf() -> str | None:
    if os.name != "nt":
        raise RuntimeError("Системный диалог выбора PDF доступен только в Windows")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d=New-Object System.Windows.Forms.OpenFileDialog;"
        "$d.Filter='PDF (*.pdf)|*.pdf';$d.Multiselect=$false;"
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){"
        "$b=[Text.Encoding]::UTF8.GetBytes($d.FileName);"
        "[Console]::Out.WriteLine([Convert]::ToBase64String($b))}"
    )
    executable = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [str(executable), "-NoProfile", "-STA", "-EncodedCommand", encoded],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
        check=False,
    )
    if completed.returncode:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        raise RuntimeError(completed.stderr.decode(encoding, errors="replace"))
    lines = completed.stdout.decode("ascii", errors="ignore").strip().splitlines()
    return base64.b64decode(lines[-1]).decode("utf-8") if lines else None


def create_app(
    registry: SourceRegistry,
    store: DocumentStore,
    tasks: ExtractionTasks,
    html_path: Path,
):
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    app = FastAPI(title="Structured PDF digitizer")

    def bad(exc: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return html_path.read_text(encoding="utf-8")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ready"}

    @app.post("/api/open/dialog")
    def open_dialog() -> dict[str, Any]:
        try:
            selected = choose_pdf()
            return {"cancelled": selected is None, "source": selected}
        except Exception as exc:
            raise bad(exc) from exc

    @app.get("/api/document")
    def get_document(source: str = Query(...)) -> dict[str, Any]:
        try:
            path = registry.resolve(source)
            return {"source": str(path), "name": path.name, "document": store.load(path)}
        except Exception as exc:
            raise bad(exc) from exc

    @app.put("/api/document")
    def put_document(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = registry.resolve(str(payload.get("source") or ""))
            document = payload.get("document")
            if not isinstance(document, dict):
                raise ValueError("document должен быть JSON-объектом")
            return {"document": store.save(source, document), "path": str(sidecar_path(source))}
        except Exception as exc:
            raise bad(exc) from exc

    @app.get("/api/pdf/page/{page_number}")
    def pdf_page(page_number: int, source: str = Query(...)) -> Response:
        try:
            path = registry.resolve(source)
            pymupdf = import_pymupdf()
            with pymupdf.open(path) as pdf:
                if page_number < 1 or page_number > pdf.page_count:
                    raise ValueError("Страница не найдена")
                page = pdf.load_page(page_number - 1)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
                return Response(pixmap.tobytes("png"), media_type="image/png")
        except Exception as exc:
            raise bad(exc) from exc

    @app.post("/api/ocr")
    def run_ocr(payload: dict[str, Any]) -> dict[str, str]:
        try:
            source = registry.resolve(str(payload.get("source") or ""))
            return {
                "taskId": tasks.submit(
                    source, str(payload.get("objectId") or ""), str(payload.get("regionId") or "")
                )
            }
        except Exception as exc:
            raise bad(exc) from exc

    @app.get("/api/tasks/{task_id}")
    def task_status(task_id: str) -> dict[str, Any]:
        try:
            return tasks.status(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Задание не найдено") from exc

    @app.get("/api/templates")
    def templates(source: str = Query(...)) -> dict[str, Any]:
        try:
            path = template_path(registry.resolve(source))
            try:
                values = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                values = {"schema": 1, "templates": []}
            return values
        except Exception as exc:
            raise bad(exc) from exc

    @app.post("/api/templates")
    def save_template(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = registry.resolve(str(payload.get("source") or ""))
            name = str(payload.get("name") or "").strip()
            document = payload.get("document") or {}
            if not name:
                raise ValueError("Укажите название шаблона")
            path = template_path(source)
            try:
                catalog = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                catalog = {"schema": 1, "templates": []}
            template = {
                "id": uuid.uuid4().hex,
                "name": name,
                "objects": [],
                "created": time.time(),
            }
            for obj in document.get("objects") or []:
                copied = json.loads(json.dumps(obj, ensure_ascii=False))
                copied["status"] = "pending"
                copied["result"] = {"value": ""} if copied.get("type") == "field" else {"columns": [], "rows": []}
                for region in copied.get("regions") or []:
                    region.update({"status": "pending", "output": None, "error": None})
                template["objects"].append(copied)
            catalog.setdefault("templates", []).append(template)
            atomic_json(path, catalog)
            return catalog
        except Exception as exc:
            raise bad(exc) from exc

    @app.post("/api/templates/apply")
    def apply_template(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            source = registry.resolve(str(payload.get("source") or ""))
            template_id = str(payload.get("templateId") or "")
            catalog = json.loads(template_path(source).read_text(encoding="utf-8"))
            template = next(
                (item for item in catalog.get("templates") or [] if item.get("id") == template_id),
                None,
            )
            if template is None:
                raise ValueError("Шаблон не найден")
            document = store.load(source)
            document["templateId"] = template_id
            document["objects"] = json.loads(json.dumps(template.get("objects") or []))
            return {"document": store.save(source, document)}
        except Exception as exc:
            raise bad(exc) from exc

    @app.get("/api/export")
    def export(source: str = Query(...)) -> JSONResponse:
        try:
            path = registry.resolve(source)
            document = store.load(path)
            return JSONResponse(
                document,
                headers={"Content-Disposition": f'attachment; filename="{path.stem}.json"'},
            )
        except Exception as exc:
            raise bad(exc) from exc

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9400)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--paddle-url", default="http://127.0.0.1:9399")
    parser.add_argument("--paddle-token", required=True)
    parser.add_argument("--llama-url", default="http://127.0.0.1:6381/v1")
    parser.add_argument("--llama-key", default="")
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("Digitizer may bind only to 127.0.0.1")
    html_path = Path(__file__).with_name("app.html")
    registry = SourceRegistry(args.data_root)
    store = DocumentStore()
    tasks = ExtractionTasks(
        store,
        PaddleClient(args.paddle_url, args.paddle_token),
        LlamaClient(args.llama_url, args.llama_key),
    )
    app = create_app(registry, store, tasks, html_path)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
