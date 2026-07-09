"""Unit tests for the downgrade classifier (_classify) and the introduction-map
derivation in migration_profile. Pure/headless -- no Painter, no .spp needed."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLASSIFY_PATH = ROOT / "spp_downgrader" / "spp_extractor" / "lib" / "hbo_reserializer" / "_classify.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# _classify imports `from . import runtime` only inside from_runtime(), so it loads
# standalone as a top-level module.
clf = _load("uspp_classify", CLASSIFY_PATH)


def node(name, fields=None):
    return (name, fields if fields is not None else [])


class AppearanceTypeTests(unittest.TestCase):
    def test_layer_stack_types_are_appearance(self):
        for n in ("DataActionFill", "DataActionFilterProcedural", "DataSourceProcedural",
                  "DataLayerColor", "DataStackActions", "DataBlending"):
            self.assertTrue(clf.is_appearance_type(n), n)

    def test_metadata_and_postfx_are_not_appearance(self):
        for n in ("DataBloomParametersV2", "SettingsSymmetry", "BakingCommonParameters",
                  "scale_unit", ""):
            self.assertFalse(clf.is_appearance_type(n), n)


class ClassifyTests(unittest.TestCase):
    def make(self, **kw):
        base = dict(
            schema={"DataActionFill": ["sources"], "DataLayerColor": ["actions", "maskActions"]},
            target_members=frozenset({"DataActionFill", "DataLayerColor", "DataStackActions",
                                      "DataSourceBitmap", "DataBlending"}),
            blacklist=["DataBloomParametersV2"],
            type_rename={"DataBrushStamp": "DataBrush"},
        )
        base.update(kw)
        return clf.Classifier(**base)

    def test_supported_type_is_kept(self):
        c = self.make()
        self.assertEqual(c.classify(node("DataActionFill")).action, clf.KEEP)

    def test_renamed_type_is_transform_not_bake(self):
        c = self.make()
        self.assertEqual(c.classify(node("DataBrushStamp")).action, clf.TRANSFORM)

    def test_unsupported_appearance_in_content_bakes_content(self):
        c = self.make()
        v = c.classify(node("DataActionFilterNewGizmo"), substack=clf.CONTENT)
        self.assertEqual(v.action, clf.BAKE)
        self.assertEqual(v.granularity, clf.G_CONTENT)

    def test_unsupported_appearance_in_mask_bakes_mask(self):
        c = self.make()
        v = c.classify(node("DataActionGeneratorNew"), substack=clf.MASK)
        self.assertEqual(v.action, clf.BAKE)
        self.assertEqual(v.granularity, clf.G_MASK)

    def test_unsupported_nonappearance_is_dropped(self):
        c = self.make()
        self.assertEqual(c.classify(node("DataBloomParametersV2")).action, clf.DROP)

    def test_blacklisted_appearance_type_is_unsupported(self):
        # A blacklisted type that IS appearance-bearing still bakes rather than silently keep.
        c = self.make(blacklist=["DataActionFill"])
        self.assertEqual(c.classify(node("DataActionFill"), substack=clf.CONTENT).action, clf.BAKE)

    def test_member_filter_disabled_keeps_unknown(self):
        c = self.make(target_members=None, schema={})
        self.assertEqual(c.classify(node("DataActionTotallyUnknown")).action, clf.KEEP)

    def test_strict_unknown_appearance_bakes_without_member_filter(self):
        c = self.make(target_members=None, schema={}, unknown_appearance_unsupported=True)
        self.assertEqual(c.classify(node("DataActionTotallyUnknown"), substack=clf.CONTENT).action, clf.BAKE)


class BlendModeTests(unittest.TestCase):
    def test_out_of_range_blend_composites(self):
        c = clf.Classifier(blend_max=26)
        self.assertEqual(c.classify_blend(30).granularity, clf.G_COMPOSITE)

    def test_in_range_blend_kept(self):
        c = clf.Classifier(blend_max=26)
        self.assertEqual(c.classify_blend(10).action, clf.KEEP)

    def test_no_blend_max_disables(self):
        c = clf.Classifier(blend_max=None)
        self.assertEqual(c.classify_blend(999).action, clf.KEEP)


class ParamEscalationTests(unittest.TestCase):
    def test_nondefault_unsupported_member_escalates(self):
        c = clf.Classifier(schema={"DataActionFill": ["sources"]})
        # 'newParam' is not in the schema for DataActionFill -> unsupported member.
        self.assertTrue(c.param_forces_bake("DataActionFill", "newParam", is_default=False))
        self.assertFalse(c.param_forces_bake("DataActionFill", "newParam", is_default=True))

    def test_supported_member_never_escalates(self):
        c = clf.Classifier(schema={"DataActionFill": ["sources"]})
        self.assertFalse(c.param_forces_bake("DataActionFill", "sources", is_default=False))


class IntroductionMapTests(unittest.TestCase):
    """derive_introductions / feature_floor / above_floor against a temp profile dir."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="uspp_intro_")
        self._write("v12_to_v11.json", {"blacklist": ["DataNewV12", "BakingCommonParameters"]})
        self._write("v9_to_v8.1.json", {"blacklist": ["BakingCommonParameters"]})
        # migration_profile is imported with SPP_PROFILE_DIR pointing at our temp dir.
        os.environ["SPP_PROFILE_DIR"] = self.dir
        mp_path = ROOT / "spp_downgrader" / "spp_extractor" / "lib" / "migration_profile.py"
        self.mp = _load("uspp_mp_intro", mp_path)

    def tearDown(self):
        os.environ.pop("SPP_PROFILE_DIR", None)

    def _write(self, name, obj):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_derives_introductions_earliest_wins(self):
        intro = self.mp.derive_introductions(self.dir)
        self.assertEqual(intro["DataNewV12"], "12")
        # BakingCommonParameters appears in both steps -> earliest (8.1's source = 9).
        self.assertEqual(intro["BakingCommonParameters"], "9")

    def test_overrides_win(self):
        self._write("feature_introduction.overrides.json",
                    {"introductions": {"DataNewV12": "12.1"}})
        intro = self.mp.derive_introductions(self.dir)
        self.assertEqual(intro["DataNewV12"], "12.1")

    def test_floor_and_above(self):
        self.assertEqual(self.mp.feature_floor(["12.1", "12", "11", "8.1"]), "8.1")
        self.assertTrue(self.mp.above_floor("12", "8.1"))
        self.assertFalse(self.mp.above_floor("8.1", "8.1"))


if __name__ == "__main__":
    unittest.main()
