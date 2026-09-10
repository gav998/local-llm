#!/usr/bin/env python3
"""Dependency-light contract test for the local RAGFlow OCR job API."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from ocr_job_gateway import (
    JobManager,
    atomic_json,
    create_app,
    validate_pipeline_profile,
)


class FakeResult:
    @property
    def json(self):
        return {
            "res": {
                "input_path": "must-be-pruned",
                "page_index": 0,
                "parsing_res_list": [
                    {
                        "block_content": "ПОРТАТИВНЫЙ ДОКУМЕНТ 2026",
                        "block_label": "text",
                        "block_bbox": [10, 20, 500, 100],
                    },
                    {
                        "block_content": (
                            "<table><tr><th>Код</th><th>Связь</th></tr>"
                            "<tr><td>A-17</td><td>B-42</td></tr></table>"
                        ),
                        "block_label": "table",
                        "block_bbox": [10, 120, 500, 300],
                    },
                ],
            }
        }


class FakePipeline:
    def predict(self, path: str, **options):
        data = Path(path).read_bytes()
        if b"FAIL" in data:
            raise RuntimeError("intentional fake predictor failure")
        assert options["use_table_recognition"] is True
        assert options["use_ocr_results_with_table_cells"] is False
        return [FakeResult()]

    def close(self):
        pass


def wait_for_state(client: TestClient, job_id: str, state: str) -> dict:
    for _ in range(100):
        response = client.get(
            f"/api/v2/ocr/jobs/{job_id}",
            headers={"Authorization": "Bearer local"},
        )
        response.raise_for_status()
        data = response.json()["data"]
        if data["state"] == state:
            return data
        if data["state"] == "failed" and state != "failed":
            raise AssertionError(data)
        time.sleep(0.02)
    raise AssertionError(f"Job {job_id} did not enter state {state}")


def main() -> int:
    source_config = Path(__file__).with_name("pp-structure-v3-8gb.yaml")
    installed_config = Path(__file__).resolve().parents[2] / "config" / source_config.name
    config_path = source_config if source_config.is_file() else installed_config
    import yaml

    profile = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    validate_pipeline_profile(profile)

    incomplete_profile = dict(profile)
    incomplete_profile.pop("use_doc_unwarping")
    try:
        validate_pipeline_profile(incomplete_profile)
    except RuntimeError as exc:
        assert "use_doc_unwarping: false" in str(exc)
    else:
        raise AssertionError("Missing explicit DocPreprocessor switches must fail closed")

    with tempfile.TemporaryDirectory(prefix="local-ocr-atomic-") as temporary:
        state_path = Path(temporary) / "state.json"
        state_path.write_text('{"state": "queued"}\n', encoding="utf-8")
        real_replace = Path.replace
        replace_attempts = 0

        def transient_windows_lock(source: Path, target: Path) -> Path:
            nonlocal replace_attempts
            replace_attempts += 1
            if replace_attempts <= 2:
                raise PermissionError(13, "transient Windows sharing violation")
            return real_replace(source, target)

        with mock.patch.object(
            Path, "replace", autospec=True, side_effect=transient_windows_lock
        ):
            atomic_json(state_path, {"state": "done"})
        assert replace_attempts == 3
        assert json.loads(state_path.read_text(encoding="utf-8")) == {"state": "done"}

    with tempfile.TemporaryDirectory(prefix="local-ocr-contract-") as temporary:
        manager = JobManager(
            FakePipeline(),
            Path(temporary),
            "http://testserver",
            {"strict_gpu": "fake-contract-test"},
        )
        try:
            with TestClient(create_app(manager, "local")) as client:
                health = client.get("/health")
                health.raise_for_status()
                assert health.json() == {
                    "status": "ready",
                    "strict_gpu": "fake-contract-test",
                }

                unauth = client.post(
                    "/api/v2/ocr/jobs",
                    data={"model": "PP-StructureV3", "optionalPayload": "{}"},
                    files={"file": ("document.pdf", b"\x89PNG\r\n\x1a\nDATA")},
                )
                assert unauth.status_code == 401, unauth.text

                table_disabled = client.post(
                    "/api/v2/ocr/jobs",
                    headers={"Authorization": "Bearer local"},
                    data={
                        "model": "PP-StructureV3",
                        "optionalPayload": json.dumps({"useTableRecognition": False}),
                    },
                    files={"file": ("document.png", b"\x89PNG\r\n\x1a\nDATA")},
                )
                assert table_disabled.status_code == 400, table_disabled.text
                assert "cannot be disabled" in table_disabled.text

                submitted = client.post(
                    "/api/v2/ocr/jobs",
                    headers={"Authorization": "Bearer local"},
                    data={
                        "model": "PP-StructureV3",
                        "optionalPayload": json.dumps(
                            {
                                "prettifyMarkdown": True,
                                "showFormulaNumber": True,
                                "useDocUnwarping": False,
                                "formatBlockContent": True,
                            }
                        ),
                    },
                    # The gateway must inspect magic bytes, not RAGFlow's fixed name.
                    files={"file": ("document.pdf", b"\x89PNG\r\n\x1a\nDATA")},
                )
                submitted.raise_for_status()
                job_id = submitted.json()["data"]["jobId"]
                done = wait_for_state(client, job_id, "done")
                result = client.get(done["resultJsonUrl"])
                result.raise_for_status()  # intentionally no Authorization header
                lines = result.text.strip().splitlines()
                assert len(lines) == 1
                payload = json.loads(lines[0])
                pruned = payload["result"]["layoutParsingResults"][0]["prunedResult"]
                assert "input_path" not in pruned and "page_index" not in pruned
                block = pruned["parsing_res_list"][0]
                assert block["block_content"] and len(block["block_bbox"]) == 4
                table = pruned["parsing_res_list"][1]
                assert table["block_label"] == "table"
                assert "<table>" in table["block_content"]

                failed = client.post(
                    "/api/v2/ocr/jobs",
                    headers={"Authorization": "Bearer local"},
                    data={"model": "PP-StructureV3", "optionalPayload": "{}"},
                    files={"file": ("document.pdf", b"\x89PNG\r\n\x1a\nFAIL")},
                )
                failed.raise_for_status()
                failed_id = failed.json()["data"]["jobId"]
                failure = wait_for_state(client, failed_id, "failed")
                assert "intentional" in failure["errorMsg"]
        finally:
            manager.close()
    print("OCR job gateway contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
