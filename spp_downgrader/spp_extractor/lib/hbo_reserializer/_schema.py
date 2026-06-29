"""SchemaMixin for HBOSerializer (see serializer.py). Split out for organization."""
from . import runtime


class SchemaMixin:
    def _project_obj_to_v10_schema(self, obj, depth=0):
        """Keep only members v10's schema defines for this object's type, in v10
        order; recurse into nested objects/arrays. Types absent from the schema are
        left untouched (only recursed) so unknown/uncovered types degrade gracefully."""
        if depth > self.MAX_RECURSION:
            return obj
        obj_name, fields = obj
        if fields is None:
            return (obj_name, None)
        fields = [self._project_field_value(f, depth) for f in fields]
        schema = runtime.V10_SCHEMA.get(obj_name)
        if schema:
            order = {name: i for i, name in enumerate(schema)}
            allowed = [f for f in fields if f[0] in order]
            # Synthesize any required member the source lacks, so the older reader
            # never hits "No value defined for member" (e.g. DataChannel.userIsColorManaged
            # which v12.1 renamed). Default value comes from the native target schema.
            present = {f[0] for f in allowed}
            for m in schema:
                if m not in present:
                    syn = self._default_field(obj_name, m)
                    if syn is not None:
                        allowed.append(syn)
            allowed.sort(key=lambda f: order.get(f[0], len(order)))
            return (obj_name, allowed)
        return (obj_name, fields)

    @staticmethod
    def _deser_default(sv):
        """Reconstruct a native value from a stored serialized default (see automap
        _ser_val): ['p',code,hex] | ['s',hex] | ['o',name,fields] | ['o',null] | ['a',[...]]."""
        k = sv[0]
        if k == "p":
            return ("primitive", int(sv[1]), bytes.fromhex(sv[2]))
        if k == "s":
            return ("string", bytes.fromhex(sv[1]))
        if k == "o":
            if len(sv) < 3 or sv[1] is None:
                return ("object", ("", None))                      # null object
            name = sv[1]
            fields = [(fn, SchemaMixin._tc_of(cv), SchemaMixin._deser_default(cv)) for fn, cv in sv[2]]
            return ("object", (name, fields))
        if k == "a":
            elems = [SchemaMixin._deser_default(e) for e in sv[1]]
            kinds = {e[0] for e in elems}
            elem_kind = "object" if (not kinds or "object" in kinds) else next(iter(kinds))
            return ("array", (elem_kind, elems))
        return ("primitive", 0, b"")

    @staticmethod
    def _tc_of(sv):
        k = sv[0]
        if k == "p":
            return int(sv[1]) if len(sv) > 1 else 0
        return {"s": 0x10, "o": 0x12, "a": 0x13}.get(k, 0)

    def _default_field(self, type_name, member):
        """Build a (name, tcode, value) field for a missing member from the target
        schema defaults (scalar or full subtree), or None if no default is known."""
        sv = runtime.SCHEMA_DEFAULTS.get(type_name, {}).get(member)
        if not sv:
            return None
        return (member, self._tc_of(sv), self._deser_default(sv))

    def _project_field_value(self, field, depth):
        name, tcode, value = field[0], field[1], field[2]
        kind = value[0]
        if kind == "object":
            child = value[1]
            if isinstance(child, tuple):
                cname, cfields = child
                # A v11 null object (flag 0) decodes to ("", []). v10 represents
                # that as a null pointer (0xFF), not an empty object body. Generic
                # rule: empty + nameless object -> v10 null.
                if not cname and not cfields:
                    value = ("object_null", b"")
                else:
                    value = ("object", self._project_obj_to_v10_schema(child, depth + 1))
        elif kind == "array":
            elem_kind, elems = value[1]
            if elem_kind == "object":
                new_elems = []
                for elem in elems:
                    if elem[0] == "object" and isinstance(elem[1], tuple):
                        new_elems.append(("object", self._project_obj_to_v10_schema(elem[1], depth + 1)))
                    else:
                        new_elems.append(elem)
                value = ("array", ("object", new_elems))
        return (name, tcode, value)

