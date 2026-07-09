"""Classify a decoded HBO node for downgrade: keep it, let an existing transform
handle it, rasterize (BAKE) it into a supported bitmap carrier, or DROP it.

This is the detection half of "rasterize-on-downgrade": instead of unconditionally
dropping what the target can't read, decide *which* unsupported nodes carry appearance
and should be baked (into a fill/mask bitmap) versus which are pure metadata/post-FX
that can only be removed.

Pure + data-driven. The oracle is the target's schema + binary member set + profile
blacklist -- all already computed by runtime/migration_profile. Nothing here needs
Painter, so it is unit-testable headless. Construct a Classifier explicitly (tests) or
via `from_runtime()` (the live engine, which binds these from the active profile).

Key distinction learned from the profiles: the per-step `blacklist` is dominated by
document post-FX (bloom/colorGrading/DOF/...) and baking/metadata settings -- those are
NOT per-layer and can only be removed. The per-*layer* features a downgrade breaks (new
mask generators, new content filters, new sources) surface instead as node TYPES absent
from the target's member set. So appearance-bearing == layer-stack node types, and the
blacklist stays the "removed" bucket.
"""

# --- verdict actions -------------------------------------------------------
KEEP = "keep"           # target supports it as-is
TRANSFORM = "transform"  # supported via a profile transform (rename/retype/...) -> not our job
BAKE = "bake"           # unsupported but appearance-bearing -> rasterize to a bitmap carrier
DROP = "drop"           # unsupported and not bakeable (metadata/post-FX) -> remove (today's behavior)

# --- sub-stack context -----------------------------------------------------
CONTENT = "content"     # inside a layer's content stack (actions/items)
MASK = "mask"           # inside a layer's mask stack (maskActions)

# --- bake granularity (what the carrier will be) ---------------------------
G_MASK = "mask"           # a single grayscale bitmap in maskActions; content stays live
G_CONTENT = "content"     # per-channel bitmaps in a DataActionFill; mask stays live
G_COMPOSITE = "composite"  # composited result at Normal (unsupported blend mode collapses the run)

# Layer-stack node types that carry APPEARANCE (so are worth baking when unsupported).
# Everything else that is unsupported is metadata/post-FX -> DROP.
_APPEARANCE_PREFIXES = ("DataAction", "DataSource", "DataLayer")
_APPEARANCE_TYPES = frozenset({"DataStackActions", "DataBlending"})


def is_appearance_type(name):
    """True if `name` is a layer-stack node that produces pixels (fill/source/effect/
    layer/blending), as opposed to a document setting or post-process parameter."""
    return bool(name) and (name in _APPEARANCE_TYPES or name.startswith(_APPEARANCE_PREFIXES))


class Verdict:
    """(action, granularity, reason). granularity is set only when action == BAKE."""
    __slots__ = ("action", "granularity", "reason")

    def __init__(self, action, granularity=None, reason=""):
        self.action = action
        self.granularity = granularity
        self.reason = reason

    def __eq__(self, other):
        return (isinstance(other, Verdict)
                and self.action == other.action
                and self.granularity == other.granularity)

    def __repr__(self):
        g = f", {self.granularity}" if self.granularity else ""
        return f"Verdict({self.action}{g}: {self.reason})"


class Classifier:
    """Decide keep/transform/bake/drop for decoded nodes against a target version.

    Args:
        schema:         {type_name: [members]} the target defines (runtime.V10_SCHEMA).
        target_members: frozenset of every identifier in the target binary, or None to
                        disable the member filter (exact rebuild / no install found).
        blacklist:      profile blacklist (types/members the target cannot read).
        defaults:       {type: {member: serialized_default}} (runtime.SCHEMA_DEFAULTS),
                        used to decide if dropping an unsupported member changes appearance.
        type_rename:    {old: new} -- a renamed type IS supported (via transform).
        blend_max:      highest blendingMode enum the target supports, or None to skip
                        blend-mode classification.
    """

    def __init__(self, *, schema=None, target_members=None, blacklist=(),
                 defaults=None, type_rename=None, blend_max=None,
                 unknown_appearance_unsupported=False):
        self.schema = schema or {}
        self.target_members = target_members
        self.blacklist = set(blacklist or ())
        self.defaults = defaults or {}
        self.type_rename = type_rename or {}
        self.blend_max = blend_max
        self.unknown_appearance_unsupported = bool(unknown_appearance_unsupported)

    @classmethod
    def from_runtime(cls, blend_max=None):
        """Build from the live profile-bound globals (see runtime.bind())."""
        from . import runtime
        return cls(
            schema=runtime.V10_SCHEMA or {},
            target_members=runtime.TARGET_MEMBERS,
            blacklist=(runtime.PROFILE.blacklist if runtime.PROFILE else ()),
            defaults=runtime.SCHEMA_DEFAULTS or {},
            type_rename=runtime.TYPE_RENAME or {},
            blend_max=blend_max,
        )

    # -- support test -------------------------------------------------------
    def type_supported(self, name):
        """Is `name` representable in the target? Renamed -> yes (transform handles it);
        blacklisted -> no; in schema -> yes; else fall back to the binary member set."""
        if name in self.type_rename:
            return True
        if name in self.blacklist:
            return False
        if name in self.schema:
            return True
        if self.target_members is None:
            if self.unknown_appearance_unsupported and is_appearance_type(name):
                return False
            return True  # member filter disabled: don't guess "unsupported"
        return name in self.target_members

    def member_supported(self, type_name, member):
        """Is `member` valid on `type_name` in the target? Prefer the curated schema;
        fall back to the binary member set for types the schema doesn't cover."""
        qualified = f"{type_name}.{member}"
        if member in self.blacklist or qualified in self.blacklist:
            return False
        sch = self.schema.get(type_name)
        if sch is not None:
            return member in sch
        if self.target_members is None:
            return True
        return member in self.target_members

    # -- node classification ------------------------------------------------
    def classify(self, node, substack=None):
        """Verdict for a decoded object node (name, fields). `substack` is CONTENT/MASK/None
        and selects the bake granularity (a mask bakes to grayscale; content to a fill)."""
        name = node[0]
        if name in self.type_rename:
            return Verdict(TRANSFORM, reason="type_rename")
        if self.type_supported(name):
            return Verdict(KEEP)
        if not is_appearance_type(name):
            return Verdict(DROP, reason="unsupported non-appearance (metadata/post-FX)")
        gran = G_MASK if substack == MASK else G_CONTENT
        where = "mask" if substack == MASK else "content"
        return Verdict(BAKE, gran, reason=f"unsupported {where} feature {name}")

    def classify_blend(self, blend_mode_value):
        """Verdict for a DataBlending.blendingMode enum value. An out-of-range mode can't
        be re-expressed, so the layer's run must be composited (baked at Normal)."""
        if self.blend_max is None or blend_mode_value is None:
            return Verdict(KEEP)
        if blend_mode_value > self.blend_max:
            return Verdict(BAKE, G_COMPOSITE, reason=f"blend mode {blend_mode_value} > target max {self.blend_max}")
        return Verdict(KEEP)

    def param_forces_bake(self, type_name, member, is_default):
        """An unsupported member on an OTHERWISE-supported type: dropping it is safe only
        if its value equals the target's default. A non-default value means the look
        depends on it -> escalate to baking the containing effect. Returns True to escalate."""
        if self.member_supported(type_name, member):
            return False
        return not is_default
