"""Non-mutating raster fallback planner for decoded HBO layer graphs.

This module deliberately does not rewrite the graph. It only records where a
downgrade would need source-version pixels to preserve appearance while keeping
as much editable structure as possible.
"""
import hashlib
import json

from . import runtime
from . import _classify


S_SOURCE = "source"
S_MASK_STACK = "mask_stack"
S_CONTENT_ACTION = "content_action"
S_LAYER = "layer"
S_GROUP = "group"
S_FULL_STACK_CHANNEL = "full_stack_channel"

K_MASK = "mask"
K_CONTENT = "content"
K_CHANNEL = "channel"


_SPAN_DEPENDENT_PREFIXES = (
    "DataActionEditor",
    "DataActionGenerator",
)
_LOCAL_CONTENT_PREFIXES = (
    "DataSource",
)


def _u32(raw):
    if not raw or len(raw) < 4:
        return None
    return int.from_bytes(raw[:4], "little", signed=False)


def _primitive_int(value):
    if not value or value[0] != "primitive":
        return None
    raw = value[2]
    if not raw:
        return None
    return int.from_bytes(raw[:min(len(raw), 8)], "little", signed=False)


def _field(fields, name):
    if not fields:
        return None
    for f in fields:
        if f[0] == name:
            return f
    return None


def _object_uid(obj):
    if not obj or not obj[1]:
        return None
    f = _field(obj[1], "uid")
    if not f:
        return None
    return _primitive_int(f[2])


def _label(obj):
    if not obj or not obj[1]:
        return None
    f = _field(obj[1], "label")
    if f and f[2][0] == "string":
        try:
            return f[2][1].decode("utf-8", "replace")
        except Exception:
            return None
    return None


def _blend_mode(obj):
    if not obj or obj[0] != "DataBlending" or not obj[1]:
        return None
    f = _field(obj[1], "blendingMode")
    if f:
        return _primitive_int(f[2])
    return None


