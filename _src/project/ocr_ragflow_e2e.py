#!/usr/bin/env python3
"""Launch the real GPU gateway and exercise RAGFlow's unmodified OCR parser."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont


DEJAVU_SANS_SHA256 = (
    "7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def load_fixture_font(path: Path, size: int):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Pinned Cyrillic E2E font is missing: {path}")
    digest = sha256_file(path)
    if digest != DEJAVU_SANS_SHA256:
        raise RuntimeError(
            "Pinned Cyrillic E2E font integrity mismatch: expected "
            f"{DEJAVU_SANS_SHA256}, found {digest}"
        )
    return ImageFont.truetype(str(path), size=size)


def create_samples(work_dir: Path, font_path: Path) -> tuple[Path, Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    image_path = work_dir / "ragflow-ocr-e2e.png"
    pdf_path = work_dir / "ragflow-ocr-e2e.pdf"
    # A document-sized raster exercises substantially more of the 8 GiB memory
    # envelope than a tiny warm-up image while staying below the 4000 px cap.
    image = Image.new("RGB", (3200, 1800), "white")
    draw = ImageDraw.Draw(image)
    font = load_fixture_font(font_path, 176)
    lines = (
        "ПОРТАТИВНЫЙ ДОКУМЕНТ",
        "ТЕСТ OCR 2026",
        "TOTAL 4425",
    )
    for index, line in enumerate(lines):
        draw.text((160, 180 + index * 360), line, font=font, fill="black")
    table_font = load_fixture_font(font_path, 72)
    columns = (160, 1080, 2020, 3040)
    rows = (1220, 1380, 1540, 1720)
    for x in columns:
        draw.line((x, rows[0], x, rows[-1]), fill="black", width=10)
    for y in rows:
        draw.line((columns[0], y, columns[-1], y), fill="black", width=10)
    cells = (
        ("КОД", "СВЯЗЬ", "ЗНАЧЕНИЕ"),
        ("A-17", "B-42", "125"),
        ("B-42", "C-09", "260"),
    )
    for row_index, values in enumerate(cells):
        for column_index, value in enumerate(values):
            draw.text(
                (columns[column_index] + 28, rows[row_index] + 35),
                value,
                font=table_font,
                fill="black",
            )
    image.save(image_path)
    image.save(pdf_path, "PDF", resolution=150.0)
    return image_path, pdf_path


def wait_ready(url: str, process: subprocess.Popen, timeout: float = 360.0) -> dict:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"OCR gateway exited with code {process.returncode}")
        try:
            response = requests.get(url + "/health", timeout=2)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "ready" and data.get("strict_gpu") is True:
                    return data
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"OCR gateway readiness timed out: {last_error}")


def normalized(text: str) -> str:
    return " ".join(text.upper().split())


def assert_real_text(text: str, label: str) -> None:
    value = normalized(text)
    if "2026" not in value or "ДОКУМ" not in value:
        raise RuntimeError(f"{label} OCR did not recognize the expected text: {text!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ocr-python", required=True, type=Path)
    parser.add_argument("--gateway-script", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--ragflow-dir", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()

    port = free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    jobs_root = args.work_dir / "jobs"
    image_path, pdf_path = create_samples(args.work_dir, args.font)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    with args.log.open("w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(
            [
                str(args.ocr_python),
                str(args.gateway_script),
                "--config",
                str(args.config),
                "--model-root",
                str(args.model_root),
                "--jobs-root",
                str(jobs_root),
                "--port",
                str(port),
                "--token",
                "local",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            creationflags=creationflags,
        )
        try:
            health = wait_ready(base_url, process)
            sys.path.insert(0, str(args.ragflow_dir))
            from deepdoc.parser.paddleocr_parser import PaddleOCRParser

            rag_parser = PaddleOCRParser(
                base_url=base_url,
                access_token="local",
                algorithm="PP-StructureV3",
                request_timeout=600,
            )
            image_text = rag_parser.parse_image(str(image_path))
            assert_real_text(image_text, "image")
            sections, tables = rag_parser.parse_pdf(
                str(pdf_path), parse_method="pipeline"
            )
            pdf_text = "\n".join(section[0] for section in sections if section)
            assert_real_text(pdf_text, "PDF")
            table_sections = [
                section
                for section in sections
                if len(section) >= 2 and section[1] == "table"
            ]
            if not table_sections:
                raise RuntimeError(
                    "PP-StructureV3 did not return a table-labelled section"
                )
            table_text = "\n".join(section[0] for section in table_sections)
            normalized_table = normalized(table_text)
            missing_structure_tags = [
                tag for tag in ("<table", "<tr", "<td") if tag not in table_text.lower()
            ]
            if missing_structure_tags:
                raise RuntimeError(
                    "Table recognition did not preserve HTML row/cell structure; "
                    f"missing {missing_structure_tags!r}: {table_text!r}"
                )
            if "A-17" not in normalized_table or "B-42" not in normalized_table:
                raise RuntimeError(
                    "Table recognition lost the expected cross-linked cell values: "
                    f"{table_text!r}"
                )

            record = {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "gateway_contract": "/api/v2/ocr/jobs",
                "ragflow_parser": "unmodified deepdoc.parser.paddleocr_parser",
                "algorithm": "PP-StructureV3",
                "detector": "PP-OCRv6_medium_det",
                "recognizer": "eslav_PP-OCRv5_mobile_rec",
                "table_recognition": True,
                "table_structure_model": "SLANet_plus",
                "fixture_font_sha256": DEJAVU_SANS_SHA256,
                "fixture_pixels": [3200, 1800],
                "image_text": image_text[:1000],
                "pdf_text": pdf_text[:1000],
                "table_text": table_text[:2000],
                "ragflow_separate_tables": len(tables),
                "health": health,
                "passed": True,
            }
            args.record.parent.mkdir(parents=True, exist_ok=True)
            args.record.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(record, ensure_ascii=False, indent=2), flush=True)
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] OCR/RAGFlow E2E failed: {error}", file=sys.stderr, flush=True)
        raise
