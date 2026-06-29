"""Diff two decoded HBO trees (UNode) into classified, profile-ready differences.

Same-content corpus assumption: SRC (higher version) and TGT (lower version) hold the
same project, so any structural difference is a pure version difference. We collect,
per object type, its field-name set and a value-signature list per field, then classify:

  type_rename     SRC-only type whose field set exactly matches a TGT-only type
  blacklist_type  SRC-only type with no field-equal TGT twin (gone in target)
  field_rename    field gone from a type, replaced by a value-equal field at same slot
  blacklist_field field gone, and the type isn't in the target schema (projection
                  can't drop it); otherwise schema projection handles it (noop)
  baker_rename /  same-named string field (bakerId / DataTweak identifier) whose values
  tweak_rename    map 1:1 onto different target strings (baking renames, generically)
  value_rename    any other same-named string field with a consistent 1:1 value remap

Confidence: HIGH auto-applies; MEDIUM asks unless a stored decision confirms; LOW asks.
"""

MAX_DEPTH = 64


def value_sig(v, depth=0):
    """Hashable signature of a canonical Value, for equality comparison."""
    if depth > MAX_DEPTH:
        return ("...",)
    k = v[0]
    if k == "primitive":
        return ("p", v[2])
    if k == "string":
        return ("s", v[1])
    if k == "object":
        if v[1] is None:
            return ("o", None)
        n, fields = v[1]
        return ("o", n, tuple(sorted((fn, value_sig(fv, depth + 1)) for fn, fv in fields)))
    if k == "array":
        return ("a", tuple(value_sig(e, depth + 1) for e in v[1]))
    return (k,)


def collect(node, types=None, fvals=None, pcodes=None, depth=0):
    """Walk a UNode. types: type_name -> set(field_names). fvals: (type,field) ->
    [value_sig,...]. pcodes: (type,field) -> set(primitive type codes seen)."""
    if types is None:
        types, fvals, pcodes = {}, {}, {}
    if node is None or depth > MAX_DEPTH:
        return types, fvals, pcodes
    tname, fields = node
    fset = types.setdefault(tname, set())
    for fn, val in fields:
        fset.add(fn)
        fvals.setdefault((tname, fn), []).append(value_sig(val))
        if val[0] == "primitive":
            pcodes.setdefault((tname, fn), set()).add(val[1])
        _recurse(val, types, fvals, pcodes, depth + 1)
    return types, fvals, pcodes


def _recurse(val, types, fvals, pcodes, depth):
    if depth > MAX_DEPTH:
        return
    if val[0] == "object" and val[1] is not None:
        collect(val[1], types, fvals, pcodes, depth)
    elif val[0] == "array":
        for e in val[1]:
            _recurse(e, types, fvals, pcodes, depth)


def _trivial(sig):
    """A value carrying no information (empty / all-zero) — too weak to anchor a rename."""
    if sig[0] in ("p", "s"):
        return not sig[1] or set(sig[1]) == {0}
    if sig[0] == "o" and sig[1] is None:
        return True
    if sig[0] == "a" and not sig[1]:
        return True
    return False


def _vals_equal(a, b):
    """Field value-lists equal and anchored by at least one non-trivial value."""
    if not a or not b or a != b:
        return False
    return any(not _trivial(s) for s in a)


def _kind_of(siglist):
    """Dominant value kind ('o','a','s','p') for a field's value-sig list."""
    return siglist[0][0] if siglist else None


def _pair_walk(sn, gn, acc, depth=0):
    """Same-content lockstep walk: collect (type,field) -> [(src_bytes, tgt_bytes)] for
    primitive leaves, so value relationships (e.g. enum->bitmask) can be inferred."""
    if sn is None or gn is None or sn[1] is None or gn[1] is None or depth > MAX_DEPTH:
        return
    t = sn[0]
    gd = {fn: v for fn, v in gn[1]}
    for fn, sv in sn[1]:
        gv = gd.get(fn)
        if gv is None:
            continue
        if sv[0] == "primitive" and gv[0] == "primitive":
            acc.setdefault((t, fn), []).append((sv[2], gv[2]))
        elif sv[0] == "object" and gv[0] == "object" and sv[1] and gv[1]:
            _pair_walk(sv[1], gv[1], acc, depth + 1)
        elif sv[0] == "array" and gv[0] == "array":
            for se, ge in zip(sv[1], gv[1]):
                if se[0] == "object" and ge[0] == "object" and se[1] and ge[1]:
                    _pair_walk(se[1], ge[1], acc, depth + 1)


