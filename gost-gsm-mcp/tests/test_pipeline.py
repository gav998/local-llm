from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from gsm_gost.pipeline import (
    DEFAULT_CONFIG,
    GostGsmPipeline,
    parse_brand_item,
    split_cell_items,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_brand_parser_preserves_raw_and_extracts_normative_document(self) -> None:
        parsed = parse_brand_item("Масло М-8 по ГОСТ 10541-2020", DEFAULT_CONFIG)

        self.assertEqual(parsed["brand"], "Масло М-8")
        self.assertEqual(parsed["normative_document"], "ГОСТ 10541-2020")
        self.assertEqual(parsed["raw_item"], "Масло М-8 по ГОСТ 10541-2020")
        self.assertNotIn("normdoc_not_found", parsed["review_flags"])

    def test_cell_split_does_not_split_on_comma(self) -> None:
        raw = "Масло А, всесезонное по ГОСТ 1; Масло Б по ТУ 2"
        self.assertEqual(
            split_cell_items(raw, DEFAULT_CONFIG),
            ["Масло А, всесезонное по ГОСТ 1", "Масло Б по ТУ 2"],
        )

    def test_separate_normative_document_is_traceable(self) -> None:
        parsed = parse_brand_item("Масло А", DEFAULT_CONFIG, "ГОСТ 1")
        self.assertEqual(parsed["normative_document"], "ГОСТ 1")
        self.assertIn("normdoc_from_separate_column", parsed["review_flags"])


class PipelineTests(unittest.TestCase):
    def make_pipeline(self) -> tuple[tempfile.TemporaryDirectory[str], GostGsmPipeline]:
        temporary = tempfile.TemporaryDirectory()
        return temporary, GostGsmPipeline(Path(temporary.name) / "job")

    def test_scan_reports_table_candidates(self) -> None:
        temporary, pipeline = self.make_pipeline()
        self.addCleanup(temporary.cleanup)

        result = pipeline.scan_source(FIXTURES / "sample.html")

        self.assertEqual(result["brand_candidates"], ["T000001"])
        self.assertEqual(result["purpose_candidates"], ["T000002"])
        self.assertEqual(result["tables"][0]["brand_candidate"]["header_depth"], 2)

    def test_html_pipeline_to_three_csv_files(self) -> None:
        temporary, pipeline = self.make_pipeline()
        self.addCleanup(temporary.cleanup)

        initialized = pipeline.initialize(FIXTURES / "sample.html")
        self.assertEqual(initialized["total_rows"], 2)
        self.assertEqual(initialized["total_nonempty_brand_cells"], 6)

        first = pipeline.process_next_batch(batch_size=1)
        self.assertEqual(first["batch_id"], "B000001")
        self.assertEqual(first["parse"]["item_count"], 4)
        second = pipeline.process_next_batch(batch_size=1)
        self.assertEqual(second["batch_id"], "B000002")
        self.assertEqual(second["parse"]["item_count"], 4)
        self.assertEqual(pipeline.progress()["status"], "completed")

        validation = pipeline.validate_final()
        self.assertTrue(validation["valid"], validation["blockers"])
        exported = pipeline.export_final_csv()
        self.assertEqual(
            exported["row_counts"],
            {
                "01_gsm_brands.csv": 8,
                "02_gsm_group_brand.csv": 8,
                "03_gsm_relations.csv": 5,
            },
        )

        with pipeline.paths["out3"].open(encoding="utf-8", newline="") as stream:
            relation_rows = list(csv.DictReader(stream, delimiter=";"))
        self.assertEqual(
            {row["Тип связи"] for row in relation_rows},
            {"Дублирующая", "Резервная", "Зарубежная"},
        )
        with pipeline.paths["out1"].open(encoding="utf-8", newline="") as stream:
            brands = list(csv.DictReader(stream, delimiter=";"))
        oil_a = next(row for row in brands if row["Марка ГСМ"] == "Масло А")
        self.assertEqual(oil_a["Нормативный документ"], "ГОСТ 1-2020")
        self.assertEqual(oil_a["Наземная техника"], "+")
        self.assertEqual(oil_a["Авиационная техника"], "-")

    def test_batch_staging_is_idempotent(self) -> None:
        temporary, pipeline = self.make_pipeline()
        self.addCleanup(temporary.cleanup)
        pipeline.initialize(FIXTURES / "sample.html")
        result = pipeline.process_next_batch(batch_size=2)
        batch_id = result["batch_id"]
        before = pipeline.paths["table1"].read_text(encoding="utf-8")

        pipeline.stage_batch(batch_id)
        after = pipeline.paths["table1"].read_text(encoding="utf-8")

        self.assertEqual(before, after)
        self.assertEqual(len(after.splitlines()), 8)

    def test_ambiguous_relation_requires_and_accepts_override(self) -> None:
        temporary, pipeline = self.make_pipeline()
        self.addCleanup(temporary.cleanup)
        pipeline.initialize(FIXTURES / "ambiguous.html")
        processed = pipeline.process_next_batch()
        self.assertEqual(processed["relations"]["unresolved_count"], 1)
        self.assertIn(
            "ambiguous_relation_mapping",
            pipeline.list_review_flags()["categories"],
        )
        self.assertFalse(pipeline.validate_final()["valid"])

        detail = pipeline.row_detail("R000001")
        main = next(
            item for item in detail["parsed_row"]["items"] if item["role"] == "main"
        )
        related = next(
            item
            for item in detail["parsed_row"]["items"]
            if item["role"] == "duplicate"
        )
        override = pipeline.add_relation_override(
            "B000001", main["item_id"], related["item_id"], "Дублирующая"
        )

        self.assertEqual(override["qc"]["qc_status"], "passed")
        self.assertNotIn(
            "ambiguous_relation_mapping",
            pipeline.list_review_flags()["categories"],
        )
        self.assertTrue(pipeline.validate_final()["valid"])

    def test_ambiguous_purpose_accepts_source_row_override(self) -> None:
        temporary, pipeline = self.make_pipeline()
        self.addCleanup(temporary.cleanup)
        pipeline.initialize(FIXTURES / "purpose_ambiguous.html")
        pipeline.process_next_batch()
        detail = pipeline.row_detail("R000001")
        item_id = detail["parsed_row"]["items"][0]["item_id"]
        candidates = pipeline.find_purpose_candidates(item_id=item_id)
        self.assertEqual(len(candidates["candidates"]), 2)
        self.assertFalse(pipeline.validate_final()["valid"])

        result = pipeline.set_purpose_override(
            "B000001",
            item_id,
            purpose_row_id=candidates["candidates"][0]["purpose_row_id"],
        )

        self.assertEqual(result["qc"]["qc_status"], "passed")
        self.assertTrue(pipeline.validate_final()["valid"])

    def test_item_override_keeps_raw_ocr_and_rebuilds_downstream_data(self) -> None:
        temporary, pipeline = self.make_pipeline()
        self.addCleanup(temporary.cleanup)
        pipeline.initialize(FIXTURES / "sample.html")
        pipeline.process_next_batch(batch_size=1)
        detail = pipeline.row_detail("R000001")
        item = next(
            item for item in detail["parsed_row"]["items"] if item["brand"] == "Масло Б"
        )

        result = pipeline.set_item_override(
            "B000001",
            item["item_id"],
            brand="Масло Б исправленное",
            source_ref="проверено по изображению страницы 1",
        )

        self.assertEqual(result["qc"]["qc_status"], "passed_with_flags")
        changed = pipeline.row_detail("R000001")
        effective_item = next(
            value
            for value in changed["parsed_row"]["items"]
            if value["item_id"] == item["item_id"]
        )
        original_item = next(
            value
            for value in changed["original_parsed_row"]["items"]
            if value["item_id"] == item["item_id"]
        )
        self.assertEqual(effective_item["brand"], "Масло Б исправленное")
        self.assertEqual(original_item["brand"], "Масло Б")
        relation = changed["relations"]["resolved"][0]
        self.assertEqual(relation["related_brand"], "Масло Б исправленное")

        pipeline.clear_override(
            "B000001", item_id=item["item_id"], clear_item_value=True
        )
        restored = pipeline.row_detail("R000001")
        restored_item = next(
            value
            for value in restored["parsed_row"]["items"]
            if value["item_id"] == item["item_id"]
        )
        self.assertEqual(restored_item["brand"], "Масло Б")

    def test_markdown_tables_are_supported(self) -> None:
        temporary, pipeline = self.make_pipeline()
        self.addCleanup(temporary.cleanup)
        initialized = pipeline.initialize(FIXTURES / "sample.md")
        self.assertEqual(initialized["total_rows"], 1)

        result = pipeline.process_batches(max_batches=10)

        self.assertEqual(result["progress"]["status"], "completed")
        export = pipeline.export_final_csv()
        self.assertEqual(export["row_counts"]["03_gsm_relations.csv"], 1)

    def test_repeated_physical_tables_form_one_logical_manifest(self) -> None:
        temporary, pipeline = self.make_pipeline()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name) / "multipage.html"
        header = """
        <tr><th>Группа ГСМ</th><th>Основная марка ГСМ</th>
        <th>Дублирующая марка ГСМ</th><th>Резервная марка ГСМ</th>
        <th>Зарубежная марка ГСМ</th></tr>
        """
        source.write_text(
            f"""<html><body>
            <table>{header}<tr><td>Топлива</td><td>А по ГОСТ 1</td><td></td><td></td><td></td></tr></table>
            <table>{header}<tr><td></td><td>Б по ГОСТ 2</td><td></td><td></td><td></td></tr></table>
            <table><tr><th>Марка ГСМ</th><th>Нормативный документ</th><th>Назначение и условия применения</th></tr>
            <tr><td>А</td><td>ГОСТ 1</td><td>Назначение А</td></tr>
            <tr><td>Б</td><td>ГОСТ 2</td><td>Назначение Б</td></tr></table>
            </body></html>""",
            encoding="utf-8",
        )

        initialized = pipeline.initialize(source)
        rows = pipeline.paths["source_rows"].read_text(encoding="utf-8").splitlines()

        self.assertEqual(initialized["brand_table_ids"], ["T000001", "T000002"])
        self.assertEqual(initialized["total_rows"], 2)
        self.assertEqual(json.loads(rows[1])["group"], "Топлива")

    def test_force_reinitialization_clears_generated_batches(self) -> None:
        temporary, pipeline = self.make_pipeline()
        self.addCleanup(temporary.cleanup)
        pipeline.initialize(FIXTURES / "sample.html")
        pipeline.process_next_batch(batch_size=1)
        self.assertTrue(list(pipeline.paths["batches"].glob("B*.json")))

        initialized = pipeline.initialize(FIXTURES / "sample.md", force=True)

        self.assertEqual(initialized["total_rows"], 1)
        self.assertFalse(list(pipeline.paths["batches"].glob("B*.json")))
        progress = json.loads(pipeline.paths["progress"].read_text(encoding="utf-8"))
        self.assertEqual(progress["processed_rows"], 0)


if __name__ == "__main__":
    unittest.main()
