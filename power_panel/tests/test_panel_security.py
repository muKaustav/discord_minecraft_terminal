import importlib
import os
import tempfile
import unittest
from pathlib import Path


class PanelSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ["MODPACK_CATALOG_PATH"] = str(Path(cls.temp_dir.name) / "panel.sqlite3")
        os.environ["POWER_PASSWORD"] = "test-password"
        os.environ["FLASK_SECRET_KEY"] = "test-secret"
        cls.module = importlib.import_module("app")
        cls.module.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def client_with_session(self):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session["ok"] = True
            session["csrf_token"] = "token"
        return client

    def test_start_requires_a_csrf_token_before_it_can_call_aws(self):
        response = self.client_with_session().post("/api/start")

        self.assertEqual(response.status_code, 400)

    def test_login_rejects_a_missing_csrf_token(self):
        response = self.module.app.test_client().post("/login", data={"password": "test-password"})

        self.assertEqual(response.status_code, 400)

    def test_selecting_a_pack_offline_requires_a_csrf_token(self):
        self.module.catalog.upsert_pack(
            {
                "project_id": 1, "project_name": "Test", "slug": "test", "client_file_id": 2,
                "client_file_name": "v1", "client_url": "https://example.test", "server_file_id": 3,
                "server_file_name": "server.zip", "minecraft_version": "1.21", "loader": "NeoForge",
            },
            state="ready",
        )
        response = self.client_with_session().post("/api/modpacks/select", json={"pack_id": 1})

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
