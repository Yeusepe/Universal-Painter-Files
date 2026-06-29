"""Translate a composed downgrade profile into plain-English, client-facing
statements about what data/features are lost when converting to an older version.

Only GENUINELY lossy categories are reported:
  - blacklist        : types/fields dropped entirely (data removed)
  - field_retype     : a primitive whose width shrank (conditional precision loss)
Faithful transforms (type/field renames, field_rekind object<->array,
field_value_transform enum<->bitmask) preserve the data and are NOT reported.

Messages come from lossiness_messages.json (data-driven: exact -> glob pattern ->
humanized generic fallback), so an uncurated key still yields readable prose.
"""
import os
import sys
import re
import json
import fnmatch


def _msg_path():
    """Frozen (PyInstaller): bundled under profiles/. Source: spp_downgrader root."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "profiles", "lossiness_messages.json")
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "lossiness_messages.json"))


_MSG_PATH = _msg_path()


def _load_messages():
    try:
        with open(_MSG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def humanize(name):
    """'DataColorGradingParametersV2' -> 'color grading parameters';
    'Type.field' -> 'field'. Strips Data/Settings prefixes and V2 suffix, splits CamelCase."""
    name = name.split(".")[-1]
    name = re.sub(r"V2$", "", name)
    name = re.sub(r"^(Data|Settings)", "", name)
    # split camelCase / PascalCase and digit groups
    parts = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", name)
    return " ".join(p.lower() for p in parts) if parts else name.lower()


def _message(rules, key):
    """exact -> first matching glob pattern -> humanized generic."""
    if not rules:
        return None
    exact = rules.get("exact", {})
    if key in exact:
        return exact[key]
    for pat in rules.get("patterns", []):
        if fnmatch.fnmatch(key, pat["match"]):
            return pat["message"]
    gen = rules.get("generic")
    return gen.replace("{human}", humanize(key)) if gen else None


def build_lossiness_report(profile, messages=None):
    """Return a deduped, sorted list of human-readable loss statements for the
    composed MigrationProfile (downgrade direction)."""
    messages = messages if messages is not None else _load_messages()
    seen, out = set(), []

    def add(rules_key, key):
        msg = _message(messages.get(rules_key), key)
        if msg and msg not in seen:
            seen.add(msg)
            out.append(msg)

    for entry in profile.blacklist:
        add("blacklist", entry)
    for key in profile.field_retype:
        add("field_retype", key)

    return sorted(out)


if __name__ == "__main__":
    # self-check: humanizer + dedupe + pattern collapse
    assert humanize("DataColorGradingParametersV2") == "color grading parameters", humanize("DataColorGradingParametersV2")
    assert humanize("SettingsSymmetry.enabled") == "enabled"
    msgs = {"blacklist": {"patterns": [{"match": "*ParametersV2", "message": "FX reset."}], "generic": "{human} removed."},
            "field_retype": {"patterns": [{"match": "*.channelTypes", "message": "channels>64 dropped."}]}}

    class P:
        blacklist = ["DataBloomParametersV2", "bloomParametersV2", "WidgetThing"]
        field_retype = {"DataActionFill.channelTypes": 12, "DataBlending.channelTypes": 12}
    rep = build_lossiness_report(P(), msgs)
    assert "FX reset." in rep and rep.count("FX reset.") == 1, rep          # 2 V2 entries -> 1 line
    assert "channels>64 dropped." in rep and sum(x == "channels>64 dropped." for x in rep) == 1, rep
    assert "widget thing removed." in rep, rep                              # generic fallback
    print("lossiness self-check OK ->", rep)
