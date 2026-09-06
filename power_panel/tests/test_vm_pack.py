import tempfile
import unittest
from pathlib import Path

from vm_pack import detect_launcher, required_java_major


class LauncherTests(unittest.TestCase):
    def test_selects_the_expected_java_runtime_for_minecraft_versions(self):
        self.assertEqual(required_java_major("1.12.2"), 8)
        self.assertEqual(required_java_major("1.20.1"), 17)
        self.assertEqual(required_java_major("1.21.4"), 21)
        self.assertEqual(required_java_major("26.1.2"), 25)

    def test_prefers_known_shell_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pack_dir = Path(temp_dir)
            (pack_dir / "start.sh").write_text("#!/usr/bin/env bash\n")

            self.assertEqual(detect_launcher(pack_dir), ("shell", "start.sh"))

    def test_supports_fabric_server_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pack_dir = Path(temp_dir)
            (pack_dir / "fabric-server-launch.jar").write_bytes(b"jar")

            self.assertEqual(detect_launcher(pack_dir), ("java", "fabric-server-launch.jar"))

    def test_rejects_unknown_layouts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                detect_launcher(Path(temp_dir))


if __name__ == "__main__":
    unittest.main()
