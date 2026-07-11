import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
UPDATER_PATH = ROOT / "universal_spp_plugin" / "lib" / "updater.py"
SPEC = importlib.util.spec_from_file_location("uspp_updater", UPDATER_PATH)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in entries.items():
            z.writestr(name, content)


class VersionTests(unittest.TestCase):
    def test_compares_semver_tags(self):
        self.assertGreater(updater.compare_versions("v0.1.10", "0.1.9"), 0)
        self.assertEqual(updater.compare_versions("v0.1.7", "0.1.7"), 0)
        self.assertLess(updater.compare_versions("0.1.6", "0.1.7"), 0)


class ReleaseParsingTests(unittest.TestCase):
    def test_selects_latest_stable_by_version(self):
        releases = [
            {"tag_name": "v0.1.9", "draft": False, "prerelease": False},
            {"tag_name": "v0.2.0-beta.1", "draft": False, "prerelease": True},
            {"tag_name": "v0.1.10", "draft": False, "prerelease": False},
            {"tag_name": "v0.3.0", "draft": True, "prerelease": False},
        ]
        self.assertEqual(updater.release_version(updater.select_latest_release(releases)), "0.1.10")
        self.assertEqual(
            updater.release_version(updater.select_latest_release(releases, include_prereleases=True)),
            "0.2.0-beta.1",
        )

    def test_builds_update_info_with_checksum(self):
        sha = "A" * 64
        release = {
            "tag_name": "v0.1.8",
            "name": "Universal SPP v0.1.8",
            "html_url": "https://example.test/releases/v0.1.8",
            "body": f"SHA-256: `{sha}`",
            "assets": [
                {
                    "name": "universal_spp_plugin-0.1.8.zip",
                    "browser_download_url": "https://example.test/plugin.zip",
                }
            ],
        }
        with mock.patch.object(updater.platform, "system", return_value="Windows"), \
                mock.patch.object(updater.platform, "machine", return_value="AMD64"):
            info = updater.update_info_from_release(release)
        self.assertEqual(info.version, "0.1.8")
        self.assertEqual(info.asset_name, "universal_spp_plugin-0.1.8.zip")
        self.assertEqual(info.sha256, sha.lower())

    def test_linux_selects_tagged_asset_and_rejects_windows_fallback(self):
        release = {
            "tag_name": "v0.2.0",
            "assets": [
                {"name": "universal_spp_plugin-0.2.0.zip", "browser_download_url": "win"},
                {
                    "name": "universal_spp_plugin-0.2.0-linux-x86_64.zip",
                    "browser_download_url": "linux",
                },
            ],
        }
        with mock.patch.object(updater.platform, "system", return_value="Linux"), \
                mock.patch.object(updater.platform, "machine", return_value="x86_64"):
            info = updater.update_info_from_release(release)
        self.assertEqual(info.asset_name, "universal_spp_plugin-0.2.0-linux-x86_64.zip")
        self.assertEqual(info.download_url, "linux")

        release["assets"] = release["assets"][:1]
        with mock.patch.object(updater.platform, "system", return_value="Linux"), \
                mock.patch.object(updater.platform, "machine", return_value="x86_64"), \
                self.assertRaises(updater.UpdateError):
            updater.update_info_from_release(release)

    def test_missing_release_asset_is_an_error(self):
        release = {"tag_name": "v0.1.8", "assets": []}
        with self.assertRaises(updater.UpdateError):
            updater.update_info_from_release(release)

    def test_fetch_releases_rejects_api_error_payload(self):
        original = updater.request_json
        updater.request_json = lambda url, timeout=10: {"message": "rate limited"}
        try:
            with self.assertRaises(updater.UpdateError):
                updater.fetch_releases()
        finally:
            updater.request_json = original


class SettingsTests(unittest.TestCase):
    def test_linux_settings_follow_xdg_config_home(self):
        with mock.patch.object(updater.sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}, clear=True):
            self.assertEqual(updater.settings_dir(), os.path.join("/tmp/xdg", "universal-spp"))

    def test_daily_check_throttling_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            settings = updater.load_settings(path)
            self.assertTrue(updater.should_auto_check(settings, now=100000))

            settings = updater.mark_checked(settings, path=path, now=100000)
            self.assertFalse(updater.should_auto_check(settings, now=100010))
            self.assertTrue(updater.should_auto_check(settings, now=100000 + updater.CHECK_INTERVAL_SECONDS))

            loaded = updater.load_settings(path)
            self.assertEqual(loaded["last_checked"], 100000)

    def test_disabled_auto_check_never_runs(self):
        settings = dict(updater.DEFAULT_SETTINGS)
        settings["auto_check_enabled"] = False
        self.assertFalse(updater.should_auto_check(settings, now=999999))

    def test_raster_settings_are_normalized_and_bounded(self):
        settings = updater._clean_settings({
            "raster_capture_enabled": 0,
            "raster_content_bit_depth": 16,
            "raster_padding": "INFINITE",
            "raster_budget_mb": 99999,
            "raster_evaluation_timeout_seconds": 1,
            "keep_failed_raster_captures": 0,
        })

        self.assertFalse(settings["raster_capture_enabled"])
        self.assertEqual(settings["raster_content_bit_depth"], "16")
        self.assertEqual(settings["raster_padding"], "infinite")
        self.assertEqual(settings["raster_budget_mb"], 4096)
        self.assertEqual(settings["raster_evaluation_timeout_seconds"], 5)
        self.assertFalse(settings["keep_failed_raster_captures"])

    def test_invalid_raster_choices_fall_back_to_defaults(self):
        settings = updater._clean_settings({
            "raster_content_bit_depth": "32",
            "raster_padding": "smear",
            "raster_budget_mb": "invalid",
            "raster_evaluation_timeout_seconds": "invalid",
        })

        self.assertEqual(settings["raster_content_bit_depth"], "source")
        self.assertEqual(settings["raster_padding"], "transparent")
        self.assertEqual(settings["raster_budget_mb"], 512)
        self.assertEqual(settings["raster_evaluation_timeout_seconds"], 30)


