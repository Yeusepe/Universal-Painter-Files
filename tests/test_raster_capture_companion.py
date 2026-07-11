from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_JS = (
    ROOT / "universal_spp_plugin" / "raster_capture_companion" / "raster_capture.js"
)


class RasterCaptureCompanionTests(unittest.TestCase):
    def test_masks_use_direct_layer_selector_without_synthetic_channel(self):
        source = CAPTURE_JS.read_text(encoding="utf-8")
        self.assertIn('_save([uid, "mask"]', source)
        self.assertIn("prepareCapture(planPath, preparationPath)", source)
        self.assertIn('texturesets.addChannel(entry.material, "blendingmask", "L8")', source)
        self.assertIn("capture(planPath, manifestPath, preparationPath, optionsPath)", source)
        self.assertIn("_removePreparedChannels(preparationPath, warnings)", source)

    def test_capture_options_control_padding_and_bit_depth(self):
        source = CAPTURE_JS.read_text(encoding="utf-8")
        self.assertIn('_captureOptions.padding === "infinite"', source)
        self.assertIn("_captureOptions.content_bit_depth", source)
        self.assertIn("capture(planPath, manifestPath, preparationPath, optionsPath)", source)


if __name__ == "__main__":
    unittest.main()
