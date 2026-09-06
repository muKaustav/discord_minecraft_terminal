import tempfile
import unittest
from pathlib import Path

from catalog import PackCatalog


PACK = {
    "project_id": 123,
    "project_name": "Better MC",
    "slug": "better-mc",
    "client_file_id": 200,
    "client_file_name": "v2",
    "client_url": "https://www.curseforge.com/minecraft/modpacks/better-mc/files/200",
    "server_file_id": 201,
    "server_file_name": "server-v2.zip",
    "minecraft_version": "1.21.4",
    "loader": "NeoForge",
}


class PackCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.catalog = PackCatalog(Path(self.temp_dir.name) / "packs.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_adds_a_pinned_pack_and_marks_it_ready(self):
        pack = self.catalog.upsert_pack(PACK, state="ready")

        self.assertEqual(pack["server_file_id"], 201)
        self.assertEqual(self.catalog.list_packs()[0]["state"], "ready")

    def test_selects_only_ready_packs(self):
        pack = self.catalog.upsert_pack(PACK, state="ready")

        self.catalog.select_pack(pack["id"])

        self.assertEqual(self.catalog.active_pack()["id"], pack["id"])

    def test_refuses_to_select_a_pack_still_installing(self):
        pack = self.catalog.upsert_pack(PACK, state="installing")

        with self.assertRaises(ValueError):
            self.catalog.select_pack(pack["id"])


if __name__ == "__main__":
    unittest.main()
