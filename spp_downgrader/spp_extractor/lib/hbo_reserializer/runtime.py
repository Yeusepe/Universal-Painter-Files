"""Profile-derived state for the HBO reserializer, kept in one module so every submodule
(and the builder) reads the same live values. bind() rebinds them from the active
migration profile; the package __init__ calls it on import -- and thus on reload, which
uspp_tool does per target to switch the active profile.

All version-pair knowledge lives in the active migration profile (data file), not here.
  schema:        target type_name -> [ordered members] (drop/reorder source objects)
  baking_schema: target bakerId -> [[tweak identifier, DataTweak type], ...]
  *_rename:      renames across versions
"""

PROFILE = None
V10_SCHEMA = None
V10_BAKING_SCHEMA = None
BAKING_TWEAK_RENAME = None
TYPE_RENAME = None
BAKING_BAKER_ID_RENAME = None
FIELD_RENAME = None
FIELD_RETYPE = None
FIELD_REKIND = None
FIELD_VALUE_TRANSFORM = None
PRIMITIVE_RETYPE = None
SCHEMA_DEFAULTS = None

# Substance graph names whose procedural SOURCES must be dropped because the target's
# substance engine can't read the cooked .sbsasm format (e.g. format-9 graphs vs v8.1's
# format-6-max engine). Set per-target by the builder; not profile-derived, so bind()
# leaves it untouched. A DataSourceProcedural referencing one of these -> dropped.
DROP_SUBSTANCE_GRAPHS = set()

# The set of every identifier the TARGET version's binary knows. A member whose name is
# absent from this set does not exist in the target version, so it is dropped on downgrade
# -- the authoritative fix for "value defined for a NON-EXISTING member". Set per-target by
# the builder; None disables the filter (e.g. exact rebuilds, or no matching install found).
#
# The set is extracted ON DEMAND from the user's OWN installed Painter binary (their
# legitimately-owned software) and cached locally -- nothing is committed to the repo or
# bundled in the exe, so no Adobe-derived data is redistributed.
TARGET_MEMBERS = None

_IDENT = None  # compiled lazily


def _vkey(s):
    try:
        return tuple(int(x) for x in s.split("."))
    except Exception:
        return ()


def _registry_installs():
    """{version_label: exe_path} for every installed Substance Painter, read from the
    Windows registry (uninstall keys). Locations are wherever the user installed them --
    no hardcoded directory."""
    import os
    try:
        import winreg
    except Exception:
        return {}
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    out = {}
    for hive, path in roots:
        try:
            base = winreg.OpenKey(hive, path)
        except OSError:
            continue
        n = winreg.QueryInfoKey(base)[0]
        for i in range(n):
            try:
                sub = winreg.OpenKey(base, winreg.EnumKey(base, i))
                name = winreg.QueryValueEx(sub, "DisplayName")[0]
                if "Substance 3D Painter" not in name:
                    continue
                loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
                ver = winreg.QueryValueEx(sub, "DisplayVersion")[0]
                exe = os.path.join(loc, "Adobe Substance 3D Painter.exe")
                # normalize DisplayVersion '8.1.0'->'8.1', '9.0.0'->'9'
                parts = _vkey(ver)
                lab = ".".join(str(p) for p in (parts[:2] if len(parts) > 1 and parts[1] else parts[:1])) if parts else ver
                if loc and os.path.exists(exe):
                    out[lab] = exe
            except OSError:
                continue
    return out


def _find_painter_exe(label):
    """The installed Painter binary whose version EXACTLY matches `label`, from the
    registry. Officially only one Painter is installed; the member set must come from the
    target version itself (a different version's set would wrongly drop/keep members), so
    we never substitute a near version -- return None and let the filter stay off."""
    return _registry_installs().get(label)


def _extract_idents(exe_path):
    """Set of identifier strings in the binary (chunked, bounded memory)."""
    import re
    global _IDENT
    if _IDENT is None:
        # Single-character member names like DataTimelineKey.x are valid identifiers.
        _IDENT = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{0,40}")
    idents = set()
    with open(exe_path, "rb") as f:
        prev = b""
        while True:
            chunk = f.read(16 << 20)
            if not chunk:
                break
            buf = prev + chunk
            head = buf[:-41] if len(buf) > 41 else buf
            idents.update(m.group(0) for m in _IDENT.finditer(head))
            prev = buf[-41:]
        idents.update(m.group(0) for m in _IDENT.finditer(prev))
    return frozenset(s.decode("latin1") for s in idents)


def load_members(label):
    """The frozenset of identifiers the target version's binary knows, for the member
    allowlist. The binary is, in order of preference:
      1. SPP_TARGET_BINARY -- the exact running Painter the plugin is opening into (no guess),
      2. the registry-discovered install for `label` (CLI, no Painter running).
    Result is cached under %LOCALAPPDATA%/USPP/member_cache. None if no binary is found."""
    import os, gzip
    cache_dir = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                             "USPP", "member_cache")
    cache = os.path.join(cache_dir, f"v{label}.txt.gz")
    if os.path.exists(cache):
        try:
            with gzip.open(cache, "rb") as f:
                return frozenset(f.read().decode("latin1").split("\n"))
        except Exception:
            pass
    exe = os.environ.get("SPP_TARGET_BINARY")
    if not (exe and os.path.exists(exe)):
        exe = _find_painter_exe(label)
    if not exe:
        return None
    idents = _extract_idents(exe)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with gzip.open(cache, "wb") as f:
            f.write("\n".join(sorted(idents)).encode("latin1"))
    except Exception:
        pass
    return idents


def bind(profile=None):
    """(Re)bind the profile-derived globals from `profile` (default: the active profile)."""
    global PROFILE, V10_SCHEMA, V10_BAKING_SCHEMA, BAKING_TWEAK_RENAME, TYPE_RENAME
    global BAKING_BAKER_ID_RENAME, FIELD_RENAME, FIELD_RETYPE, FIELD_REKIND
    global FIELD_VALUE_TRANSFORM, PRIMITIVE_RETYPE, SCHEMA_DEFAULTS
    if profile is None:
        from lib import migration_profile
        profile = migration_profile.ACTIVE
    PROFILE = profile
    V10_SCHEMA = profile.schema
    V10_BAKING_SCHEMA = profile.baking_schema
    BAKING_TWEAK_RENAME = profile.baking_tweak_rename
    TYPE_RENAME = profile.type_rename
    BAKING_BAKER_ID_RENAME = profile.baking_baker_id_rename
    FIELD_RENAME = profile.field_rename
    FIELD_RETYPE = profile.field_retype
    FIELD_REKIND = profile.field_rekind
    FIELD_VALUE_TRANSFORM = profile.field_value_transform
    PRIMITIVE_RETYPE = profile.primitive_retype
    SCHEMA_DEFAULTS = profile.schema_defaults
