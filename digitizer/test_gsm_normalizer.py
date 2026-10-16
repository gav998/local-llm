from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from gsm_normalizer import (
    RELATION_HEADERS,
    GsmNormalizer,
    RelationshipStore,
    normalize_text,
)


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
        self.assertTrue(result["items"][0]["catalogMissing"])
        self.assertEqual(
            result["items"][0]["proposal"],
            {
                "brand": "Т-3",
                "document": "ГОСТ 10227-86",
                "value": "Т-3 по ГОСТ 10227-86",
            },
        )
        self.assertGreaterEqual(len(result["items"][0]["candidates"]), 2)

    def test_user_can_append_missing_pair_to_catalog(self) -> None:
        saved = self.normalizer.append_entry("Т-3", "ГОСТ 10227-86")
        self.assertTrue(saved["added"])
        self.assertEqual(saved["total"], 6)
        result = self.normalizer.analyze("Т-3 по ГОСТ 10227-86")
        self.assertTrue(result["resolved"])
        self.assertEqual(result["text"], "Т-3 по ГОСТ 10227-86")
        self.assertFalse(
            self.normalizer.append_entry("т-3", "ГОСТ 10227-86")["added"]
        )

    def test_unknown_document_is_offered_for_catalog_append(self) -> None:
        result = self.normalizer.analyze("Новая-1, Новая-2 по СТО 999")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(
            [item["proposal"]["value"] for item in result["items"]],
            ["Новая-1 по СТО 999", "Новая-2 по СТО 999"],
        )
        self.assertTrue(all(item["catalogMissing"] for item in result["items"]))

    def test_strict_parser_ignores_cell_ocr_comment(self) -> None:
        parsed = self.normalizer.parse_strict(
            'Т-1 по ГОСТ 10227-86 <!-- digitizer:ocr "Т-1 ГОСТ" -->'
        )
        self.assertEqual([entry.value for entry in parsed], ["Т-1 по ГОСТ 10227-86"])

    def test_unresolved_value_survives_repeated_normalization(self) -> None:
        source = "- по ГОСТ 10227-86; Jet A-1 по ASTM D1655"
        result = self.normalizer.analyze(source)
        self.assertEqual(result["text"], source)
        self.assertEqual(len(result["items"]), 2)
        self.assertFalse(result["resolved"])
        self.assertEqual(
            self.normalizer.normalize_text("- по -; - по ГОСТ 10227-86"),
            "- по -; - по ГОСТ 10227-86",
        )

    def test_multiple_brands_and_documents_expand_to_individual_values(self) -> None:
        result = self.normalizer.analyze(
            "Т-1, Т-2 по ГОСТ 10227-86\n"
            "Т-1 по ГОСТ 10227-86 и ТУ 38.1011025-85"
        )
        self.assertEqual(
            result["text"],
            "Т-1 по ГОСТ 10227-86; Т-2 по ГОСТ 10227-86; "
            "Т-1 по ГОСТ 10227-86; Т-1 по ТУ 38.1011025-85",
        )
        self.assertTrue(result["resolved"])
        self.assertEqual(
            self.normalizer.normalize_text(
                "Т-1 по ТУ 38.1011025-85 и ГОСТ 10227-86"
            ),
            "Т-1 по ТУ 38.1011025-85; Т-1 по ГОСТ 10227-86",
        )

    def test_parenthetical_conjunction_is_not_a_brand_separator(self) -> None:
        catalog = self.root / "qualified-brands.csv"
        catalog.write_text(
            "Марка ГСМ;Нормативный документ\n"
            "марка 1 (классов А и В);ГОСТ 1234\n"
            "марка 2 (классов А и В);ГОСТ 1234\n",
            encoding="utf-8",
        )
        result = GsmNormalizer(catalog).analyze(
            "марка 1 и марка 2 (классов А и В) по ГОСТ 1234;"
        )
        self.assertEqual(
            result["text"],
            "марка 1 (классов А и В) по ГОСТ 1234; "
            "марка 2 (классов А и В) по ГОСТ 1234",
        )
        self.assertTrue(result["resolved"])

    def test_unresolved_candidates_include_entire_sorted_catalog(self) -> None:
        result = self.normalizer.analyze("Неизвестная по ASTM D1655")
        candidates = result["items"][0]["candidates"]
        self.assertEqual(len(candidates), 5)
        self.assertEqual(candidates[0]["value"], "Jet A-1 по ASTM D1655")
        self.assertEqual(
            [item["value"] for item in candidates[1:]],
            sorted(
                [item["value"] for item in candidates[1:]],
                key=str.casefold,
            ),
        )

    def test_catalog_is_reloaded_after_external_edit(self) -> None:
        self.assertEqual(self.normalizer.normalize_text("Новая"), "- по -")
        with self.catalog.open("a", encoding="utf-8") as stream:
            stream.write("Новая;СТО 99\n")
        self.assertEqual(
            self.normalizer.normalize_text("Новая"), "Новая по СТО 99"
        )

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
        self.assertEqual(first["added"], 2)
        with self.path.open(encoding="utf-8-sig", newline="") as stream:
            saved = list(csv.reader(stream, delimiter=";"))
        saved[1][3] = "ручное примечание"
        with self.path.open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream, delimiter=";", lineterminator="\n").writerows(saved)
        second = self.store.append(rows, ["primary", "duplicate_reserve"])
        self.assertEqual(second["added"], 0)
        with self.path.open(encoding="utf-8-sig", newline="") as stream:
            saved = list(csv.reader(stream, delimiter=";"))
        self.assertEqual(
            saved,
            [
                ["Первая марка", "Тип связи", "Вторая марка", "Примечание"],
                [
                    "Основная по ГОСТ 1",
                    "дублирующая",
                    "Дубль по ГОСТ 2",
                    "ручное примечание",
                ],
                [
                    "Основная по ГОСТ 1",
                    "резервная",
                    "Дубль по ГОСТ 2",
                    "",
                ],
            ],
        )

    def test_relation_export_rejects_unnormalized_cells(self) -> None:
        with self.assertRaisesRegex(ValueError, "Строка 1, столбец 2"):
            self.store.build(
                [["Основная по ГОСТ 1", "Дубль"]],
                ["primary", "duplicate"],
            )

    def test_status_matrix_distinguishes_catalog_and_relation_misses(self) -> None:
        rows = [
            ["Основная по ГОСТ 1", "Дубль по ГОСТ 2"],
            ["Основная по ГОСТ 1", "Неизвестная по ГОСТ 9"],
        ]
        column_types = ["primary", "duplicate"]
        self.assertEqual(
            self.store.status_matrix(rows, column_types),
            [
                ["relation-missing", "relation-missing"],
                ["relation-missing", "brand-missing"],
            ],
        )
        self.store.append([rows[0]], column_types)
        self.assertEqual(
            self.store.status_matrix([rows[0]], column_types),
            [["linked", "linked"]],
        )

    def test_legacy_three_column_csv_gains_empty_notes_column(self) -> None:
        self.path.write_text(
            "Первая марка;Тип связи;Вторая марка\n"
            "Старая;дублирующая;Запись\n",
            encoding="utf-8",
        )
        self.store.append(
            [["Основная по ГОСТ 1", "Резерв по ГОСТ 3"]],
            ["primary", "reserve"],
        )
        with self.path.open(encoding="utf-8-sig", newline="") as stream:
            saved = list(csv.reader(stream, delimiter=";"))
        self.assertEqual(saved[0], [*RELATION_HEADERS])
        self.assertEqual(saved[1], ["Старая", "дублирующая", "Запись", ""])


if __name__ == "__main__":
    unittest.main()
