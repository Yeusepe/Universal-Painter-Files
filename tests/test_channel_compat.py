import struct
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spp_downgrader" / "spp_extractor"))

from lib.hbo_reserializer import HBOSerializer, runtime  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
