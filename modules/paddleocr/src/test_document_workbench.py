#!/usr/bin/env python3
"""Dependency-light contracts for the document workbench."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from document_workbench import (
    ResultStore,
    Workspace,
    build_tree,
    html_table_to_markdown,
    join_markdown,
    normalize_asset_path,
    parse_ocr_bundle,
    parse_ocr_jsonl,
)


def main() -> int:
    table = html_table_to_markdown(
        "<table><tr><th>Код</th><th>Текст</th></tr><tr><td>A-1</td><td>Пример</td></tr></table>"
    )
    assert "| Код | Текст |" in table and "| A-1 | Пример |" in table

    payload = {
        "result": {
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "parsing_res_list": [
                            {"block_label": "doc_title", "block_content": "Документ"},
                            {"block_label": "text", "block_content": "Текст страницы"},
                        ]
                    }
                }
            ]
        }
    }
    pages = parse_ocr_jsonl(json.dumps(payload, ensure_ascii=False))
    assert pages == ["# Документ\n\nТекст страницы"]
    assert join_markdown(pages).startswith("<!-- page: 1 -->")
    markdown_payload = {
        "result": {
            "markdown": {
                "text": "Текст с рисунком\n\n![Рисунок](imgs/figure.png)",
                "assets": ["imgs/figure.png"],
            }
        }
    }
    markdown_pages, markdown_assets = parse_ocr_bundle(
        json.dumps(markdown_payload, ensure_ascii=False)
    )
    assert markdown_pages == ["Текст с рисунком\n\n![Рисунок](imgs/figure.png)"]
    assert markdown_assets == ["imgs/figure.png"]
    for invalid_asset in ("../outside.png", "."):
        try:
            normalize_asset_path(invalid_asset)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid Markdown asset path must be rejected")

    with tempfile.TemporaryDirectory(prefix="ocr-workbench-") as temporary:
        root = Path(temporary)
        documents = root / "documents"
        documents.mkdir()
        source = documents / "пример.pdf"
        source.write_bytes(b"%PDF-fake")
        (documents / "notes.txt").write_text("notes", encoding="utf-8")
        workspace = Workspace()
        workspace.select(str(documents))
        assert workspace.resolve("пример.pdf", pdf_only=True) == source.resolve()
        try:
            workspace.resolve("../outside.pdf", pdf_only=True)
        except (ValueError, FileNotFoundError):
            pass
        else:
            raise AssertionError("Workspace traversal must be rejected")
        tree = build_tree(documents)
        assert [item["kind"] for item in tree] == ["file", "pdf"]
        store = ResultStore(root / "state")
        store.put(
            source,
            pages,
            {"useTableRecognition": True},
            {"imgs/figure.png": b"PNG-data"},
        )
        assert store.get(source)["pages"] == pages
        export_root = root / "export"
        exported = store.export_assets(source, export_root)
        assert exported == [export_root / "imgs" / "figure.png"]
        assert exported[0].read_bytes() == b"PNG-data"
        store.put(source, ["edited"], {"useTableRecognition": True})
        assert store.get(source)["assets"][0]["path"] == "imgs/figure.png"

    html = Path(__file__).with_name("document_workbench.html").read_text(
        encoding="utf-8"
    )
    assert 'data-option="useFormulaRecognition"' in html
    assert 'data-option="useFormulaRecognition" data-type="bool" type="checkbox" checked' in html
    assert "resize:none; overflow:hidden" in html

    print("Document workbench contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
