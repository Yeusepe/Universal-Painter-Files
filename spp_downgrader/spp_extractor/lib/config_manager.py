"""Load the legacy fallback blacklist and maximum HBO data version."""

import json
from pathlib import Path

try:
    import yaml
except ImportError:  # The JSON fallback still works without PyYAML.
    yaml = None


DEFAULT_RULES = (["BakingCommonParameters", "DataTweakInt", "DataTweakInt2"], 81)


def _find_config(config_path=None):
    if config_path:
        return Path(config_path)
    names = ("downgrade_config.yaml", "downgrade_config.yml", "downgrade_config.json")
    roots = (Path(__file__).resolve().parent.parent / "config", Path.cwd())
    return next((root / name for root in roots for name in names if (root / name).exists()), None)


def load_config(config_path=None):
    """Return ``(blacklist, max_data_version)`` from the fallback config."""
    path = _find_config(config_path)
    if path is None:
        return DEFAULT_RULES
    if not path.exists():
        print("Warning: Config file '%s' not found, using defaults" % path)
        return DEFAULT_RULES
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) if yaml and path.suffix in (".yaml", ".yml") else json.load(stream)
        data = data or {}
        blacklist = list((data.get("dict_removal") or {}).get("blacklist") or [])
        max_version = int((data.get("target_version") or {}).get("max_data_version", 81))
        return blacklist, max_version
    except Exception as exc:
        print("Warning: Error loading config file: %s" % exc)
        return DEFAULT_RULES
