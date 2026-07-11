"""Self-update helpers for the Universal SPP Painter plugin.

This module deliberately avoids Qt and substance_painter imports so the risky
parts of the updater can be tested outside Painter.
"""
from dataclasses import dataclass
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import time
import urllib.request
import zipfile


PLUGIN_VERSION = "0.1.9"
OWNER = "Yeusepe"
REPO = "Universal-Painter-Files"
RELEASES_API_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/releases"
RELEASES_PAGE_URL = f"https://github.com/{OWNER}/{REPO}/releases"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ZIP_ROOT = "universal_spp_plugin"
_USER_AGENT = f"UniversalSPP/{PLUGIN_VERSION}"
_SHA_RE = re.compile(r"\b([A-Fa-f0-9]{64})\b")

DEFAULT_SETTINGS = {
    "auto_check_enabled": True,
    "include_prereleases": False,
    "skipped_version": "",
    "last_checked": 0.0,
    "raster_capture_enabled": True,
    "raster_content_bit_depth": "source",
    "raster_padding": "transparent",
    "raster_budget_mb": 512,
    "raster_evaluation_timeout_seconds": 30,
    "keep_failed_raster_captures": True,
}


class UpdateError(RuntimeError):
    pass


@dataclass
class UpdateInfo:
    version: str
    tag_name: str
    name: str
    html_url: str
    asset_name: str
    download_url: str
    sha256: str = ""
    checksum_url: str = ""


def normalize_version(value):
    text = str(value or "").strip()
    if text.lower().startswith("v"):
        text = text[1:]
    return text


def version_key(value):
    nums = [int(x) for x in re.findall(r"\d+", normalize_version(value))]
    if not nums:
        return ()
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums[:4])


def compare_versions(left, right):
    a = version_key(left)
    b = version_key(right)
    if not a or not b:
        raise UpdateError(f"Could not compare versions: {left!r}, {right!r}")
    return (a > b) - (a < b)


def asset_name_for_version(version):
    return f"{_ZIP_ROOT}-{normalize_version(version)}.zip"


def platform_asset_tag(system=None, machine=None):
    system = (system or platform.system()).strip().lower()
    machine = (machine or platform.machine()).strip().lower()
    arches = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}
    return "{}-{}".format(system or "unknown", arches.get(machine, machine or "unknown"))


def release_asset_names(version, system=None, machine=None):
    """Preferred release payloads for this host.

    Existing releases use a generic ZIP containing the Windows executable, so only
    Windows may safely fall back to that historical name. Linux requires an explicitly
    tagged payload and will never install a Windows bundle by accident.
    """
    normalized = normalize_version(version)
    system_name = (system or platform.system()).strip().lower()
    tagged = f"{_ZIP_ROOT}-{normalized}-{platform_asset_tag(system_name, machine)}.zip"
    if system_name == "windows":
        return (tagged, asset_name_for_version(normalized))
    return (tagged,)


def settings_dir(base_dir=None):
    if base_dir:
        return base_dir
    override = os.environ.get("USPP_SETTINGS_DIR")
    if override:
        return override
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "UniversalSPP")
    if sys.platform.startswith("linux"):
        config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
        return os.path.join(config_home, "universal-spp")
    return os.path.join(os.path.expanduser("~"), ".universal_spp")


def settings_path(base_dir=None):
    return os.path.join(settings_dir(base_dir), "settings.json")


def _clean_settings(data):
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        settings.update(data)
    settings["auto_check_enabled"] = bool(settings.get("auto_check_enabled"))
    settings["include_prereleases"] = bool(settings.get("include_prereleases"))
    settings["skipped_version"] = normalize_version(settings.get("skipped_version") or "")
    settings["raster_capture_enabled"] = bool(settings.get("raster_capture_enabled"))
    depth = str(settings.get("raster_content_bit_depth") or "source").lower()
    settings["raster_content_bit_depth"] = depth if depth in ("source", "8", "16") else "source"
    padding = str(settings.get("raster_padding") or "transparent").lower()
    settings["raster_padding"] = padding if padding in ("transparent", "infinite") else "transparent"
    settings["keep_failed_raster_captures"] = bool(
        settings.get("keep_failed_raster_captures")
    )
    try:
        budget = int(settings.get("raster_budget_mb") or 512)
    except (TypeError, ValueError):
        budget = 512
    settings["raster_budget_mb"] = max(64, min(4096, budget))
    try:
        timeout = int(settings.get("raster_evaluation_timeout_seconds") or 30)
    except (TypeError, ValueError):
        timeout = 30
    settings["raster_evaluation_timeout_seconds"] = max(5, min(300, timeout))
    try:
        settings["last_checked"] = float(settings.get("last_checked") or 0.0)
    except (TypeError, ValueError):
        settings["last_checked"] = 0.0
    return settings


