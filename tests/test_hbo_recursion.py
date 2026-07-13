import io
from pathlib import Path
import struct
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spp_downgrader" / "spp_extractor"))

from lib.hbo_reserializer import runtime  # noqa: E402
from lib.hbo_reserializer.serializer import HBOSerializer  # noqa: E402


def _deep_registry_stream(depth):
    node = ("DeepNode", [])
    for _ in range(depth):
        node = ("DeepNode", [("child", 0x12, ("object", node))])

    serializer = HBOSerializer(struct.pack("<III", 0x1B7C2FDD, 1, 91))
    dst = io.BytesIO()
    dst.write(struct.pack("<III", 0x1B7C2FDD, 1, 91))
    dst.write(struct.pack("<I", 0x12))
    dst.write(struct.pack("<I", 0))
    emitted = {}
    serializer._write_reg_object(dst, node, emitted, 0)
    data = bytearray(dst.getvalue())
    struct.pack_into("<I", data, 16, len(emitted))
    return bytes(data)


class HBORecursionTests(unittest.TestCase):
    def test_registry_transcode_handles_document_beyond_old_depth_limit(self):
        source = _deep_registry_stream(30)
        serializer = HBOSerializer(source)
        old_members = runtime.TARGET_MEMBERS
        runtime.TARGET_MEMBERS = None
        try:
            root = serializer.parse_root_object()
            converted = serializer.prune_and_reserialize([], data_version=81)
        finally:
            runtime.TARGET_MEMBERS = old_members

        self.assertEqual(root[0], "DeepNode")
        self.assertGreater(len(converted), 12)
        self.assertEqual(struct.unpack("<III", converted[:12]), (0x1B7C2FDD, 0, 81))


if __name__ == "__main__":
    unittest.main()
