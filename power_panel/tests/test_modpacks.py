import unittest

from modpacks import (
    CurseForgeClient,
    CurseForgeLinkError,
    choose_server_file,
    parse_curseforge_link,
    safe_archive_member,
)


class CurseForgeLinkTests(unittest.TestCase):
    def test_parses_a_modpack_project_link(self):
        link = parse_curseforge_link("https://www.curseforge.com/minecraft/modpacks/better-mc-bmc6")

        self.assertEqual(link.slug, "better-mc-bmc6")
        self.assertIsNone(link.file_id)

    def test_parses_a_modpack_file_link(self):
        link = parse_curseforge_link(
            "https://www.curseforge.com/minecraft/modpacks/better-mc-bmc6/files/8728668"
        )

        self.assertEqual(link.slug, "better-mc-bmc6")
        self.assertEqual(link.file_id, 8728668)

    def test_rejects_non_curseforge_links(self):
        with self.assertRaises(CurseForgeLinkError):
            parse_curseforge_link("https://example.com/minecraft/modpacks/test")

    def test_rejects_an_invalid_zero_file_id(self):
        with self.assertRaises(CurseForgeLinkError):
            parse_curseforge_link("https://www.curseforge.com/minecraft/modpacks/test/files/0")

    def test_selects_the_latest_release_with_a_server_pack(self):
        files = [
            {"id": 20, "releaseType": 2, "serverPackFileId": 99, "dateReleased": "2026-09-04T00:00:00Z"},
            {"id": 10, "releaseType": 1, "serverPackFileId": 98, "dateReleased": "2026-09-03T00:00:00Z"},
            {"id": 30, "releaseType": 1, "serverPackFileId": 0, "dateReleased": "2026-09-05T00:00:00Z"},
        ]

        self.assertEqual(choose_server_file(files)["id"], 10)


class ArchiveSafetyTests(unittest.TestCase):
    def test_allows_a_normal_server_pack_path(self):
        self.assertTrue(safe_archive_member("mods/example.jar"))

    def test_rejects_archive_path_traversal(self):
        self.assertFalse(safe_archive_member("../../etc/passwd"))
        self.assertFalse(safe_archive_member("/opt/server/start.sh"))


class CurseForgeResolutionTests(unittest.TestCase):
    def test_project_link_resolves_latest_stable_client_and_server_pack(self):
        responses = {
            "/v1/mods/search?gameId=432&classId=4471&slug=better-mc": {
                "data": [{"id": 123, "name": "Better MC", "slug": "better-mc"}]
            },
            "/v1/mods/123/files": {
                "data": [
                    {"id": 100, "releaseType": 1, "serverPackFileId": 101, "dateReleased": "2026-09-01T00:00:00Z", "displayName": "v1", "gameVersions": ["1.21.1"]},
                    {"id": 200, "releaseType": 1, "serverPackFileId": 201, "dateReleased": "2026-09-02T00:00:00Z", "displayName": "v2", "gameVersions": ["NeoForge", "1.21.4"]},
                ]
            },
            "/v1/mods/123/files/201": {"data": {"id": 201, "isServerPack": True, "fileName": "server-v2.zip", "downloadUrl": "https://cdn.example/v2.zip"}},
        }
        client = CurseForgeClient("test-key", lambda path: responses[path])

        resolved = client.resolve(parse_curseforge_link("https://www.curseforge.com/minecraft/modpacks/better-mc"))

        self.assertEqual(resolved["project_id"], 123)
        self.assertEqual(resolved["client_file_id"], 200)
        self.assertEqual(resolved["server_file_id"], 201)
        self.assertEqual(resolved["server_download_url"], "https://cdn.example/v2.zip")
        self.assertEqual(resolved["minecraft_version"], "1.21.4")
        self.assertEqual(resolved["loader"], "NeoForge")


if __name__ == "__main__":
    unittest.main()
