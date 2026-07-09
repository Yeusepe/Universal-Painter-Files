import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spp_downgrader" / "spp_extractor"))

from lib.hbo_reserializer import _raster_replace as rr  # noqa: E402


def prim(code, n):
    return ("primitive", code, int(n).to_bytes(8 if code == 12 else 4, "little", signed=True))


def obj(name, fields=None):
    return (name, fields or [])


def field(name, value, code=0x12):
    return (name, code, value)


def oval(o):
    return ("object", o)


class RasterReplaceTests(unittest.TestCase):
    def test_mask_stack_replacement_uses_prepared_url(self):
        root = obj("DataDocument", [
            field("layers", ("array", ("object", [
                ("object", obj("DataLayerColor", [
                    field("uid", prim(12, 42), 12),
                    field("maskActions", oval(obj("DataStackActions", [
                        field("uid", prim(12, 100), 12),
                        field("items", ("array", ("object", [])), 19),
                    ]))),
                ])),
            ])), 19),
        ])
        requests = [{
            "id": "rf_mask",
            "scope": "mask_stack",
            "layer_uid": 42,
        }]
        root, stats = rr.apply_raster_replacements(
            root,
            {"rf_mask": [{"url": "/Universal SPP Raster rf_mask?version=abc.image", "kind": "mask"}]},
            requests=requests,
        )
        self.assertEqual(stats["mask_stacks_replaced"], 1)
        layer = root[1][0][2][1][1][0][1]
        mask_actions = next(f for f in layer[1] if f[0] == "maskActions")
        stack = mask_actions[2][1]
        self.assertEqual(stack[0], "DataStackActions")
        fill = stack[1][1][2][1][1][0][1]
        self.assertEqual(fill[0], "DataActionFill")
        source = next(f for f in fill[1] if f[0] == "sources")[2][1][1][0][1]
        bitmap = next(f for f in source[1] if f[0] == "bitmap")[2][1]
        url = next(f for f in bitmap[1] if f[0] == "urlToBitmapRes")[2][1].decode("utf-8")
        self.assertEqual(url, "/Universal SPP Raster rf_mask?version=abc.image")

    def test_no_matching_asset_leaves_tree_alone(self):
        root = obj("DataLayerColor", [
            field("uid", prim(12, 42), 12),
            field("maskActions", oval(obj("DataStackActions", []))),
        ])
        old_mask = root[1][1][2]
        root, stats = rr.apply_raster_replacements(
            root,
            {},
            requests=[{"id": "rf_mask", "scope": "mask_stack", "layer_uid": 42}],
        )
        self.assertEqual(stats["mask_stacks_replaced"], 0)
        self.assertIs(root[1][1][2], old_mask)


if __name__ == "__main__":
    unittest.main()