def diff(src_node, tgt_node, target_schema_types=None):
    """Return a list of classified difference dicts (see module docstring)."""
    target_schema_types = target_schema_types or set()
    st, sf, sp = collect(src_node)
    tt, tf, tp = collect(tgt_node)
    pairs = {}
    _pair_walk(src_node, tgt_node, pairs)
    out = []

    src_only = set(st) - set(tt)
    tgt_only = set(tt) - set(st)
    used_tgt, renamed = set(), set()

    # --- type renames: SRC-only type whose field set exactly matches a TGT-only type
    for s in sorted(src_only):
        cand = [t for t in tgt_only if t not in used_tgt and tt[t] == st[s]]
        if len(cand) == 1:
            used_tgt.add(cand[0]); renamed.add(s)
            out.append(_d("TYPE", "type_rename", "HIGH", src_type=s, tgt_type=cand[0],
                          src_fieldset=sorted(st[s])))
        elif len(cand) > 1:
            out.append(_d("TYPE", "ambiguous", "LOW", src_type=s, src_fieldset=sorted(st[s]),
                          note="field-equal to multiple targets: " + ", ".join(sorted(cand))))

    # --- SRC-only types with no twin: gone in target -> blacklist. BUT only if the
    # type is absent from the target EVERYWHERE (target_schema_types is the global set
    # across all target streams). A type missing from this one stream yet present in
    # another (e.g. DataTweakBool: absent from baking, present in viewersettings) must
    # NOT be blacklisted globally, or it gets nuked where it's valid.
    for s in sorted(src_only - renamed):
        if s in target_schema_types:
            continue
        if not any(d["src_type"] == s and d["action"] == "ambiguous" for d in out):
            out.append(_d("TYPE_DROP", "blacklist_type", "HIGH", src_type=s, src_fieldset=sorted(st[s])))

    # --- field-level diffs for types present in both
    for t in sorted(set(st) & set(tt)):
        missing = st[t] - tt[t]
        extra = tt[t] - st[t]
        matched_extra = set()
        for f in sorted(missing):
            cand = [g for g in extra if g not in matched_extra and _vals_equal(sf.get((t, f)), tf.get((t, g)))]
            if len(cand) == 1:
                matched_extra.add(cand[0])
                out.append(_d("FIELD_RENAME", "field_rename", "MEDIUM", src_type=t,
                              field_name=f, tgt_field=cand[0], src_fieldset=sorted(st[t])))
            elif t in target_schema_types:
                out.append(_d("FIELD_DROP", "noop_projection", "HIGH", src_type=t,
                              field_name=f, src_fieldset=sorted(st[t])))
            else:
                out.append(_d("FIELD_DROP", "blacklist_field", "MEDIUM", src_type=t,
                              field_name=f, src_fieldset=sorted(st[t])))

        # --- primitive retype / value transform: same-named field whose primitive type
        #     code changed. A change can be a pure width change (channelTypes 16B->8B) OR
        #     a SEMANTIC remap (DataChannel.type: enum index n -> bitmask 1<<n). Tell them
        #     apart by checking the relationship across positionally-paired instances.
        for f in sorted(st[t] & tt[t]):
            sc, tc = sp.get((t, f)), tp.get((t, f))
            if not (sc and tc and len(sc) == 1 and len(tc) == 1 and sc != tc):
                continue
            from_code, to_code = next(iter(sc)), next(iter(tc))
            pr = [(int.from_bytes(sv, "little"), int.from_bytes(gv, "little"))
                  for sv, gv in pairs.get((t, f), [])]
            if pr and all(s < 64 and g == (1 << s) for s, g in pr):
                out.append(_d("FIELD_VALUE_TRANSFORM", "field_value_transform", "HIGH",
                              src_type=t, field_name=f, op="enum_to_bitmask", to_code=to_code))
            elif pr and all(s == g for s, g in pr):
                out.append(_d("FIELD_RETYPE", "field_retype", "HIGH", src_type=t,
                              field_name=f, from_code=from_code, to_code=to_code))
            else:
                # unknown relationship (or no pairs): width-resize best effort + flag
                out.append(_d("FIELD_RETYPE", "field_retype",
                              "HIGH" if not pr else "MEDIUM", src_type=t,
                              field_name=f, from_code=from_code, to_code=to_code))

        # --- container rekind: same-named field that changed object<->array
        #     (e.g. v12.1 made DataAction*.sourceTransparent an object; older = array).
        for f in sorted(st[t] & tt[t]):
            sk, tk = _kind_of(sf.get((t, f))), _kind_of(tf.get((t, f)))
            if sk and tk and sk != tk and {sk, tk} <= {"o", "a"}:
                out.append(_d("FIELD_REKIND", "field_rekind", "HIGH", src_type=t,
                              field_name=f, to_kind=("array" if tk == "a" else "object")))

        # --- value renames on same-named string fields (catches baking ids/identifiers)
        for f in sorted(st[t] & tt[t]):
            sv, tv = sf.get((t, f)), tf.get((t, f))
            if not sv or not tv or len(sv) != len(tv) or sv == tv:
                continue
            if not all(x[0] == "s" for x in sv) or not all(x[0] == "s" for x in tv):
                continue
            # A real rename changes the value SET; an identical set in a different
            # order is just reordering (cyclic A->B->C->A) -> not a rename. Map only
            # strings that truly disappear in src onto strings that truly appear in tgt.
            src_set = {x[1] for x in sv}
            tgt_set = {x[1] for x in tv}
            disappeared, appeared = src_set - tgt_set, tgt_set - src_set
            if not disappeared or not appeared:
                continue
            mapping, ok = {}, True
            for a, b in zip(sv, tv):
                if a == b or a[1] not in disappeared or b[1] not in appeared:
                    continue
                if a[1] in mapping and mapping[a[1]] != b[1]:
                    ok = False; break
                mapping[a[1]] = b[1]
            if ok and mapping:
                action = {"bakerId": "baker_rename", "identifier": "tweak_rename"}.get(f, "value_rename")
                out.append(_d("VALUE_RENAME", action, "MEDIUM", src_type=t, field_name=f,
                              mapping={k.decode("utf-8", "replace"): v.decode("utf-8", "replace")
                                       for k, v in mapping.items()}))
    return out


