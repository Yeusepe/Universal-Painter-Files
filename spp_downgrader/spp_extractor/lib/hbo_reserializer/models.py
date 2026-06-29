"""Small value types and the primitive-size loader for the HBO reserializer."""
import os
import sys
import json


def _load_registry_primitive_sizes(defaults):
    """Merge the 'registry' primitive-size table from profiles/primitive_sizes.json
    over the hardcoded defaults. Lets a new primitive (e.g. v12.1 code 22) be added
    as data instead of code. Missing file -> defaults unchanged."""
    if getattr(sys, "frozen", False):
        path = os.path.join(sys._MEIPASS, "profiles", "primitive_sizes.json")
    else:
        # this module lives at lib/hbo_reserializer/models.py -> profiles is three up.
        path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "profiles", "primitive_sizes.json"))
    merged = dict(defaults)
    try:
        with open(path, "r", encoding="utf-8") as f:
            merged.update({int(k): int(v) for k, v in json.load(f).get("registry", {}).items()})
    except Exception:
        pass
    return merged


class MemberDef:
    def __init__(self, name, type_code):
        self.name = name
        self.type_code = type_code


class ObjectDef:
    def __init__(self, name):
        self.name = name
        self.members = []
