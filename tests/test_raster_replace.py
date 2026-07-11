import sys
import struct
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
    def test_capture_channel_names_override_mapexport_array_indexes(self):
        lookup = {(0, 0, 0): 1}

        roughness = rr._entry_channel_mask({
            "channel": "roughness",
            "material_index": 0,
            "stack_index": 0,
            "channel_index": 0,
        }, lookup)
        metallic = rr._entry_channel_mask({"channel": "metallic"}, {})
        user0 = rr._entry_channel_mask({"channel": "user0"}, {})
        user5 = rr._entry_channel_mask({"channel": "user5"}, {})

        self.assertEqual(roughness, 1 << 7)
        self.assertEqual(metallic, 1 << 13)
        self.assertEqual(user0, 1 << 14)
        self.assertEqual(user5, 1 << 19)

    def test_primitive_int_reads_full_v12_channel_mask(self):
        value = 1 << 69
        self.assertEqual(
            rr._primitive_int(("primitive", 22, value.to_bytes(16, "little"))),
            value,
        )

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

    def test_layer_replacement_keeps_mask_stack_and_replaces_actions(self):
        root = channel_doc(0)
        original_mask = oval(obj("DataStackActions", [
            field("uid", prim(12, 200), 12),
            field("items", ("array", ("object", [])), 19),
        ]))
        root[1][1] = field("layers", arr(obj("DataLayerColor", [
            field("uid", prim(12, 42), 12),
            field("actions", oval(obj("DataStackActions", [
                field("uid", prim(12, 100), 12),
                field("items", arr(obj("DataActionGeneratorFancy", [
                    field("uid", prim(12, 101), 12),
                ])), 19),
            ]))),
            field("maskActions", original_mask),
        ])), 19)

        root, stats = rr.apply_raster_replacements(
            root,
            {"rf_layer": [{
                "url": "/Universal SPP Raster rf_layer?version=abc.image",
                "kind": "content",
                "material_index": 0,
                "stack_index": 0,
                "channel_index": 0,
            }]},
            requests=[{"id": "rf_layer", "scope": "layer", "layer_uid": 42, "object_uid": 42}],
        )

        self.assertEqual(stats["layers_replaced"], 1)
        layer = root[1][1][2][1][1][0][1]
        self.assertIs(get_field(layer, "maskActions")[2], original_mask)
        action_stack = get_field(layer, "actions")[2][1]
        fill = get_field(action_stack, "items")[2][1][1][0][1]
        self.assertEqual(fill[0], "DataActionFill")
        self.assertEqual(int_field(fill, "channelTypes"), 1)

    def test_full_stack_replacement_adds_named_top_group_and_preserves_original_channels(self):
        content_fill = obj("DataActionFill", [
            field("uid", prim(12, 101), 12),
            field("channelTypes", pvalue(7), 12),
            field("sources", arr(obj("DataSourceUniform", [
                field("uid", prim(12, 102), 12),
                field("channelTypes", pvalue(7), 12),
            ])), 19),
        ])
        mask_fill = obj("DataActionFill", [
            field("uid", prim(12, 201), 12),
            field("channelTypes", pvalue(1), 12),
            field("sources", arr(obj("DataSourceUniform", [
                field("uid", prim(12, 202), 12),
                field("channelTypes", pvalue(1), 12),
            ])), 19),
        ])
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
                                field("items", arr(content_fill), 19),
                            ]))),
                            field("maskActions", oval(obj("DataStackActions", [
                                field("uid", prim(12, 200), 12),
                                field("items", arr(mask_fill), 19),
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
        self.assertEqual(len(items), 2)
        original_layer = items[0][1]
        raster_group = items[1][1]
        self.assertEqual(raster_group[0], "DataLayerGroup")
        self.assertEqual(
            get_field(raster_group, "label")[2][1].decode("utf-8"),
            "Universal SPP Raster - Base Color, Height",
        )
        raster_items = get_field(get_field(raster_group, "subStack")[2][1], "items")[2][1][1]
        self.assertEqual(len(raster_items), 1)
        raster_layer = raster_items[0][1]
        fill = get_field(get_field(raster_layer, "actions")[2][1], "items")[2][1][1][0][1]
        self.assertEqual(fill[0], "DataActionFill")
        self.assertEqual(int_field(fill, "channelTypes"), 3)
        self.assertEqual(int_field(fill, "projection"), 0)

        original_fill = get_field(get_field(original_layer, "actions")[2][1], "items")[2][1][1][0][1]
        original_source = get_field(original_fill, "sources")[2][1][1][0][1]
        self.assertEqual(int_field(original_fill, "channelTypes"), 7)
        self.assertEqual(int_field(original_source, "channelTypes"), 7)
        original_mask_fill = get_field(
            get_field(original_layer, "maskActions")[2][1], "items"
        )[2][1][1][0][1]
        original_mask_source = get_field(original_mask_fill, "sources")[2][1][1][0][1]
        self.assertEqual(int_field(original_mask_fill, "channelTypes"), 1)
        self.assertEqual(int_field(original_mask_source, "channelTypes"), 1)

    def test_full_stack_uv_tiles_are_contained_in_the_raster_group(self):
        root = obj("DataDocument", [
            field("materials", arr(obj("DataMaterial", [
                field("stacks", arr(obj("DataMaterialStack", [
                    field("channels", arr(obj("DataChannel", [
                        field("type", pvalue(0, 9), 9),
                    ])), 19),
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
        assets = [
            {"url": "/stack1001", "kind": "full_stack_channel", "channel": "baseColor",
             "material_index": 0, "stack_index": 0, "uv_tile": 1001},
            {"url": "/stack1002", "kind": "full_stack_channel", "channel": "baseColor",
             "material_index": 0, "stack_index": 0, "uv_tile": 1002},
        ]

        root, stats = rr.apply_raster_replacements(
            root,
            {"rf_stack": assets},
            requests=[{"id": "rf_stack", "scope": "full_stack_channel", "stack_uid": 900,
                       "material_index": 0, "stack_index": 0}],
        )

        self.assertEqual(stats["full_stacks_replaced"], 1)
        material = root[1][0][2][1][1][0][1]
        stack_obj = get_field(material, "stacks")[2][1][1][0][1]
        items = get_field(get_field(stack_obj, "stack")[2][1], "items")[2][1][1]
        group = items[-1][1]
        children = get_field(get_field(group, "subStack")[2][1], "items")[2][1][1]
        self.assertEqual(len(children), 2)
        self.assertEqual(
            [struct.unpack("<ii", get_field(
                get_field(child[1], "enabledUVTileList")[2][1][1][0][1], "value"
            )[2][2]) for child in children],
            [(0, 0), (1, 0)],
        )

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

    def test_uv_tile_layer_replacement_builds_tile_scoped_children(self):
        root = channel_doc(0)
        original_mask = oval(obj("DataStackActions", [
            field("uid", prim(12, 200), 12),
            field("items", ("array", ("object", [])), 19),
        ]))
        root[1][1] = field("layers", arr(obj("DataLayerColor", [
            field("uid", prim(12, 42), 12),
            field("actions", oval(obj("DataStackActions", [
                field("uid", prim(12, 100), 12),
                field("items", ("array", ("object", [])), 19),
            ]))),
            field("maskActions", original_mask),
        ])), 19)

        root, stats = rr.apply_raster_replacements(
            root,
            {"rf_layer": [
                {"url": "/tile1001", "kind": "content", "material_index": 0,
                 "stack_index": 0, "channel_index": 0, "uv_tile": 1001},
                {"url": "/tile1002", "kind": "content", "material_index": 0,
                 "stack_index": 0, "channel_index": 0, "uv_tile": 1002},
            ]},
            requests=[{"id": "rf_layer", "scope": "layer", "layer_uid": 42}],
        )

        self.assertEqual(stats["layers_replaced"], 1)
        wrapper = root[1][1][2][1][1][0][1]
        self.assertEqual(wrapper[0], "DataLayerGroup")
        self.assertEqual(get_field(wrapper, "maskActions")[2], original_mask)
        child_stack = get_field(wrapper, "subStack")[2][1]
        children = get_field(child_stack, "items")[2][1][1]
        self.assertEqual(len(children), 2)
        coords = []
        for child_value in children:
            child = child_value[1]
            self.assertEqual(int_field(child, "enabledUVTileDefault"), 0)
            boxes = get_field(child, "enabledUVTileList")[2][1][1]
            raw = get_field(boxes[0][1], "value")[2][2]
            coords.append(struct.unpack("<ii", raw))
        self.assertEqual(coords, [(0, 0), (1, 0)])

    def test_uv_tile_mask_replacement_wraps_content_and_mask_per_tile(self):
        root = channel_doc(0)
        root[1][1] = field("layers", arr(obj("DataLayerColor", [
            field("uid", prim(12, 42), 12),
            field("actions", oval(obj("DataStackActions", [
                field("uid", prim(12, 100), 12),
                field("items", ("array", ("object", [])), 19),
            ]))),
            field("maskActions", oval(obj("DataStackActions", []))),
        ])), 19)
        assets = []
        for uv_tile in (1001, 1002):
            assets.extend([
                {"url": f"/content{uv_tile}", "kind": "content", "material_index": 0,
                 "stack_index": 0, "channel_index": 0, "uv_tile": uv_tile},
                {"url": f"/mask{uv_tile}", "kind": "mask", "uv_tile": uv_tile},
            ])

        root, stats = rr.apply_raster_replacements(
            root,
            {"rf_mask": assets},
            requests=[{"id": "rf_mask", "scope": "mask_stack", "layer_uid": 42}],
        )

        self.assertEqual(stats["mask_stacks_replaced"], 1)
        wrapper = root[1][1][2][1][1][0][1]
        self.assertEqual(wrapper[0], "DataLayerGroup")
        self.assertEqual(get_field(wrapper, "maskActions")[2][0], "object_null")
        children = get_field(get_field(wrapper, "subStack")[2][1], "items")[2][1][1]
        self.assertEqual(len(children), 2)
        mask_urls = []
        for child_value in children:
            child = child_value[1]
            mask_stack = get_field(child, "maskActions")[2][1]
            mask_fill = get_field(mask_stack, "items")[2][1][1][0][1]
            mask_source = get_field(mask_fill, "sources")[2][1][1][0][1]
            bitmap = get_field(mask_source, "bitmap")[2][1]
            mask_urls.append(get_field(bitmap, "urlToBitmapRes")[2][1].decode("utf-8"))
        self.assertEqual(mask_urls, ["/mask1001", "/mask1002"])


if __name__ == "__main__":
    unittest.main()
