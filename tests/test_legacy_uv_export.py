import json
import importlib.util
import struct
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universal_spp_plugin" / "lib" / "legacy_uv_export.py"
SPEC = importlib.util.spec_from_file_location("uspp_legacy_uv_export", MODULE_PATH)
legacy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(legacy)


def synthetic_painter_exe(guard=b"\x74\x7e"):
    data = bytearray(0x900)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x86, 2)
    struct.pack_into("<H", data, 0x94, 0xF0)
    section_table = 0x80 + 24 + 0xF0

    def section(index, name, virtual_address, raw_offset, raw_size):
        offset = section_table + index * 40
        data[offset:offset + 8] = name.ljust(8, b"\0")
        struct.pack_into("<IIII", data, offset + 8,
                         raw_size, virtual_address, raw_size, raw_offset)

    section(0, b".text", 0x1000, 0x400, 0x200)
    section(1, b".rdata", 0x2000, 0x600, 0x300)
    string_raw = 0x620
    data[string_raw:string_raw + len(legacy.UV_TILE_ERROR)] = legacy.UV_TILE_ERROR

    guard_raw = 0x440
    reference_raw = guard_raw + 9
    guard_rva = 0x1000 + guard_raw - 0x400
    reference_rva = 0x1000 + reference_raw - 0x400
    string_rva = 0x2000 + string_raw - 0x600
    data[guard_raw:guard_raw + 2] = guard
    data[reference_raw:reference_raw + 3] = b"\x4c\x8d\x05"
    struct.pack_into("<i", data, reference_raw + 3, string_rva - (reference_rva + 7))
    return bytes(data), guard_rva


class LegacyUvExportTests(unittest.TestCase):
    def test_finds_guard_from_pe_string_reference(self):
        payload, expected_rva = synthetic_painter_exe()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "Painter.exe"
            path.write_bytes(payload)
            rva, original = legacy.find_guard(str(path))
        self.assertEqual(rva, expected_rva)
        self.assertEqual(original, b"\x74\x7e")

    def test_rejects_non_je_guard(self):
        payload, _expected_rva = synthetic_painter_exe(b"\x75\x7e")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "Painter.exe"
            path.write_bytes(payload)
            with self.assertRaisesRegex(ValueError, "guard branch"):
                legacy.find_guard(str(path))

    def test_expands_numbered_uv_tile_assets(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "layer.1002.png").write_bytes(b"two")
            (base / "layer.1001.png").write_bytes(b"one")
            manifest_path = base / "manifest.json"
            manifest_path.write_text(json.dumps({
                "assets": [{"request_id": "rf_layer", "path": "layer.png"}],
            }), encoding="utf-8")

            count = legacy.expand_manifest_uv_tiles(str(manifest_path))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(count, 2)
        self.assertEqual(
            [(a["uv_tile"], a["path"]) for a in manifest["assets"]],
            [(1001, "layer.1001.png"), (1002, "layer.1002.png")],
        )


if __name__ == "__main__":
    unittest.main()