class ZipValidationTests(unittest.TestCase):
    def test_linux_converter_is_marked_executable_after_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "linux.zip")
            make_zip(zip_path, {
                "universal_spp_plugin/__init__.py": "# plugin\n",
                "universal_spp_plugin/bin/uspp_tool": "binary",
            })
            dest = os.path.join(tmp, "dest")
            with mock.patch.object(updater.sys, "platform", "linux"), \
                    mock.patch.object(updater.os, "chmod") as chmod:
                updater.safe_extract(zip_path, dest)
            chmod.assert_called_once()

    def test_validates_good_zip_and_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "good.zip")
            make_zip(
                zip_path,
                {
                    "universal_spp_plugin/": "",
                    "universal_spp_plugin/lib/": "",
                    "universal_spp_plugin/__init__.py": "# plugin\n",
                    "universal_spp_plugin/lib/updater.py": "# updater\n",
                },
            )
            sha = updater.sha256_file(zip_path)
            self.assertTrue(updater.validate_update_zip(zip_path, sha))
            with self.assertRaises(updater.UpdateError):
                updater.validate_update_zip(zip_path, "0" * 64)

    def test_rejects_zip_missing_plugin_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "missing.zip")
            make_zip(zip_path, {"universal_spp_plugin/README.md": "readme\n"})
            with self.assertRaises(updater.UpdateError):
                updater.validate_update_zip(zip_path)

    def test_rejects_path_traversal_and_outside_paths(self):
        cases = [
            {"universal_spp_plugin/__init__.py": "", "universal_spp_plugin/../evil.txt": ""},
            {"universal_spp_plugin/__init__.py": "", "evil.txt": ""},
            {"universal_spp_plugin/__init__.py": "", "C:/evil.txt": ""},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for idx, entries in enumerate(cases):
                zip_path = os.path.join(tmp, f"bad-{idx}.zip")
                make_zip(zip_path, entries)
                with self.assertRaises(updater.UpdateError):
                    updater.validate_update_zip(zip_path)


class InstallTests(unittest.TestCase):
    def test_install_replaces_plugin_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = os.path.join(tmp, "universal_spp_plugin")
            os.makedirs(plugin_root)
            Path(plugin_root, "__init__.py").write_text("# old\n", encoding="utf-8")
            Path(plugin_root, "old_only.txt").write_text("old\n", encoding="utf-8")

            zip_path = os.path.join(tmp, "update.zip")
            make_zip(
                zip_path,
                {
                    "universal_spp_plugin/__init__.py": "# new\n",
                    "universal_spp_plugin/lib/updater.py": "# updater\n",
                },
            )
            updater.install_zip(zip_path, plugin_root=plugin_root, temp_dir=tmp)

            self.assertEqual(Path(plugin_root, "__init__.py").read_text(encoding="utf-8"), "# new\n")
            self.assertFalse(Path(plugin_root, "old_only.txt").exists())
            self.assertTrue(Path(plugin_root, "lib", "updater.py").exists())

    def test_install_rolls_back_on_copy_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_root = os.path.join(tmp, "universal_spp_plugin")
            os.makedirs(plugin_root)
            Path(plugin_root, "__init__.py").write_text("# old\n", encoding="utf-8")

            zip_path = os.path.join(tmp, "update.zip")
            make_zip(zip_path, {"universal_spp_plugin/__init__.py": "# new\n"})

            calls = {"count": 0}

            def fail_second_copy(src, dst, *args, **kwargs):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise RuntimeError("simulated copy failure")
                return shutil.copytree(src, dst, *args, **kwargs)

            with self.assertRaises(updater.UpdateError):
                updater.install_zip(
                    zip_path,
                    plugin_root=plugin_root,
                    temp_dir=tmp,
                    copytree_func=fail_second_copy,
                )

            self.assertEqual(Path(plugin_root, "__init__.py").read_text(encoding="utf-8"), "# old\n")


if __name__ == "__main__":
    unittest.main()
