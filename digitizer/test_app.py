from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import app  # noqa: E402

SERVER_SPEC = importlib.util.spec_from_file_location(
    "paddle_server", Path(__file__).parents[1] / "paddleocr" / "server.py"
)
paddle_server = importlib.util.module_from_spec(SERVER_SPEC)
assert SERVER_SPEC.loader is not None
SERVER_SPEC.loader.exec_module(paddle_server)


class FakePaddle:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []

    def recognize(self, image: bytes, mode: str = "text") -> str:
        self.calls.append((mode, image))
        return f"result:{mode}:{len(self.calls)}"

    def health(self):
        return {"status": "ready", "capabilities": ["text", "seal", "formula"]}


class FakeLlama:
    def process(self, text: str, prompt: str) -> str:
        return f"fixed:{text}"


class MemoryStore:
    def __init__(self) -> None:
        self.document = None

    def save(self, _source: Path, document):
        self.document = app.materialize(app.normalize_document(copy.deepcopy(document)))
        return copy.deepcopy(self.document)

    def load(self, _source: Path):
        return copy.deepcopy(self.document)

    def mutate(self, source: Path, callback):
        document = self.load(source)
        callback(document)
        return self.save(source, document)


class DigitizerSchemaTests(unittest.TestCase):
    def test_catalog_builds_semantic_record_and_table(self) -> None:
        approval = app.create_object("approval")
        self.assertEqual(approval["type"], "record")
        self.assertEqual(
            {field["ocrMode"] for field in approval["fields"]},
            {"text", "text_seal", "seal", "none"},
        )
        table = app.create_object("table")
        self.assertEqual(table["type"], "table")
        self.assertEqual(table["rows"], [])

    def test_schema_one_is_migrated_without_losing_values(self) -> None:
        legacy = {
            "schema": 1,
            "objects": [
                {
                    "id": "old-field",
                    "type": "field",
                    "name": "Организация",
                    "key": "organization",
                    "regions": [
                        {
                            "id": "source",
                            "kind": "field",
                            "page": 1,
                            "rect": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1},
                            "properties": {"orientation": 90},
                            "status": "recognized",
                            "output": "Завод",
                        }
                    ],
                    "result": {"value": "Завод"},
                },
                {
                    "id": "old-table",
                    "type": "table",
                    "name": "ТО",
                    "key": "maintenance",
                    "regions": [],
                    "result": {"columns": ["Операция"], "rows": [["Осмотр"]]},
                },
            ],
        }
        migrated = app.normalize_document(legacy)
        self.assertEqual(migrated["schema"], 2)
        self.assertEqual(migrated["objects"][0]["fields"][0]["value"], "Завод")
        self.assertEqual(
            migrated["objects"][0]["fields"][0]["sources"][0]["orientation"], 90
        )
        self.assertEqual(migrated["objects"][1]["rows"][0]["cells"][0]["value"], "Осмотр")

    def test_materialize_applies_groups_only_to_selected_rows(self) -> None:
        document = app.new_document(Path("sample.pdf"), 1)
        table = app.create_object("table", "Регламент")
        table["key"] = "schedule"
        column = app.new_field("action", "Операция")
        column["value"] = "Операция"
        table["columns"].append(column)
        rows = []
        for value in ("Осмотр", "Замена"):
            rows.append(
                {
                    "id": app.uid(),
                    "blockId": None,
                    "cells": [
                        {
                            "id": app.uid(),
                            "columnId": column["id"],
                            "value": value,
                            "orientation": None,
                            "sources": [],
                        }
                    ],
                }
            )
        table["rows"] = rows
        group = app.new_field("period", "Периодичность")
        group["value"] = "ежемесячно"
        group["rowIds"] = [rows[1]["id"]]
        table["groups"].append(group)
        document["objects"].append(table)
        data = app.materialize(document)["data"]["schedule"]
        self.assertEqual(data["rows"][0]["Периодичность"], "")
        self.assertEqual(data["rows"][1]["Периодичность"], "ежемесячно")
        self.assertEqual(data["rows"][1]["Операция"], "Замена")

    def test_cell_sources_are_discoverable(self) -> None:
        document = app.new_document(Path("sample.pdf"), 1)
        table = app.create_object("table")
        column = app.new_field("value", "Значение", "formula")
        table["columns"].append(column)
        source = app.new_source(1, {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2})
        cell = {
            "id": app.uid(),
            "columnId": column["id"],
            "value": "",
            "orientation": 270,
            "sources": [source],
        }
        table["rows"].append(
            {"id": app.uid(), "blockId": None, "cells": [cell]}
        )
        document["objects"].append(table)
        found = list(app.iter_regions(document))
        self.assertEqual(found[0][2]["id"], source["id"])
        self.assertEqual(found[0][3], "cell")

    def test_paddle_profiles_load_only_their_required_models(self) -> None:
        fast = set(paddle_server.required_models_for_profile("fast-text"))
        digitizer = set(paddle_server.required_models_for_profile("digitizer"))
        full = set(paddle_server.required_models_for_profile("full-structure"))
        self.assertIn("PP-OCRv6_tiny_det", fast)
        self.assertNotIn("SLANet_plus", fast)
        self.assertIn("PP-OCRv4_server_seal_det", digitizer)
        self.assertIn("PP-FormulaNet_plus-S", digitizer)
        self.assertNotIn("SLANet_plus", digitizer)
        self.assertIn("PP-Chart2Table", full)

    def test_active_profile_rejects_unloaded_request_branch(self) -> None:
        options = paddle_server.build_predict_options(
            {"useTableRecognition": False}, {"text"}
        )
        self.assertFalse(options["use_table_recognition"])
        with self.assertRaisesRegex(ValueError, "formula"):
            paddle_server.build_predict_options(
                {"useFormulaRecognition": True}, {"text"}
            )

    def test_legacy_template_catalog_is_migrated(self) -> None:
        catalog = app.normalize_template_catalog(
            {
                "schema": 1,
                "templates": [
                    {
                        "id": "template",
                        "name": "Старый",
                        "objects": [
                            {
                                "id": "field",
                                "type": "field",
                                "name": "Поле",
                                "regions": [],
                                "result": {"value": "текст"},
                            }
                        ],
                    }
                ],
            }
        )
        self.assertEqual(catalog["schema"], 2)
        self.assertEqual(catalog["templates"][0]["objects"][0]["type"], "record")


class DigitizerExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pdf_path = Path("document.pdf")
        self.store = MemoryStore()
        self.paddle = FakePaddle()
        self.tasks = app.ExtractionTasks(self.store, self.paddle, FakeLlama())
        self.original_render_crop = app.render_crop
        app.render_crop = lambda source, page, rect, orientation=0: (
            f"{source}:{page}:{rect}:{orientation}".encode()
        )

    def tearDown(self) -> None:
        app.render_crop = self.original_render_crop

    def test_field_uses_its_mode_orientation_and_llm(self) -> None:
        document = app.new_document(self.pdf_path, 1)
        obj = app.create_object("approval")
        field = next(item for item in obj["fields"] if item["ocrMode"] == "seal")
        source = app.new_source(1, {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5})
        source["orientation"] = 90
        source["properties"] = {"useLlm": True, "prompt": "correct"}
        field["sources"].append(source)
        document["objects"].append(obj)
        self.store.save(self.pdf_path, document)

        self.tasks._tasks["task"] = {"state": "queued"}
        self.tasks._run("task", self.pdf_path, source["id"])

        result = self.tasks.status("task")
        self.assertEqual(result["state"], "done")
        self.assertEqual(self.paddle.calls[0][0], "seal")
        saved = self.store.load(self.pdf_path)
        saved_field = saved["objects"][0]["fields"][4]
        self.assertTrue(saved_field["value"].startswith("fixed:result:seal"))
        self.assertTrue(self.paddle.calls[0][1].endswith(b":90"))

    def test_table_block_recognizes_each_manual_cell_without_table_model(self) -> None:
        document = app.new_document(self.pdf_path, 1)
        table = app.create_object("table")
        first = app.new_field("text", "Текст", "text")
        second = app.new_field("formula", "Формула", "formula")
        table["columns"] = [first, second]
        block = {
            "id": "block",
            "name": "Блок 1",
            "region": app.new_source(
                1,
                {"x": 0.0, "y": 0.0, "w": 0.8, "h": 0.8},
                "table_block",
            ),
            "grid": {"columns": [0.5], "rows": [0.5]},
            "rowIds": [],
        }
        table["blocks"] = [block]
        app.ExtractionTasks._sync_rows(table, block, 2)
        document["objects"].append(table)
        self.store.save(self.pdf_path, document)

        self.tasks._tasks["task"] = {"state": "queued"}
        self.tasks._run("task", self.pdf_path, block["region"]["id"])

        self.assertEqual(self.tasks.status("task")["state"], "done")
        self.assertEqual([mode for mode, _image in self.paddle.calls], ["text", "formula", "text", "formula"])
        saved = self.store.load(self.pdf_path)
        self.assertEqual(len(saved["objects"][0]["rows"]), 2)
        self.assertEqual(saved["objects"][0]["rows"][1]["cells"][1]["value"], "result:formula:4")

    def test_cell_source_inherits_column_mode(self) -> None:
        document = app.new_document(self.pdf_path, 1)
        table = app.create_object("table")
        column = app.new_field("formula", "Формула", "formula")
        source = app.new_source(1, {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2})
        table["columns"] = [column]
        table["rows"] = [
            {
                "id": app.uid(),
                "blockId": None,
                "cells": [
                    {
                        "id": app.uid(),
                        "columnId": column["id"],
                        "value": "",
                        "orientation": 180,
                        "sources": [source],
                    }
                ],
            }
        ]
        document["objects"].append(table)
        self.store.save(self.pdf_path, document)
        self.tasks._tasks["task"] = {"state": "queued"}

        self.tasks._run("task", self.pdf_path, source["id"])

        self.assertEqual(self.paddle.calls[0][0], "formula")
        saved = self.store.load(self.pdf_path)
        self.assertEqual(
            saved["objects"][0]["rows"][0]["cells"][0]["value"],
            "result:formula:1",
        )

    def test_manual_edit_during_ocr_is_not_overwritten(self) -> None:
        document = app.new_document(self.pdf_path, 1)
        obj = app.create_object("custom_field")
        field = obj["fields"][0]
        field["value"] = "Исправлено вручную"
        source = app.new_source(1, {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2})
        source["output"] = "Старый OCR"
        field["sources"].append(source)
        document["objects"].append(obj)
        self.store.save(self.pdf_path, document)

        original_recognize = self.paddle.recognize

        def recognize_and_edit(image: bytes, mode: str = "text") -> str:
            result = original_recognize(image, mode)

            def mark_modified(current):
                next(app.iter_regions(current))[2]["status"] = "modified"

            self.store.mutate(self.pdf_path, mark_modified)
            return result

        self.paddle.recognize = recognize_and_edit

        self.tasks._tasks["task"] = {"state": "running"}
        self.tasks._run("task", self.pdf_path, source["id"])

        saved = self.store.load(self.pdf_path)
        saved_field = saved["objects"][0]["fields"][0]
        self.assertEqual(saved_field["value"], "Исправлено вручную")
        self.assertEqual(saved_field["sources"][0]["status"], "modified")


if __name__ == "__main__":
    unittest.main()
