"""Persisted decisions for the auto-mapper ("learning").

When the mapper can't infer a difference with confidence it asks the user; the answer
is saved here keyed by a PATH-INDEPENDENT signature of the diff, so the same logical
difference (e.g. a type rename) is resolved once and reused everywhere and on re-runs.

Store: profiles/decisions/v{from}_to_v{to}.json
  { "<sig>": {action, value, diff_kind, description, decided_by, decided_on} }

action ∈ rename | field_rename | drop | keep | baker_rename | tweak_rename
value:  target name (rename), {"from","to"} (field/baker/tweak rename), or null (drop/keep)
"""
import os
import json
import hashlib

_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "profiles", "decisions"))


def signature(diff):
    """Stable key independent of tree path / array index, so one decision covers all
    occurrences and survives unrelated edits."""
    parts = [
        diff.get("kind", ""),
        diff.get("src_type") or "",
        ",".join(sorted(diff.get("src_fieldset") or [])),
        diff.get("field_name") or "",
        diff.get("tgt_type") or "",
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _path(from_v, to_v):
    return os.path.join(_DIR, f"v{from_v}_to_v{to_v}.json")


def load(from_v, to_v):
    try:
        with open(_path(from_v, to_v), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save(from_v, to_v, store):
    os.makedirs(_DIR, exist_ok=True)
    with open(_path(from_v, to_v), "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, sort_keys=True)


def resolve(store, diff):
    """Return the stored resolution for this diff, or None."""
    return store.get(signature(diff))


def record(store, diff, action, value, decided_by, today):
    """Add/replace a decision. `today` is passed in (no Date.now in some contexts)."""
    store[signature(diff)] = {
        "action": action,
        "value": value,
        "diff_kind": diff.get("kind"),
        "description": diff.get("description") or _describe(diff),
        "decided_by": decided_by,
        "decided_on": today,
    }
    return store


def _describe(diff):
    k = diff.get("kind")
    st, tt = diff.get("src_type"), diff.get("tgt_type")
    fn = diff.get("field_name")
    if k == "TYPE":
        return f"type {st} (fields {{{','.join(sorted(diff.get('src_fieldset') or []))}}}) -> {tt or '?'}"
    if k in ("FIELD_MISSING_IN_TARGET", "FIELD_EXTRA_IN_TARGET"):
        return f"{k} {st}.{fn}"
    return f"{k} {st or ''} {fn or ''}".strip()


if __name__ == "__main__":
    # self-check: signature is stable & path-independent; round-trips
    d1 = {"kind": "TYPE", "src_type": "DataBrushStamp", "tgt_type": "DataBrush",
          "src_fieldset": ["b", "a"], "field_name": None}
    d2 = dict(d1, src_fieldset=["a", "b"])         # reordered fieldset -> same sig
    assert signature(d1) == signature(d2), "signature must ignore fieldset order"
    d3 = dict(d1, src_type="Other")
    assert signature(d1) != signature(d3), "different type -> different sig"
    s = {}
    record(s, d1, "rename", "DataBrush", "interactive", "2026-06-26")
    assert resolve(s, d2)["value"] == "DataBrush", "resolve must match reordered twin"
    print("decisions_store self-check OK")
