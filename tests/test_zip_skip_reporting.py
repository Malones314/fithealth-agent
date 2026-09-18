from __future__ import annotations

import io
import unittest
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient
from fithealth_agent.runtime import deps

import main


class ZipSkipReportingTest(unittest.TestCase):
    @staticmethod
    def archive() -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("daily/readme.txt", "unsupported")
        return output.getvalue()

    def test_upload_reports_unprocessed_inner_filename_without_backup_classification(self) -> None:
        imported = {
            "filename": "daily.zip",
            "status": "partial",
            "duplicate": False,
            "data_types": [],
            "source_count": 0,
            "warnings": ["压缩包中没有可处理的 FIT 数据"],
            "skipped_files": [{
                "filename": "daily/readme.txt",
                "reason": "不属于可处理的 FIT 数据",
            }],
        }
        with (
            patch.object(deps.health_import_service, "import_file", return_value=imported),
            patch.object(deps, "extract_activity_fits", return_value=[]),
        ):
            response = TestClient(main.app).post(
                "/upload_health",
                files={"files": ("daily.zip", self.archive(), "application/zip")},
            )
        body = response.json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["status"], "partial")
        self.assertEqual(body["unprocessed_files"], [{
            "zip": "daily.zip",
            "filename": "daily/readme.txt",
            "reason": "不属于可处理的 FIT 数据",
        }])
        self.assertIn("daily/readme.txt", body["message"])


if __name__ == "__main__":
    unittest.main()
