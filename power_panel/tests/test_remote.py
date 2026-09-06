import json
import unittest

from remote import pack_manager_command


class RemoteCommandTests(unittest.TestCase):
    def test_install_command_passes_json_as_base64_not_shell_text(self):
        manifest = {"project_name": "Pack; rm -rf /", "server_download_url": "https://example.test/server.zip"}

        command = pack_manager_command("install", manifest)

        self.assertIn("pack_manager.py install", command)
        self.assertNotIn("Pack; rm -rf", command)
        self.assertIn("base64", command)

    def test_activate_command_uses_the_pinned_pack_id(self):
        command = pack_manager_command("activate", {"server_file_id": 201})

        self.assertEqual(command, "python3 /opt/minecraft-booter/pack_manager.py activate 201")


if __name__ == "__main__":
    unittest.main()
