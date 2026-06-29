"""Self-check for identity-preserving transform passes."""
import os, sys, struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from lib.hbo_reserializer import HBOSerializer, runtime


def _ser():
    return HBOSerializer(struct.pack("<III", 0x1B7C2FDD, 1, 17))


def prim(code, b):
    return ("primitive", code, b)


def test_drop_unknown_identity_and_drop():
    s = _ser()
    runtime.TARGET_MEMBERS = frozenset({"keep", "x", "DataTimelineKey", "type", "value"})
    obj = ("DataTimelineKey", [("type", 9, prim(9, b"\0\0\0\0")),
                               ("value", 1, prim(1, b"\0\0\0\0")),
                               ("x", 1, prim(1, b"\0\0\0\0"))])
    assert s._drop_unknown_members(obj) is obj, "unchanged drop must return same object"
    obj2 = ("T", [("keep", 1, prim(1, b"\1")), ("gone", 1, prim(1, b"\2"))])
    out = s._drop_unknown_members(obj2)
    assert out is not obj2 and [f[0] for f in out[1]] == ["keep"], out
    runtime.TARGET_MEMBERS = None
    assert s._drop_unknown_members(obj2) is obj2, "TARGET_MEMBERS=None must be a no-op"


def test_narrow_identity_and_change():
    s = _ser()
    runtime.PRIMITIVE_RETYPE = {22: 12}
    obj = ("T", [("a", 1, prim(1, b"\0\0\0\0"))])
    assert s._narrow_primitives(obj) is obj
    obj2 = ("T", [("a", 22, prim(22, b"\xff" * 16))])
    out = s._narrow_primitives(obj2)
    assert out is not obj2 and out[1][0][1] == 12 and len(out[1][0][2][2]) == 8, out
    runtime.PRIMITIVE_RETYPE = {}
    assert s._narrow_primitives(obj2) is obj2, "empty PRIMITIVE_RETYPE must be a no-op"


def test_project_identity_and_reorder():
    s = _ser()
    runtime.V10_SCHEMA = {"DataTimelineKey": ["type", "value", "x"]}
    runtime.SCHEMA_DEFAULTS = {}
    fields = [("type", 9, prim(9, b"\0\0\0\0")), ("value", 1, prim(1, b"\0\0\0\0")),
              ("x", 1, prim(1, b"\0\0\0\0"))]
    obj = ("DataTimelineKey", fields)
    out = s._project_obj_to_v10_schema(obj)
    assert [f[0] for f in out[1]] == ["type", "value", "x"], out
    untouched = ("UnknownType", [("z", 1, prim(1, b"\0"))])
    assert s._project_obj_to_v10_schema(untouched) is untouched


if __name__ == "__main__":
    test_drop_unknown_identity_and_drop()
    test_narrow_identity_and_change()
    test_project_identity_and_reorder()
    print("identity self-check passed")
