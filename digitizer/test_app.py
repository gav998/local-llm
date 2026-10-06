from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import app


class FakePaddle:
    def __init__(self) -> None:
        self.images: list[bytes] = []

    def recognize(self, image: bytes) -> str:
        self.images.append(image)
        return "OCR result"


class FakeLlm:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def correct(self, instruction: str, message: str) -> str:
        self.calls.append((instruction, message))
        return "```raw model answer```"


class ConfigTests(unittest.TestCase):
    def test_config_has_only_types_and_first_type_is_text(self) -> None:
        config = app.load_digitizer_config()
        self.assertEqual(list(config), ["types"])
        self.assertEqual(next(iter(config["types"])), "text")
        self.assertEqual(config["types"]["table"]["fields"]["body"]["kind"], "grid")
        for object_type in config["types"].values():
            for field in object_type["fields"].values():
                self.assertEqual(set(field["prompt"]), {"instruction", "input"})
                self.assertTrue(field["prompt"]["instruction"])
                self.assertTrue(field["prompt"]["input"])

    def test_public_config_does_not_duplicate_prompts(self) -> None:
        value = app.public_config()
        self.assertNotIn("prompt", value["types"]["text"]["fields"]["text"])

    def test_prompt_variables_are_available_in_their_object(self) -> None:
        for type_key, object_type in app.DIGITIZER_CONFIG["types"].items():
            available = {"cell", type_key, *object_type["fields"]}
            for field in object_type["fields"].values():
                templates = field["prompt"].values()
                variables = {
                    match.group(1)
                    for template in templates
                    for match in app.VARIABLE_RE.finditer(template)
                }
                self.assertLessEqual(variables, available)


class MarkdownModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.objects = [
            {
                "type": "text",
                "regions": [
                    {
                        "kind": "region",
                        "field": "text",
                        "page": 1,
                        "rect": [0.1, 0.2, 0.3, 0.1],
                        "rotation": 0,
                        "status": "recognized",
                        "text": "Первая строка\n\nВторая строка",
                    }
                ],
            },
            {
                "type": "table",
                "regions": [
                    {
                        "kind": "table",
                        "field": "body",
                        "page": 2,
                        "rect": [0.05, 0.1, 0.9, 0.8],
                        "rotation": 90,
                        "status": "recognized",
                        "grid": {
                            "x": [0, 0.5, 1],
                            "y": [0, 1],
                            "statuses": [["recognized", "modified"]],
                        },
                        "text": "| 1 | 2 |\n| --- | --- |\n| A | B |",
                    }
                ],
            },
        ]

    def test_round_trip_has_no_ids_or_json_sidecar_shape(self) -> None:
        markdown = app.serialize_markdown(self.objects, page_count=2)
        self.assertIn("<!-- digitizer:object", markdown)
        self.assertIn("<!-- digitizer:table", markdown)
        self.assertNotIn('"id"', markdown)
        self.assertIn("Первая строка\n\nВторая строка", markdown)
        self.assertEqual(app.parse_markdown(markdown, page_count=2), self.objects)

    def test_objects_are_separated_but_blank_lines_inside_text_are_safe(self) -> None:
        markdown = app.serialize_markdown(self.objects, page_count=2)
        self.assertIn("\n\n<!-- digitizer:object", markdown)
        parsed = app.parse_markdown(markdown, page_count=2)
        self.assertEqual(
            parsed[0]["regions"][0]["text"], "Первая строка\n\nВторая строка"
        )

    def test_transient_status_becomes_pending(self) -> None:
        self.objects[0]["regions"][0]["status"] = "running"
        self.objects[1]["regions"][0]["grid"]["statuses"] = [["queued", "running"]]
        parsed = app.parse_markdown(
            app.serialize_markdown(self.objects, page_count=2), page_count=2
        )
        self.assertEqual(parsed[0]["regions"][0]["status"], "pending")
        self.assertEqual(
            parsed[1]["regions"][0]["grid"]["statuses"],
            [["pending", "pending"]],
        )

    def test_error_is_stored_only_for_error_status(self) -> None:
        region = self.objects[0]["regions"][0]
        region["error"] = "stale"
        markdown = app.serialize_markdown(self.objects, page_count=2)
        self.assertNotIn("stale", markdown)
        region["status"] = "error"
        markdown = app.serialize_markdown(self.objects, page_count=2)
        self.assertIn("stale", markdown)

    def test_unknown_field_is_rejected(self) -> None:
        self.objects[0]["regions"][0]["field"] = "missing"
        with self.assertRaisesRegex(ValueError, "Неизвестное поле"):
            app.serialize_markdown(self.objects, page_count=2)

    def test_grid_must_cover_zero_to_one(self) -> None:
        self.objects[1]["regions"][0]["grid"]["x"] = [0.1, 1]
        with self.assertRaisesRegex(ValueError, "от 0 до 1"):
            app.serialize_markdown(self.objects, page_count=2)


class OcrTests(unittest.TestCase):
    def test_raw_recognition_wins_over_layout_image(self) -> None:
        payload = {
            "result": {
                "markdown": {"text": '<img src="image.png">'},
                "layoutParsingResults": [
                    {
                        "prunedResult": {
                            "overall_ocr_res": {"rec_texts": ["Номер", "12-34"]},
                            "parsing_res_list": [
                                {"block_label": "image", "block_content": "ignored"}
                            ],
                        }
                    }
                ],
            }
        }
        self.assertEqual(app.flatten_ocr_result(json.dumps(payload)), "Номер\n12-34")

    def test_markdown_heading_from_ocr_is_plain_text(self) -> None:
        payload = {"result": {"markdown": {"text": "# Заголовок"}}}
        self.assertEqual(app.flatten_ocr_result(json.dumps(payload)), "Заголовок")


