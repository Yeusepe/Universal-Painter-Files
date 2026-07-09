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


def arr(*objects):
    return ("array", ("object", [("object", o) for o in objects]))


def pint(value, code=12):
    return int(value).to_bytes(8 if code == 12 else 4, "little", signed=True)


def pvalue(value, code=12):
    return ("primitive", code, pint(value, code))


def channel_doc(*channel_types):
    channels = [
        obj("DataChannel", [field("type", pvalue(t, 9), 9)])
        for t in channel_types
    ]
    return obj("DataDocument", [
        field("materials", arr(obj("DataMaterial", [
            field("stacks", arr(obj("DataMaterialStack", [
                field("channels", arr(*channels), 19),
            ])), 19),
        ])), 19),
        field("layers", ("array", ("object", [])), 19),
    ])


def get_field(o, name):
    return next(f for f in o[1] if f[0] == name)


def int_field(o, name):
    raw = get_field(o, name)[2][2]
    return int.from_bytes(raw, "little", signed=True)


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

    def test_source_replacement_preserves_neighbor_sources(self):
        root = channel_doc(0, 1)
        action = obj("DataActionFill", [
            field("uid", prim(12, 20), 12),
            field("sources", arr(
                obj("DataSourceUniform", [field("uid", prim(12, 25), 12)]),
                obj("DataSourceFancy", [field("uid", prim(12, 30), 12)]),
            ), 19),
        ])
        root[1][1] = field("layers", arr(obj("DataLayerColor", [
            field("uid", prim(12, 42), 12),
            field("actions", oval(obj("DataStackActions", [
                field("uid", prim(12, 100), 12),
                field("items", arr(action), 19),
            ]))),
        ])), 19)

        root, stats = rr.apply_raster_replacements(
            root,
            {"rf_source": [{
                "url": "/Universal SPP Raster rf_source?version=abc.image",
                "kind": "content",
                "material_index": 0,
                "stack_index": 0,
                "channel_index": 1,
            }]},
            requests=[{
                "id": "rf_source",
                "scope": "source",
                "object_uid": 30,
            }],
        )

        self.assertEqual(stats["sources_replaced"], 1)
        layer = root[1][1][2][1][1][0][1]
        stack = get_field(layer, "actions")[2][1]
        fill = get_field(stack, "items")[2][1][1][0][1]
        sources = get_field(fill, "sources")[2][1][1]
        self.assertEqual([s[1][0] for s in sources], ["DataSourceUniform", "DataSourceBitmap"])
        bitmap_source = sources[1][1]
        self.assertEqual(int_field(bitmap_source, "channelTypes"), 2)

    def test_content_action_replacement_uses_captured_channel_indexes(self):
        root = channel_doc(0, 13)
        unsupported = obj("DataActionGeneratorFancy", [
            field("uid", prim(12, 70), 12),
        ])
        root[1][1] = field("layers", arr(obj("DataLayerColor", [
            field("uid", prim(12, 42), 12),
            field("actions", oval(obj("DataStackActions", [
                field("uid", prim(12, 100), 12),
                field("items", arr(unsupported), 19),
            ]))),
        ])), 19)

        root, stats = rr.apply_raster_replacements(
            root,
            {"rf_action": [
                {
                    "url": "/Universal SPP Raster rf_action_base?version=abc.image",
                    "kind": "content",
                    "material_index": 0,
                    "stack_index": 0,
                    "channel_index": 0,
                },
                {
                    "url": "/Universal SPP Raster rf_action_rough?version=def.image",
                    "kind": "content",
                    "material_index": 0,
                    "stack_index": 0,
                    "channel_index": 1,
                },
            ]},
            requests=[{
                "id": "rf_action",
                "scope": "content_action",
                "object_uid": 70,
            }],
        )

        self.assertEqual(stats["content_actions_replaced"], 1)
        layer = root[1][1][2][1][1][0][1]
        stack = get_field(layer, "actions")[2][1]
        fill = get_field(stack, "items")[2][1][1][0][1]
        self.assertEqual(fill[0], "DataActionFill")
        self.assertEqual(int_field(fill, "channelTypes"), 1 | (1 << 13))
        sources = get_field(fill, "sources")[2][1][1]
        self.assertEqual(len(sources), 2)
        self.assertEqual([int_field(s[1], "channelTypes") for s in sources], [1, 1 << 13])

    def test_group_replacement_uses_group_scope_assets(self):
        root = channel_doc(0)
        group = obj("DataActionGroup", [
            field("uid", prim(12, 70), 12),
            field("subStack", oval(obj("DataStackActions", [
                field("uid", prim(12, 71), 12),
                field("items", arr(obj("DataActionGeneratorFancy", [
                    field("uid", prim(12, 72), 12),
                ])), 19),
            ]))),
        ])
        root[1][1] = field("layers", arr(obj("DataLayerColor", [
            field("uid", prim(12, 42), 12),
            field("actions", oval(obj("DataStackActions", [
                field("uid", prim(12, 100), 12),
                field("items", arr(group), 19),
            ]))),
        ])), 19)

        root, stats = rr.apply_raster_replacements(
            root,
            {"rf_group": [{
                "url": "/Universal SPP Raster rf_group?version=abc.image",
                "kind": "group",
                "material_index": 0,
                "stack_index": 0,
                "channel_index": 0,
            }]},
            requests=[{"id": "rf_group", "scope": "group", "object_uid": 70}],
        )

        self.assertEqual(stats["content_actions_replaced"], 1)
        layer = root[1][1][2][1][1][0][1]
        stack = get_field(layer, "actions")[2][1]
        fill = get_field(stack, "items")[2][1][1][0][1]
        self.assertEqual(fill[0], "DataActionFill")
        self.assertEqual(int_field(fill, "channelTypes"), 1)

    def test_full_stack_replacement_flattens_stack_to_raster_layer(self):
        root = obj("DataDocument", [
            field("materials", arr(obj("DataMaterial", [
                field("stacks", arr(obj("DataMaterialStack", [
                    field("channels", arr(
                        obj("DataChannel", [field("type", pvalue(0, 9), 9)]),
                        obj("DataChannel", [field("type", pvalue(1, 9), 9)]),
                    ), 19),
                    field("stack", oval(obj("DataStackLayers", [
                        field("uid", prim(12, 900), 12),
                        field("items", arr(obj("DataLayerColor", [
                            field("uid", prim(12, 42), 12),
                            field("actions", oval(obj("DataStackActions", [
                                field("uid", prim(12, 100), 12),
                                field("items", ("array", ("object", [])), 19),
                            ]))),
                        ])), 19),
                    ]))),
                ])), 19),
            ])), 19),
        ])

        root, stats = rr.apply_raster_replacements(
            root,
            {"rf_stack": [
                {
                    "url": "/Universal SPP Raster rf_stack_base?version=abc.image",
                    "kind": "full_stack_channel",
                    "material_index": 0,
                    "stack_index": 0,
                    "channel_index": 0,
                },
                {
                    "url": "/Universal SPP Raster rf_stack_height?version=def.image",
                    "kind": "full_stack_channel",
                    "material_index": 0,
                    "stack_index": 0,
                    "channel_index": 1,
                },
            ]},
            requests=[{"id": "rf_stack", "scope": "full_stack_channel", "stack_uid": 900}],
        )

        self.assertEqual(stats["full_stacks_replaced"], 1)
        material = root[1][0][2][1][1][0][1]
        stack_obj = get_field(material, "stacks")[2][1][1][0][1]
        layer_stack = get_field(stack_obj, "stack")[2][1]
        items = get_field(layer_stack, "items")[2][1][1]
        self.assertEqual(len(items), 1)
        raster_layer = items[0][1]
        self.assertEqual(raster_layer[0], "DataLayerColor")
        fill = get_field(get_field(raster_layer, "actions")[2][1], "items")[2][1][1][0][1]
        self.assertEqual(fill[0], "DataActionFill")
        self.assertEqual(int_field(fill, "channelTypes"), 3)

    def test_legacy_channel_name_mapping_is_conservative(self):
        root = obj("DataLayerColor", [
            field("uid", prim(12, 42), 12),
            field("actions", oval(obj("DataStackActions", [
                field("uid", prim(12, 100), 12),
                field("items", arr(obj("DataActionFancy", [
                    field("uid", prim(12, 70), 12),
                ])), 19),
            ]))),
        ])

        root, stats = rr.apply_raster_replacements(
            root,
            {"rf_action": [
                {"url": "/Universal SPP Raster rf_action?version=abc.image", "kind": "content", "channel": "basecolor"},
                {"url": "/Universal SPP Raster rf_skip?version=def.image", "kind": "content", "channel": "unknown_special"},
            ]},
            requests=[{"id": "rf_action", "scope": "content_action", "object_uid": 70}],
        )

        self.assertEqual(stats["content_actions_replaced"], 1)
        self.assertEqual(stats["channel_assets_skipped"], 1)
        stack = get_field(root, "actions")[2][1]
        fill = get_field(stack, "items")[2][1][1][0][1]
        self.assertEqual(int_field(fill, "channelTypes"), 1)


if __name__ == "__main__":
    unittest.main()