def _d(kind, action, confidence, **kw):
    d = {"kind": kind, "action": action, "confidence": confidence,
         "src_type": kw.get("src_type"), "tgt_type": kw.get("tgt_type"),
         "field_name": kw.get("field_name"), "src_fieldset": kw.get("src_fieldset")}
    d.update(kw)
    return d


if __name__ == "__main__":
    # synthetic self-check covering each classification
    def obj(t, **fields):
        return (t, [(k, v) for k, v in fields.items()])
    def s(b):
        return ("string", b)
    def p(b):
        return ("primitive", 9, b)

    src = obj("Root",
              brush=("object", obj("DataBrushStamp", size=p(b"\x01\x00\x00\x00"))),     # renamed type
              gone=("object", obj("OldType", x=p(b"\x05\x00\x00\x00"))),                 # dropped type
              mat=("object", obj("DataMaterial", oldName=s(b"hello"), keep=p(b"\x09\x00\x00\x00"))),  # field rename
              bake=("array", [("object", obj("Baker", bakerId=s(b"Normal"))),
                              ("object", obj("Baker", bakerId=s(b"Color")))]))
    tgt = obj("Root",
              brush=("object", obj("DataBrush", size=p(b"\x01\x00\x00\x00"))),
              mat=("object", obj("DataMaterial", newName=s(b"hello"), keep=p(b"\x09\x00\x00\x00"))),
              bake=("array", [("object", obj("Baker", bakerId=s(b"GLMapBakerManager.NormalFromDetail"))),
                              ("object", obj("Baker", bakerId=s(b"GLMapBakerManager.ColorFromDetail")))]))
    # reordering must NOT be mistaken for a value rename (cyclic A->B->C->A)
    src_ro = obj("Root2", tw=("array", [("object", obj("Tw", id=s(b"A"))),
                                        ("object", obj("Tw", id=s(b"B")))]))
    tgt_ro = obj("Root2", tw=("array", [("object", obj("Tw", id=s(b"B"))),
                                        ("object", obj("Tw", id=s(b"A")))]))
    assert not any(d["action"] in ("value_rename", "tweak_rename", "baker_rename") for d in diff(src_ro, tgt_ro)), "reorder must not be a rename"

    ds = diff(src, tgt, target_schema_types={"DataMaterial"})
    by = {(d["action"]) for d in ds}
    assert any(d["action"] == "type_rename" and d["src_type"] == "DataBrushStamp" and d["tgt_type"] == "DataBrush" for d in ds), ds
    assert any(d["action"] == "blacklist_type" and d["src_type"] == "OldType" for d in ds), ds
    assert any(d["action"] == "field_rename" and d["field_name"] == "oldName" and d["tgt_field"] == "newName" for d in ds), ds
    assert any(d["action"] == "baker_rename" and d["mapping"].get("Normal", "").endswith("NormalFromDetail") for d in ds), ds
    print("hbo_diff self-check OK ->", sorted(by))