class PromptAndLlmLogTests(unittest.TestCase):
    def test_prompt_uses_current_object_fields_and_removes_comments(self) -> None:
        objects = [
            {
                "type": "table",
                "regions": [
                    {
                        "kind": "region",
                        "field": "title",
                        "page": 1,
                        "rect": [0, 0, 1, 0.1],
                        "rotation": 0,
                        "status": "recognized",
                        "text": "Таблица 1 <!-- внутренний комментарий -->",
                    },
                    {
                        "kind": "table",
                        "field": "body",
                        "page": 1,
                        "rect": [0, 0.1, 1, 0.9],
                        "rotation": 0,
                        "status": "recognized",
                        "grid": {
                            "x": [0, 1],
                            "y": [0, 1],
                            "statuses": [["recognized"]],
                        },
                        "text": "| 1 |\n| --- |\n| Насос |",
                    },
                ],
            }
        ]
        markdown = app.serialize_markdown(objects, page_count=1)
        instruction, message = app.render_prompt(
            app.DIGITIZER_CONFIG,
            markdown,
            0,
            "table",
            "body",
            "Нас0с <!-- удалить -->",
        )
        self.assertIn("конкретной ячейки", instruction)
        self.assertIn("Таблица 1", message)
        self.assertIn("| Насос |", message)
        self.assertIn("Нас0с", message)
        self.assertNotIn("<!--", message)
        self.assertNotIn("digitizer:", message)

    def test_llm_jsonl_log_appends_request_and_response(self) -> None:
        class FakeResponse:
            status_code = 200
            text = ""

            def __init__(self, body: dict[str, object]) -> None:
                self.body = body

            def json(self) -> dict[str, object]:
                return self.body

            def raise_for_status(self) -> None:
                return None

        class FakeHttpClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def __enter__(self) -> "FakeHttpClient":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def get(self, url: str) -> FakeResponse:
                return FakeResponse({"data": [{"id": "test-model"}]})

            def post(self, url: str, json: dict[str, object]) -> FakeResponse:
                return FakeResponse(
                    {"choices": [{"message": {"content": "Исправлено"}}]}
                )

        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "llm.log.jsonl"
            client = app.OpenAICompatibleClient("http://llm/v1", log_path=log_path)
            with mock.patch("httpx.Client", FakeHttpClient):
                self.assertEqual(
                    client.correct("Инструкция", "Сообщение"), "Исправлено"
                )
                self.assertEqual(
                    client.correct("Инструкция 2", "Сообщение 2"), "Исправлено"
                )
            entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["request"]["messages"][0]["content"], "Инструкция")
        self.assertEqual(entries[0]["response"]["status"], 200)
        self.assertEqual(entries[1]["request"]["messages"][1]["content"], "Сообщение 2")


class StoreAndApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.pdf = self.root / "document.pdf"
        self.pdf.write_bytes(b"%PDF- test fixture")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_store_writes_only_markdown_sidecar(self) -> None:
        store = app.DocumentStore()
        markdown = app.serialize_markdown(
            [
                {
                    "type": "text",
                    "regions": [
                        {
                            "kind": "region",
                            "field": "text",
                            "page": 1,
                            "rect": [0, 0, 0.5, 0.5],
                            "rotation": 0,
                            "status": "recognized",
                            "text": "Текст",
                        }
                    ],
                }
            ],
            page_count=2,
        )
        with mock.patch.object(store, "page_count", return_value=2):
            store.save(self.pdf, markdown)
        self.assertTrue(Path(str(self.pdf) + ".digitizer.md").is_file())
        self.assertFalse(Path(str(self.pdf) + ".digitizer.json").exists())
        with mock.patch.object(store, "page_count", return_value=2):
            self.assertEqual(store.load(self.pdf)["markdown"], markdown)

    def test_api_ocr_is_universal_and_ai_result_is_verbatim(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("FastAPI test client is unavailable")
        paddle, llm = FakePaddle(), FakeLlm()
        application = app.create_app(
            app.SourceRegistry(self.root / "data"),
            app.DocumentStore(),
            paddle,
            llm,
            Path(__file__).with_name("app.html"),
        )
        client = TestClient(application)
        with mock.patch.object(app, "render_crop", return_value=b"png image"):
            ocr = client.post(
                "/api/ocr",
                json={
                    "source": str(self.pdf),
                    "page": 1,
                    "rect": [0, 0, 0.5, 0.5],
                    "rotation": 0,
                    "type": "ignored",
                },
            )
        self.assertEqual(ocr.json(), {"text": "OCR result"})
        self.assertEqual(len(paddle.images), 1)
        corrected = client.post(
            "/api/correct",
            json={
                "type": "text",
                "field": "text",
                "text": "raw OCR",
                "objectIndex": 0,
                "markdown": app.serialize_markdown(
                    [
                        {
                            "type": "text",
                            "regions": [
                                {
                                    "kind": "region",
                                    "field": "text",
                                    "page": 1,
                                    "rect": [0, 0, 0.5, 0.5],
                                    "rotation": 0,
                                    "status": "recognized",
                                    "text": "raw OCR",
                                }
                            ],
                        }
                    ],
                    page_count=1,
                ),
            },
        )
        self.assertEqual(corrected.json()["text"], "```raw model answer```")
        self.assertIn("очевидные ошибки OCR", llm.calls[0][0])
        self.assertEqual(llm.calls[0][1], "raw OCR")


if __name__ == "__main__":
    unittest.main()