def load_settings(path=None):
    path = path or settings_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _clean_settings(json.load(f))
    except FileNotFoundError:
        return dict(DEFAULT_SETTINGS)
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(settings, path=None):
    path = path or settings_path()
    settings = _clean_settings(settings)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return settings


def should_auto_check(settings, now=None):
    settings = _clean_settings(settings)
    if not settings["auto_check_enabled"]:
        return False
    now = time.time() if now is None else float(now)
    return now - settings["last_checked"] >= CHECK_INTERVAL_SECONDS


def mark_checked(settings, path=None, now=None):
    settings = _clean_settings(settings)
    settings["last_checked"] = time.time() if now is None else float(now)
    return save_settings(settings, path)


def format_last_checked(settings):
    ts = _clean_settings(settings).get("last_checked") or 0
    if ts <= 0:
        return "Never"
    return time.strftime("%x %X", time.localtime(ts))


def request_json(url, timeout=10):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def request_text(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_releases(timeout=10):
    data = request_json(f"{RELEASES_API_URL}?per_page=30", timeout=timeout)
    if not isinstance(data, list):
        message = data.get("message") if isinstance(data, dict) else ""
        raise UpdateError(message or "GitHub did not return a release list.")
    return data


def release_version(release):
    return normalize_version(release.get("tag_name") or release.get("name") or "")


def select_latest_release(releases, include_prereleases=False):
    candidates = []
    for release in releases or []:
        if release.get("draft"):
            continue
        if release.get("prerelease") and not include_prereleases:
            continue
        ver = release_version(release)
        if version_key(ver):
            candidates.append(release)
    if not candidates:
        return None
    return max(candidates, key=lambda r: version_key(release_version(r)))


def extract_sha256(text):
    match = _SHA_RE.search(text or "")
    return match.group(1).lower() if match else ""


def _checksum_asset_url(assets, zip_name):
    names = {
        (zip_name + ".sha256").lower(),
        (zip_name + ".sha256.txt").lower(),
        "sha256.txt",
        "checksums.txt",
        "sha256sums",
        "sha256sums.txt",
    }
    for asset in assets or []:
        name = str(asset.get("name") or "").lower()
        if name in names:
            return asset.get("browser_download_url") or ""
    return ""


def update_info_from_release(release):
    ver = release_version(release)
    zip_names = release_asset_names(ver)
    assets = release.get("assets") or []
    asset = None
    zip_name = zip_names[0]
    for candidate in zip_names:
        for item in assets:
            if item.get("name") == candidate:
                zip_name = candidate
                asset = item
                break
        if asset is not None:
            break
    if asset is None:
        raise UpdateError(
            f"Release v{ver} does not include a compatible asset ({', '.join(zip_names)})."
        )
    url = asset.get("browser_download_url") or ""
    if not url:
        raise UpdateError(f"Release asset {zip_name} does not have a download URL.")
    return UpdateInfo(
        version=ver,
        tag_name=release.get("tag_name") or f"v{ver}",
        name=release.get("name") or f"Universal SPP v{ver}",
        html_url=release.get("html_url") or RELEASES_PAGE_URL,
        asset_name=zip_name,
        download_url=url,
        # Historical releases had one generic Windows ZIP and put its hash in the
        # release body. Platform-tagged releases may contain several hashes, so use a
        # per-asset checksum file instead of accidentally accepting the first body hash.
        sha256=(
            extract_sha256(release.get("body") or "")
            if zip_name == asset_name_for_version(ver)
            else ""
        ),
        checksum_url=_checksum_asset_url(assets, zip_name),
    )


def get_latest_update(current_version=PLUGIN_VERSION, include_prereleases=False, timeout=10):
    release = select_latest_release(fetch_releases(timeout=timeout), include_prereleases)
    if release is None:
        return None
    ver = release_version(release)
    if compare_versions(ver, current_version) <= 0:
        return None
    return update_info_from_release(release)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalized_zip_name(name):
    name = str(name or "").replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    return name


def _validate_zip_name(name):
    norm = _normalized_zip_name(name)
    if not norm or norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        raise UpdateError(f"Unsafe path in update ZIP: {name}")
    parts = norm.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise UpdateError(f"Unsafe path in update ZIP: {name}")
    if parts[0] != _ZIP_ROOT:
        raise UpdateError(f"Unexpected path in update ZIP: {name}")
    return norm


def validate_update_zip(zip_path, expected_sha256=""):
    if expected_sha256:
        actual = sha256_file(zip_path)
        if actual.lower() != expected_sha256.lower():
            raise UpdateError("The downloaded update did not match the published SHA-256 checksum.")

    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [_validate_zip_name(info.filename) for info in z.infolist()]
    except zipfile.BadZipFile as e:
        raise UpdateError("The downloaded update ZIP is not valid.") from e

    if f"{_ZIP_ROOT}/__init__.py" not in names:
        raise UpdateError("The update ZIP is missing universal_spp_plugin/__init__.py.")
    return True


def safe_extract(zip_path, dest_dir):
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            norm = _validate_zip_name(info.filename)
            target = os.path.join(dest_dir, *norm.split("/"))
            if info.is_dir() or norm.endswith("/"):
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if sys.platform.startswith("linux") and norm == f"{_ZIP_ROOT}/bin/uspp_tool":
                current = os.stat(target).st_mode
                os.chmod(target, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _download_url(url, path, timeout=60, progress=None):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(path, "wb") as f:
        try:
            total = int(r.headers.get("Content-Length") or "0")
        except ValueError:
            total = 0
        read = 0
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            read += len(chunk)
            if progress:
                progress("Downloading update...", (read / total) if total else None)


def download_update(info, temp_dir=None, timeout=60, progress=None):
    work_dir = tempfile.mkdtemp(prefix="uspp_update_download_", dir=temp_dir)
    zip_path = os.path.join(work_dir, info.asset_name)
    _download_url(info.download_url, zip_path, timeout=timeout, progress=progress)
    expected = info.sha256
    if not expected and info.checksum_url:
        expected = extract_sha256(request_text(info.checksum_url, timeout=timeout))
    if progress:
        progress("Verifying update...", None)
    validate_update_zip(zip_path, expected)
    return zip_path, work_dir, expected


def install_zip(zip_path, plugin_root=None, temp_dir=None, copytree_func=None, rmtree_func=None):
    plugin_root = os.path.abspath(plugin_root or _PLUGIN_ROOT)
    if os.path.basename(plugin_root) != _ZIP_ROOT:
        raise UpdateError(f"Plugin root must be named {_ZIP_ROOT}: {plugin_root}")
    if not os.path.isdir(plugin_root):
        raise UpdateError(f"Plugin root not found: {plugin_root}")

    copytree_func = copytree_func or shutil.copytree
    rmtree_func = rmtree_func or shutil.rmtree
    parent = os.path.dirname(plugin_root)
    install_tmp = tempfile.mkdtemp(prefix="uspp_update_install_", dir=temp_dir)
    backup_tmp = tempfile.mkdtemp(prefix="uspp_plugin_backup_", dir=temp_dir)
    backup_root = os.path.join(backup_tmp, _ZIP_ROOT)
    extract_root = os.path.join(install_tmp, "extract")
    stage_root = os.path.join(extract_root, _ZIP_ROOT)

    try:
        validate_update_zip(zip_path)
        safe_extract(zip_path, extract_root)
        if not os.path.isdir(stage_root):
            raise UpdateError("The update ZIP did not extract to a plugin folder.")

        copytree_func(plugin_root, backup_root)
        try:
            if os.path.exists(plugin_root):
                rmtree_func(plugin_root)
            copytree_func(stage_root, os.path.join(parent, _ZIP_ROOT))
        except Exception as e:
            try:
                if os.path.exists(plugin_root):
                    rmtree_func(plugin_root)
                copytree_func(backup_root, plugin_root)
            except Exception as rollback_error:
                raise UpdateError(
                    f"Update failed and rollback also failed. Backup is at {backup_root}."
                ) from rollback_error
            raise UpdateError("Update installation failed; the previous plugin was restored.") from e
    finally:
        for path in (install_tmp, backup_tmp):
            if os.path.exists(path):
                try:
                    rmtree_func(path)
                except Exception:
                    pass
    return True


def install_update(info, plugin_root=None, timeout=60, progress=None):
    work_dir = None
    try:
        zip_path, work_dir, _ = download_update(info, timeout=timeout, progress=progress)
        if progress:
            progress("Installing update...", None)
        return install_zip(zip_path, plugin_root=plugin_root)
    finally:
        if work_dir and os.path.exists(work_dir):
            try:
                shutil.rmtree(work_dir)
            except OSError:
                pass
