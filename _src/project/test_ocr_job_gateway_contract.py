#!/usr/bin/env python3
"""Dependency-light contract test for the local RAGFlow OCR job API."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from ocr_job_gateway import JobManager, create_app


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
                    }
                ],
            }
        }


class FakePipeline:
    def predict(self, path: str, **options):
        data = Path(path).read_bytes()
        if b"FAIL" in data:
            raise RuntimeError("intentional fake predictor failure")
        assert options["use_table_recognition"] is False
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
    with tempfile.TemporaryDirectory(prefix="local-ocr-contract-") as temporary:
        manager = JobManager(
            FakePipeline(),
            Path(temporary),
            "http://testserver",
            {"strict_gpu": "fake-contract-test"},
        )
        try:
            with TestClient(create_app(manager, "local")) as client:
                unauth = client.post(
                    "/api/v2/ocr/jobs",
                    data={"model": "PP-StructureV3", "optionalPayload": "{}"},
                    files={"file": ("document.pdf", b"\x89PNG\r\n\x1a\nDATA")},
                )
                assert unauth.status_code == 401, unauth.text

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

                failed = client.post(
                    "/api/v2/ocr/jobs",
                    headers={"Authorization": "Bearer local"},
                    data={"model": "PP-StructureV3", "optionalPayload": "{}"},
                    files={
                        "file": ("document.pdf", b"\x89PNG\r\n\x1a\nFAIL")
                    },
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
