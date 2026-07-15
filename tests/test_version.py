import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = ROOT / "universal_spp_plugin" / "lib" / "version.py"


def load_version():
    spec = importlib.util.spec_from_file_location("uspp_plugin_version_test", VERSION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PluginVersionTests(unittest.TestCase):
    def test_native_painter_binary_names(self):
        version = load_version()
        self.assertEqual(version.painter_binary_names("nt"), ("Adobe Substance 3D Painter.exe",))
        self.assertEqual(version.painter_binary_names("posix")[0], "Adobe Substance 3D Painter")

    def test_finds_extensionless_linux_binary_from_embedded_python_path(self):
        version = load_version()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "Adobe Substance 3D Painter v12.1"
            python = root / "resources" / "python" / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"")
            painter = root / "Adobe Substance 3D Painter"
            painter.write_bytes(b"\x7fELF")
            with mock.patch.object(version, "painter_binary_names", return_value=(painter.name,)), \
                    mock.patch.object(version.sys, "platform", "test"), \
                    mock.patch.object(version.sys, "executable", str(python)):
                found = version.running_binary()
        self.assertEqual(found, os.path.realpath(painter))


if __name__ == "__main__":
    unittest.main()
