#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from digitizer import (
    DocumentStore,
    cell_rect,
    clean_text,
    flatten_ocr_result,
    materialize,
    new_document,
    normalized_cuts,
    rebuild_table_result,
    sidecar_path,
)


def main() -> int:
    assert normalized_cuts([0.7, 0.3, 0.3]) == [0.0, 0.3, 0.7, 1.0]
    assert cell_rect({"x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6}, 0, 0.5, 0, 1) == {
        "x": 0.1,
        "y": 0.2,
        "w": 0.4,
        "h": 0.6,
    }
    assert clean_text("```text\nПример\n```") == "Пример"
    payload = {
        "result": {
            "layoutParsingResults": [
                {"prunedResult": {"parsing_res_list": [{"block_content": "Русский текст"}]}}
            ]
        }
    }
    assert flatten_ocr_result(json.dumps(payload, ensure_ascii=False)) == "Русский текст"
    document = new_document(Path("scan.pdf"), 2)
    document["objects"] = [
        {"id": "f", "type": "field", "key": "title", "result": {"value": "Отчёт"}},
        {
            "id": "t",
            "type": "table",
            "key": "items",
            "result": {"columns": ["Код", "Текст"], "rows": [["1", "Строка"]]},
        },
    ]
    data = materialize(document)["data"]
    assert data["title"] == "Отчёт"
    assert data["items"]["rows"] == [{"Код": "1", "Текст": "Строка"}]
    table = {
        "type": "table",
        "regions": [
            {"kind": "table_header", "page": 1, "status": "recognized", "output": [["Код"]]},
            {"kind": "group_value", "page": 1, "status": "recognized", "output": "A", "properties": {"column": "Раздел"}},
            {"kind": "table_rows", "page": 1, "status": "recognized", "output": [["1"], ["2"]]},
            {"kind": "group_value", "page": 2, "status": "recognized", "output": "B", "properties": {"column": "Раздел"}},
            {"kind": "table_rows", "page": 2, "status": "recognized", "output": [["3"]]},
        ],
    }
    rebuild_table_result(table)
    assert table["result"]["columns"] == ["Раздел", "Код"]
    assert table["result"]["rows"] == [["A", "1"], ["A", "2"], ["B", "3"]]
    with tempfile.TemporaryDirectory(prefix="digitizer-") as temporary:
        source = Path(temporary) / "scan.pdf"
        source.write_bytes(b"%PDF-fake")
        assert sidecar_path(source).name == "scan.pdf.digitizer.json"

    html = Path(__file__).with_name("digitizer.html").read_text(encoding="utf-8")
    assert 'data-tool="table_rows"' in html
    assert 'data-tool="group_value"' in html
    assert "?source=" in html
    assert "текстовый слой" in html.lower()
    print("Digitizer contracts: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
