"""Running-Painter-version detection. parse_label() is pure/testable; detect_running()
is defensive: the substance_painter.application module is absent on some builds, so we
fall back to parsing the version from the Painter install path (the plugin runs inside
Painter, so substance_painter.__file__ / sys.executable point into the versioned
install dir, e.g. '...\\Adobe Substance 3D Painter v12.1\\...')."""
import os
import re
import sys


def parse_label(s):
    """'10.0.1' / '12.1' / (12,1,0) -> 'major.minor' label ('12.1', '10'). None if unparseable."""
    if isinstance(s, (list, tuple)):
        nums = [int(x) for x in s[:2]]
    else:
        m = re.findall(r"\d+", str(s))
        if not m:
            return None
        nums = [int(x) for x in m[:2]]
    if not nums:
        return None
    major = nums[0]
    minor = nums[1] if len(nums) > 1 else 0
    return f"{major}.{minor}" if minor else str(major)


def label_from_path(path):
    """'...\\Adobe Substance 3D Painter v12.1\\...' -> '12.1'; '...Painter v10\\' -> '10'."""
    if not path:
        return None
    m = re.search(r"Painter\s*v?(\d+)(?:\.(\d+))?", str(path), re.IGNORECASE)
    if not m:
        return None
    major, minor = int(m.group(1)), int(m.group(2) or 0)
    return f"{major}.{minor}" if minor else str(major)


def painter_binary_names(os_name=None):
    """Known executable basenames, with the native platform spelling first."""
    if (os_name or os.name) == "nt":
        return ("Adobe Substance 3D Painter.exe",)
    return ("Adobe Substance 3D Painter", "Substance 3D Painter")


def _is_painter_binary(path, names):
    if not path:
        return False
    base = os.path.basename(os.path.realpath(path)).lower()
    return any(base == name.lower() for name in names)


def running_binary():
    """Absolute path to the running Painter executable, or None. The plugin runs INSIDE
    Painter, so the install dir is an ancestor of substance_painter.__file__ and of
    sys.executable. We walk up from each until we find the dir that actually contains
    the platform's native Painter executable -- no path-shape or label assumptions,
    works wherever Painter is installed."""
    names = painter_binary_names()
    starts = []
    try:
        import substance_painter
        f = getattr(substance_painter, "__file__", None)
        if f:
            starts.append(f)
    except Exception:
        pass
    if sys.platform.startswith("linux"):
        try:
            starts.append(os.path.realpath("/proc/self/exe"))
        except Exception:
            pass
    if getattr(sys, "executable", None):
        starts.append(sys.executable)
    # Embedded Python normally reports Painter itself. /proc/self/exe is the more
    # authoritative source on Linux, where launchers and Steam runtimes may be involved.
    for start in starts:
        if _is_painter_binary(start, names) and os.path.exists(start):
            return os.path.realpath(start)
    for start in starts:
        d = os.path.dirname(os.path.abspath(start))
        for _ in range(10):
            for name in names:
                executable = os.path.join(d, name)
                if os.path.isfile(executable):
                    return os.path.realpath(executable)
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return None


def detect_running():
    """-> 'major.minor' label for the running Painter, or None."""
    # 1) API, where present (module is absent on some builds).
    try:
        import importlib
        app = importlib.import_module("substance_painter.application")
        for getter in ("version_info", "version"):
            fn = getattr(app, getter, None)
            if callable(fn):
                label = parse_label(fn())
                if label:
                    return label
    except Exception:
        pass
    # 2) Parse from the Painter install path (reliable across all versions).
    try:
        import substance_painter
        label = label_from_path(getattr(substance_painter, "__file__", None))
        if label:
            return label
    except Exception:
        pass
    return label_from_path(sys.executable)


if __name__ == "__main__":
    assert parse_label("10.0.1") == "10"
    assert parse_label("12.1.0") == "12.1"
    assert parse_label((12, 1, 0)) == "12.1"
    assert parse_label("garbage") is None
    assert label_from_path(r"C:\Program Files\Adobe\Adobe Substance 3D Painter v12.1\resources\x") == "12.1"
    assert label_from_path(r"C:\Program Files\Adobe\Adobe Substance 3D Painter v10\app.exe") == "10"
    assert label_from_path(r"C:\Program Files\Adobe\Adobe Substance 3D Painter v8.1\x") == "8.1"
    assert label_from_path(r"D:\no\version\here") is None
    assert painter_binary_names("nt") == ("Adobe Substance 3D Painter.exe",)
    assert painter_binary_names("posix")[0] == "Adobe Substance 3D Painter"
    print("version self-check OK")
