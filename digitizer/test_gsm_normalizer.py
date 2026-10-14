from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gsm_normalizer import GsmNormalizer, RelationshipStore, normalize_text


class GsmNormalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.catalog = self.root / "brands.csv"
        self.catalog.write_text(
            "Марка ГСМ;Нормативный документ\n"
            "Т-1;ГОСТ 10227-86\n"
            "Т-2;ГОСТ 10227-86\n"
            "Т-1;ТУ 38.1011025-85\n"
            "ТС-1;ГОСТ 10227-86\n"
            "Jet A-1;ASTM D1655\n",
            encoding="utf-8",
        )
        self.normalizer = GsmNormalizer(self.catalog)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_shared_document_is_applied_without_losing_any_brand(self) -> None:
        result = self.normalizer.analyze("Т-1, Т-2; ГОСТ 10227-86")
        self.assertEqual(
            result["text"],
            "Т-1 по ГОСТ 10227-86; Т-2 по ГОСТ 10227-86",
        )
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(result["resolved"])

    def test_ambiguous_ocr_value_is_not_silently_replaced(self) -> None:
        result = self.normalizer.analyze("Т-3 по ГОСТ 10227-86")
        self.assertEqual(result["text"], "- по ГОСТ 10227-86")
        self.assertFalse(result["resolved"])
        self.assertEqual(result["items"][0]["source"], "Т-3 по ГОСТ 10227-86")
        self.assertGreaterEqual(len(result["items"][0]["candidates"]), 2)

    def test_brand_with_multiple_documents_requires_document_choice(self) -> None:
        result = self.normalizer.analyze("Т-1")
        self.assertEqual(result["text"], "- по -")
        self.assertFalse(result["resolved"])

    def test_unique_brand_supplies_its_only_document(self) -> None:
        self.assertEqual(
            self.normalizer.normalize_text("Jet A-1"),
            "Jet A-1 по ASTM D1655",
        )

    def test_document_without_brand_stays_as_unresolved_item(self) -> None:
        result = self.normalizer.analyze("ГОСТ 10227-86")
        self.assertEqual(result["text"], "- по ГОСТ 10227-86")
        self.assertFalse(result["resolved"])

    def test_simple_string_api_and_ocr_confusable(self) -> None:
        self.assertEqual(
            normalize_text("TC-I ГОСТ 10227-86", self.catalog),
            "ТС-1 по ГОСТ 10227-86",
        )

    def test_strict_parser_accepts_only_catalog_pairs(self) -> None:
        parsed = self.normalizer.parse_strict(
            "Т-1 по ГОСТ 10227-86; Jet A-1 по ASTM D1655"
        )
        self.assertEqual([entry.brand for entry in parsed], ["Т-1", "Jet A-1"])
        with self.assertRaisesRegex(ValueError, "не выбрана марка"):
            self.normalizer.parse_strict("- по ГОСТ 10227-86")


class RelationshipStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        catalog = self.root / "brands.csv"
        catalog.write_text(
            "Марка ГСМ,Нормативный документ\n"
            "Основная,ГОСТ 1\n"
            "Дубль,ГОСТ 2\n"
            "Резерв,ГОСТ 3\n",
            encoding="utf-8",
        )
        self.normalizer = GsmNormalizer(catalog)
        self.path = self.root / "relations.csv"
        self.store = RelationshipStore(self.path, self.normalizer)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_duplicate_and_reserve_creates_two_edges_and_deduplicates(self) -> None:
        rows = [["Основная по ГОСТ 1", "Дубль по ГОСТ 2"]]
        first = self.store.append(rows, ["primary", "duplicate_reserve"])
        second = self.store.append(rows, ["primary", "duplicate_reserve"])
        self.assertEqual(first["added"], 2)
        self.assertEqual(second["added"], 0)
        with self.path.open(encoding="utf-8-sig", newline="") as stream:
            saved = list(csv.reader(stream, delimiter=";"))
        self.assertEqual(
            saved,
            [
                ["Первая марка", "Тип связи", "Вторая марка"],
                ["Основная", "дублирующая", "Дубль"],
                ["Основная", "резервная", "Дубль"],
            ],
        )

    def test_relation_export_rejects_unnormalized_cells(self) -> None:
        with self.assertRaisesRegex(ValueError, "Строка 1, столбец 2"):
            self.store.build(
                [["Основная по ГОСТ 1", "Дубль"]],
                ["primary", "duplicate"],
            )


if __name__ == "__main__":
    unittest.main()
