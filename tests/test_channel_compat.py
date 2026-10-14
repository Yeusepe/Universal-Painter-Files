import io
import struct
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spp_downgrader" / "spp_extractor"))

from lib.hbo_reserializer import HBOSerializer, runtime  # noqa: E402
from lib.migration_profile import load  # noqa: E402


def serializer():
    return HBOSerializer(struct.pack("<III", 0x1B7C2FDD, 1, 17))


def primitive(code, value, size):
    return ("primitive", code, int(value).to_bytes(size, "little"))


class ChannelCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.saved_runtime = {
            name: getattr(runtime, name)
            for name in (
                "FIELD_VALUE_TRANSFORM",
                "FIELD_RETYPE",
                "FIELD_REKIND",
                "FIELD_RENAME",
                "PRIMITIVE_RETYPE",
            )
        }
        runtime.FIELD_VALUE_TRANSFORM = {}
        runtime.FIELD_RETYPE = {}
        runtime.FIELD_REKIND = {}
        runtime.FIELD_RENAME = {}
        runtime.PRIMITIVE_RETYPE = {}

    def tearDown(self):
        for name, value in self.saved_runtime.items():
            setattr(runtime, name, value)

    def test_custom_channel_enum_maps_to_legacy_user_bit(self):
        runtime.FIELD_VALUE_TRANSFORM = {
            "DataChannel.type": {
                "op": "channel_enum_to_legacy_bitmask",
                "code": 21,
            }
        }
        fields = [("type", 9, primitive(9, 69, 4), False)]

        out = serializer()._apply_field_xforms("DataChannel", fields)

        self.assertEqual(out[0][1], 21)
        self.assertEqual(int.from_bytes(out[0][2][2], "little"), 1 << 19)

    def test_blanket_128_bit_mask_narrowing_preserves_custom_channels(self):
        runtime.PRIMITIVE_RETYPE = {22: 12}
        source_mask = (1 << 0) | (1 << 22) | (1 << 64) | (1 << 69)
        root = ("DataActionFancy", [
            ("channelTypes", 22, primitive(22, source_mask, 16), False),
        ])

        out = serializer()._narrow_primitives(root)

        expected = (1 << 0) | (1 << 22) | (1 << 14) | (1 << 19)
        self.assertEqual(out[1][0][1], 12)
        self.assertEqual(int.from_bytes(out[1][0][2][2], "little"), expected)

    def test_field_retype_uses_same_custom_channel_mapping(self):
        runtime.FIELD_RETYPE = {"DataActionFill.channelTypes": 12}
        source_mask = (1 << 7) | (1 << 65)
        fields = [
            ("channelTypes", 22, primitive(22, source_mask, 16), False),
        ]

        out = serializer()._apply_field_xforms("DataActionFill", fields)

        self.assertEqual(out[0][1], 12)
        self.assertEqual(int.from_bytes(out[0][2][2], "little"), (1 << 7) | (1 << 15))

    def test_solo_channel_uses_enum_semantics_in_composed_1215_downgrade(self):
        profile = load('v12.1_to_v11')
        runtime.FIELD_VALUE_TRANSFORM = profile.field_value_transform
        runtime.FIELD_RETYPE = profile.field_retype
        for source, expected in [(0, 1), (7, 1 << 7), (69, 1 << 19), (0xFFFFFFFF, 0)]:
            with self.subTest(source=source):
                fields = [('channelType', 9, primitive(9, source, 4), False)]
                output = serializer()._apply_field_xforms('SettingsSolo', fields)
                self.assertEqual(output[0][1], 12)
                self.assertEqual(int.from_bytes(output[0][2][2], 'little'), expected)
                self.assertEqual(serializer()._apply_field_xforms('SettingsSolo', output), output)

    def test_export_channel_variants_use_legacy_masks(self):
        runtime.FIELD_VALUE_TRANSFORM = load('v12.1_to_v10').field_value_transform
        for source, expected in [(0, 1), (13, 1 << 13), (64, 1 << 14),
                                 (65, 1 << 15), (74, 1 << 34), (77, 1 << 37)]:
            with self.subTest(source=source):
                raw = b'\x00\x00\x00\x03\x00' + source.to_bytes(4, 'big')
                fields = [('mapId', 16, ('string', raw), False),
                          ('mapIdType', 9, primitive(9, 1, 4), False)]
                output = serializer()._apply_field_xforms('DataExportChannel', fields)
                self.assertEqual(output[0][2][1], b'\x00\x00\x00\x05\x00' + expected.to_bytes(8, 'big'))
                self.assertEqual(serializer()._apply_field_xforms('DataExportChannel', output), output)

    def test_export_constants_named_maps_and_legacy_variants_stay_unchanged(self):
        runtime.FIELD_VALUE_TRANSFORM = load('v12.1_to_v12').field_value_transform
        for map_type, raw in [(0, b'\x00\x00\x00\x03\x00' + bytes(4)),
                              (2, b'\x00\x00\x00\x0a\x00Normal_OpenGL'),
                              (1, b'\x00\x00\x00\x05\x00' + (1 << 15).to_bytes(8, 'big'))]:
            fields = [('mapId', 16, ('string', raw), False),
                      ('mapIdType', 9, primitive(9, map_type, 4), False)]
            self.assertEqual(serializer()._apply_field_xforms('DataExportChannel', fields), fields)

    def test_binary_string_values_roundtrip_without_utf8_replacement(self):
        raw = b'\x00\x00\x00\x05\x00' + (1 << 15).to_bytes(8, 'big')
        stream = io.BytesIO()
        serializer()._write_reg_value(stream, 16, ('string', raw), {}, 0)
        stream.seek(0)
        self.assertEqual(serializer()._parse_v11_value(stream, 16, [], []), ('string', raw))
        with self.assertRaisesRegex(ValueError, 'Unexpected EOF'):
            serializer()._parse_v11_value(io.BytesIO(struct.pack('<I', 10) + b'x'), 16, [], [])


if __name__ == "__main__":
    unittest.main()
