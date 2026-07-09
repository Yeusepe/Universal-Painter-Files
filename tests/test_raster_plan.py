import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "spp_downgrader" / "spp_extractor"))

from lib.hbo_reserializer import _classify as clf  # noqa: E402
from lib.hbo_reserializer import _raster_plan as rp  # noqa: E402


def prim(code, n):
    return ("primitive", code, int(n).to_bytes(4, "little", signed=True))


def obj(name, fields=None):
    return (name, fields or [])


def field(name, value, code=0x12):
    return (name, code, value)


def oval(o):
    return ("object", o)


def arr(*objects):
    return ("array", ("object", [("object", o) for o in objects]))


class RasterPlanScopeTests(unittest.TestCase):
    def make_classifier(self, blacklist=()):
        return clf.Classifier(
            schema={
                "DataLayerColor": ["actions", "maskActions", "uid"],
                "DataDocument": ["materials"],
                "DataMaterial": ["stacks"],
                "DataMaterialStack": ["stack"],
                "DataStackLayers": ["items", "uid"],
                "DataStackActions": ["items", "uid"],
                "DataActionFill": ["sources", "uid"],
                "DataActionGroup": ["subStack", "uid"],
                "DataSourceBitmap": ["bitmap", "uid"],
            },
            target_members=frozenset({
                "DataLayerColor", "DataStackActions", "DataActionFill",
                "DataActionGroup", "DataSourceBitmap", "DataDocument",
                "DataMaterial", "DataMaterialStack", "DataStackLayers",
                "uid", "items", "actions", "maskActions", "sources",
                "subStack", "materials", "stacks", "stack",
            }),
            blacklist=blacklist,
            unknown_appearance_unsupported=True,
        )

    def collect(self, root, blacklist=()):
        return rp.collect_raster_requests(
            root,
            classifier=self.make_classifier(blacklist),
            dataset="paint/document.bin",
            target="8.1",
        )

    def test_unsupported_mask_generator_produces_mask_stack(self):
        root = obj("DataLayerColor", [
            field("uid", prim(9, 10), 9),
            field("maskActions", oval(obj("DataStackActions", [
                field("items", arr(obj("DataActionGeneratorNew", [field("uid", prim(9, 20), 9)])), 0x13),
            ]))),
        ])
        reqs = self.collect(root)
        self.assertEqual(reqs[0]["scope"], rp.S_MASK_STACK)
        self.assertEqual(reqs[0]["kind"], rp.K_MASK)
        self.assertEqual(reqs[0]["layer_uid"], 10)

    def test_mask_stack_requests_are_coalesced_by_layer(self):
        root = obj("DataLayerColor", [
            field("uid", prim(9, 10), 9),
            field("maskActions", oval(obj("DataStackActions", [
                field("items", arr(
                    obj("DataActionGeneratorNew", [field("uid", prim(9, 20), 9)]),
                    obj("DataSourceReference", [field("uid", prim(9, 21), 9)]),
                ), 0x13),
            ]))),
        ])
        reqs = self.collect(root)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["scope"], rp.S_MASK_STACK)
        self.assertIn("DataActionGeneratorNew", reqs[0]["object_type"])
        self.assertIn("DataSourceReference", reqs[0]["object_type"])

    def test_unsupported_source_inside_fill_produces_source_scope(self):
        root = obj("DataLayerColor", [
            field("uid", prim(9, 10), 9),
            field("actions", oval(obj("DataStackActions", [
                field("items", arr(obj("DataActionFill", [
                    field("sources", arr(obj("DataSourceVectorial", [field("uid", prim(9, 30), 9)])), 0x13),
                ])), 0x13),
            ]))),
        ])
        reqs = self.collect(root, blacklist=["DataSourceVectorial"])
        self.assertEqual(reqs[0]["scope"], rp.S_SOURCE)
        self.assertEqual(reqs[0]["object_type"], "DataSourceVectorial")

    def test_unsupported_local_content_action_produces_content_action(self):
        root = obj("DataLayerColor", [
            field("uid", prim(9, 10), 9),
            field("actions", oval(obj("DataStackActions", [
                field("items", arr(obj("DataActionFancy", [field("uid", prim(9, 40), 9)])), 0x13),
            ]))),
        ])
        reqs = self.collect(root)
        self.assertEqual(reqs[0]["scope"], rp.S_CONTENT_ACTION)

    def test_span_dependent_generator_inside_group_produces_group_scope(self):
        root = obj("DataLayerColor", [
            field("uid", prim(9, 10), 9),
            field("actions", oval(obj("DataStackActions", [
                field("items", arr(obj("DataActionGroup", [
                    field("uid", prim(9, 50), 9),
                    field("subStack", oval(obj("DataStackActions", [
                        field("items", arr(obj("DataActionGeneratorNew", [
                            field("uid", prim(9, 60), 9),
                        ])), 0x13),
                    ]))),
                ])), 0x13),
            ]))),
        ])
        reqs = self.collect(root)
        self.assertEqual(reqs[0]["scope"], rp.S_GROUP)
        self.assertEqual(reqs[0]["capture"]["selector"], [10, "<channel>"])
        self.assertEqual(reqs[0]["preserves_editability"], "low")

    def test_nested_document_requests_include_material_and_stack_indexes(self):
        root = obj("DataDocument", [
            field("materials", arr(obj("DataMaterial", [
                field("stacks", arr(obj("DataMaterialStack", [
                    field("stack", oval(obj("DataStackLayers", [
                        field("uid", prim(9, 77), 9),
                        field("items", arr(obj("DataLayerColor", [
                            field("uid", prim(9, 10), 9),
                            field("actions", oval(obj("DataStackActions", [
                                field("items", arr(obj("DataActionFancy", [
                                    field("uid", prim(9, 40), 9),
                                ])), 0x13),
                            ]))),
                        ])), 0x13),
                    ]))),
                ])), 0x13),
            ])), 0x13),
        ])
        reqs = self.collect(root)
        self.assertEqual(reqs[0]["scope"], rp.S_CONTENT_ACTION)
        self.assertEqual(reqs[0]["material_index"], 0)
        self.assertEqual(reqs[0]["stack_index"], 0)


if __name__ == "__main__":
    unittest.main()