class RasterRequest:
    __slots__ = (
        "id", "dataset", "target", "scope", "kind", "object_type", "reason",
        "path", "layer_uid", "stack_uid", "object_uid", "label",
        "material_index", "stack_index", "capture",
        "preserves_editability", "visual_confidence",
    )

    def __init__(self, *, dataset=None, target=None, scope=None, kind=None,
                 object_type=None, reason="", path=(), layer_uid=None,
                 stack_uid=None, object_uid=None, label=None,
                 material_index=None, stack_index=None, capture=None,
                 preserves_editability="partial", visual_confidence="exact"):
        self.dataset = dataset
        self.target = target
        self.scope = scope
        self.kind = kind
        self.object_type = object_type
        self.reason = reason
        self.path = tuple(path or ())
        self.layer_uid = layer_uid
        self.stack_uid = stack_uid
        self.object_uid = object_uid
        self.label = label
        self.material_index = material_index
        self.stack_index = stack_index
        self.capture = capture or {}
        self.preserves_editability = preserves_editability
        self.visual_confidence = visual_confidence
        self.id = self._id()

    def _id(self):
        if self.scope == S_MASK_STACK:
            boundary = self.layer_uid
        elif self.scope == S_LAYER:
            boundary = self.layer_uid
        elif self.scope == S_GROUP:
            boundary = self.object_uid or self.layer_uid
        elif self.scope == S_FULL_STACK_CHANNEL:
            boundary = self.stack_uid or self.dataset
        else:
            boundary = self.object_uid or self.path
        seed = {
            "dataset": self.dataset,
            "target": self.target,
            "scope": self.scope,
            "kind": self.kind,
            "boundary": boundary,
        }
        raw = json.dumps(seed, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "rf_" + hashlib.sha256(raw).hexdigest()[:16]

    def to_dict(self):
        return {
            "id": self.id,
            "dataset": self.dataset,
            "target": self.target,
            "scope": self.scope,
            "kind": self.kind,
            "object_type": self.object_type,
            "reason": self.reason,
            "path": list(self.path),
            "layer_uid": self.layer_uid,
            "stack_uid": self.stack_uid,
            "object_uid": self.object_uid,
            "label": self.label,
            "material_index": self.material_index,
            "stack_index": self.stack_index,
            "capture": self.capture,
            "preserves_editability": self.preserves_editability,
            "visual_confidence": self.visual_confidence,
        }


class RasterPlanner:
    def __init__(self, *, classifier=None, dataset=None, target=None):
        if classifier is None:
            classifier = _classify.Classifier.from_runtime(
                blend_max=getattr(runtime, "BLEND_MAX", None)
            )
            classifier.unknown_appearance_unsupported = True
        self.classifier = classifier
        self.dataset = dataset
        self.target = target
        self.requests = []
        self._seen = {}

    def collect(self, root):
        self._visit_obj(root, ctx={
            "substack": None,
            "path": (),
            "layer_uid": None,
            "stack_uid": None,
            "in_fill_sources": False,
            "group_uid": None,
            "material_index": None,
            "stack_index": None,
        })
        return [r.to_dict() for r in self.requests]

    def _add(self, req):
        existing = self._seen.get(req.id)
        if existing is not None:
            if req.reason and req.reason not in existing.reason:
                existing.reason = "; ".join([r for r in (existing.reason, req.reason) if r])
            if req.object_type and req.object_type not in (existing.object_type or "").split(","):
                existing.object_type = ",".join([r for r in (existing.object_type, req.object_type) if r])
            return
        self._seen[req.id] = req
        self.requests.append(req)

    def _scope_for(self, obj_name, ctx, verdict):
        if ctx.get("substack") == _classify.MASK:
            return S_MASK_STACK, K_MASK
        if ctx.get("in_fill_sources") and obj_name.startswith("DataSource"):
            return S_SOURCE, K_CONTENT
        if verdict.granularity == _classify.G_COMPOSITE:
            return S_GROUP if ctx.get("group_uid") else S_FULL_STACK_CHANNEL, K_CHANNEL
        if obj_name.startswith(_SPAN_DEPENDENT_PREFIXES):
            return S_GROUP if ctx.get("group_uid") else S_FULL_STACK_CHANNEL, K_CHANNEL
        if obj_name.startswith(_LOCAL_CONTENT_PREFIXES):
            return S_SOURCE, K_CONTENT
        if obj_name.startswith("DataAction"):
            return S_CONTENT_ACTION, K_CONTENT
        if obj_name.startswith("DataLayer"):
            return S_LAYER, K_CHANNEL
        return S_FULL_STACK_CHANNEL, K_CHANNEL

    def _capture_for(self, scope, kind, ctx):
        if scope == S_MASK_STACK:
            return {"method": "alg.mapexport.save", "selector": [ctx.get("layer_uid"), "mask"]}
        if scope in (S_SOURCE, S_CONTENT_ACTION, S_LAYER):
            return {"method": "alg.mapexport.save", "selector": [ctx.get("layer_uid"), "<channel>"]}
        if scope == S_GROUP and ctx.get("layer_uid") is not None:
            return {"method": "alg.mapexport.save", "selector": [ctx.get("layer_uid"), "<channel>"]}
        return {
            "method": "alg.mapexport.save",
            "selector": ["<material>", "<stack>", "<channel>"],
        }

    def _request(self, obj, ctx, verdict):
        obj_name, fields = obj
        scope, kind = self._scope_for(obj_name, ctx, verdict)
        req = RasterRequest(
            dataset=self.dataset,
            target=self.target,
            scope=scope,
            kind=kind,
            object_type=obj_name,
            reason=verdict.reason,
            path=ctx.get("path") or (),
            layer_uid=ctx.get("layer_uid"),
            stack_uid=ctx.get("stack_uid"),
            object_uid=_object_uid(obj),
            label=_label(obj),
            material_index=ctx.get("material_index"),
            stack_index=ctx.get("stack_index"),
            capture=self._capture_for(scope, kind, ctx),
            preserves_editability=("low" if scope in (S_GROUP, S_FULL_STACK_CHANNEL) else "partial"),
            visual_confidence="exact",
        )
        self._add(req)

    def _visit_obj(self, obj, ctx):
        if not obj or obj[1] is None:
            return
        obj_name, fields = obj
        uid = _object_uid(obj)
        nctx = dict(ctx)
        if obj_name in ("DataLayerColor", "DataLayerGroup"):
            nctx["layer_uid"] = uid
        if obj_name in ("DataStackActions", "DataStackLayers"):
            nctx["stack_uid"] = uid
        if obj_name == "DataActionGroup":
            nctx["group_uid"] = uid or nctx.get("group_uid")

        verdict = self.classifier.classify(obj, substack=nctx.get("substack"))
        if verdict.action == _classify.BAKE:
            self._request(obj, nctx, verdict)
        elif verdict.action == _classify.DROP:
            return

        if obj_name == "DataBlending":
            bv = self.classifier.classify_blend(_blend_mode(obj))
            if bv.action == _classify.BAKE:
                self._request(obj, nctx, bv)

        for name, _tcode, value in fields:
            self._visit_value(value, self._ctx_for_field(nctx, obj_name, name))

    def _ctx_for_field(self, ctx, obj_name, field_name):
        nctx = dict(ctx)
        nctx["path"] = tuple(list(ctx.get("path") or ()) + [f"{obj_name}.{field_name}"])
        if field_name == "maskActions":
            nctx["substack"] = _classify.MASK
        elif field_name in ("actions", "subStack"):
            nctx["substack"] = _classify.CONTENT
        elif field_name == "items" and nctx.get("substack") != _classify.MASK:
            nctx["substack"] = _classify.CONTENT
        nctx["in_fill_sources"] = obj_name == "DataActionFill" and field_name == "sources"
        return nctx

    def _visit_value(self, value, ctx):
        if not value:
            return
        kind = value[0]
        if kind == "object":
            child = value[1]
            if isinstance(child, tuple):
                self._visit_obj(child, ctx)
        elif kind == "array":
            elem_kind, elems = value[1]
            if elem_kind == "object":
                for i, elem in enumerate(elems):
                    if elem and elem[0] == "object" and isinstance(elem[1], tuple):
                        nctx = dict(ctx)
                        nctx["path"] = tuple(list(ctx.get("path") or ()) + [f"[{i}]"])
                        if ctx.get("path") and ctx["path"][-1] == "DataDocument.materials":
                            nctx["material_index"] = i
                            nctx["stack_index"] = None
                        elif ctx.get("path") and ctx["path"][-1] == "DataMaterial.stacks":
                            nctx["stack_index"] = i
                        self._visit_obj(elem[1], nctx)


def collect_raster_requests(root, *, classifier=None, dataset=None, target=None):
    return RasterPlanner(classifier=classifier, dataset=dataset, target=target).collect(root)
