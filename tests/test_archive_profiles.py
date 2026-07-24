import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class ArchiveProfileTests(unittest.TestCase):
    def test_archive_api_separates_naval_and_defaults_legacy_items_to_jung(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            naval_dir = output_dir / "naval-item"
            jung_dir = output_dir / "legacy-jung-item"
            naval_dir.mkdir()
            jung_dir.mkdir()
            (naval_dir / "metadata.json").write_text(json.dumps({
                "title": "纳瓦尔测试",
                "uploaded": True,
                "archive_profile": "naval",
            }, ensure_ascii=False), encoding="utf-8")
            (jung_dir / "metadata.json").write_text(json.dumps({
                "title": "荣格旧档案",
                "uploaded": True,
            }, ensure_ascii=False), encoding="utf-8")

            with (
                patch.object(server.config, "OUTPUT_DIR", output_dir),
                patch.object(server, "_QUEUE", []),
            ):
                items = server.get_archives()["items"]

        profiles = {item["title"]: item["archive_profile"] for item in items}
        self.assertEqual(profiles["纳瓦尔测试"], "naval")
        self.assertEqual(profiles["荣格旧档案"], "jung")


if __name__ == "__main__":
    unittest.main()
