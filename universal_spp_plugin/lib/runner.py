"""Locate and invoke the bundled uspp_tool.exe. Pure (no substance_painter / PySide),
so it is unit-testable headless. Data crosses the boundary as files + one JSON blob."""
import os
import sys
import json
import subprocess

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CREATE_NO_WINDOW = 0x08000000  # Windows: don't flash a console for the child


def tool_path():
    """bin/uspp_tool.exe next to the plugin. Override with USPP_TOOL for dev/testing;
    if it ends in .py it's run with the current Python, else executed directly.
    (Spaces in the path are fine — no shell splitting.)"""
    return os.environ.get("USPP_TOOL") or os.path.join(_PLUGIN_ROOT, "bin", "uspp_tool.exe")


def _argv(*args):
    tp = tool_path()
    head = [sys.executable, tp] if tp.lower().endswith(".py") else [tp]
    return head + list(args)


def _run(*args, capture=True, env_extra=None):
    kwargs = dict(text=True)
    if os.name == "nt":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if env_extra:
        env = dict(os.environ); env.update(env_extra); kwargs["env"] = env
    return subprocess.run(_argv(*args), **kwargs)


def available():
    return os.path.exists(tool_path())


def _spp_icon(winreg):
    """The icon Explorer shows for .spp, as 'path,index' -- read from the .spp association
    (HKCR\\.spp -> progid -> DefaultIcon). Returns None if not found. No hardcoded path."""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ".spp") as k:
            progid = winreg.QueryValueEx(k, "")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\DefaultIcon") as k:
            return winreg.QueryValueEx(k, "")[0] or None
    except OSError:
        return None


def ensure_association(painter_exe):
    """Make double-clicking a .uspp run `Painter.exe "<file.uspp>"` so Painter opens and the
    plugin reads the path from its launch arguments (QApplication.arguments()) -- the file
    reaches the plugin with no converter, no temp .spp, no IPC. `painter_exe` is THIS running
    Painter's path. Per-user, no admin; idempotent. Returns True if (re)written. Windows-only."""
    if os.name != "nt" or not painter_exe or not os.path.exists(painter_exe):
        return False
    try:
        import winreg
    except Exception:
        return False
    painter_exe = os.path.normpath(painter_exe)   # MUST be backslashes: Explorer's shell\open
    cmd = f'"{painter_exe}" "%1"'                  # \command can't resolve a forward-slash exe path
    key = r"Software\Classes\USPP.Project\shell\open\command"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            if winreg.QueryValueEx(k, "")[0] == cmd:
                return False          # already current -> nothing to do
    except OSError:
        pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.uspp") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, "USPP.Project")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\USPP.Project") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, "Universal Substance Painter Project")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValue(k, "", winreg.REG_SZ, cmd)
    icon = _spp_icon(winreg)   # show the same icon Explorer uses for .spp
    if icon:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\USPP.Project\DefaultIcon") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, icon)
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # refresh Explorer
    except Exception:
        pass
    return True


def run_plan(uspp, target):
    """-> dict (the plan JSON). Raises RuntimeError with stderr on hard failure."""
    r = _run("plan", "--uspp", uspp, "--target", target)
    if r.returncode not in (0,):
        raise RuntimeError(r.stderr or f"plan exited {r.returncode}")
    return json.loads(r.stdout)


_PROGRESS_TAG = "__USPP_PROGRESS__"
# Map the tool's plain stdout lines to a friendly status when no structured progress is
# available, so the dialog still narrates the phases on older builds of the tool.
_PHASES = (
    ("Saving to:", "Reading project…"),
    ("Extracting", "Extracting project data…"),
    ("Building SPP", "Converting to target version…"),
    ("Creating:", "Writing project file…"),
    ("Transcoded", "Converting layers…"),
    ("Stripping", "Cleaning cache…"),
    ("packed ->", "Finishing…"),
    ("Created:", "Finishing…"),
)


def _run_streaming(args, on_progress, env_extra=None):
    """Run the tool, stream stdout, drive on_progress(frac, msg). stderr is folded into
    stdout so one reader can't deadlock on a full pipe. Returns (ok, err)."""
    env = dict(os.environ)
    env["USPP_PROGRESS"] = "1"
    if env_extra:
        env.update(env_extra)
    kwargs = dict(text=True, bufsize=1, env=env,
                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if os.name == "nt":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    p = subprocess.Popen(_argv(*args), **kwargs)
    log = []
    for line in p.stdout:
        line = line.rstrip("\r\n")
        if line.startswith(_PROGRESS_TAG):
            parts = line.split("\t")
            if len(parts) >= 3:
                try:
                    f = float(parts[1])
                    on_progress(None if f < 0 else f, parts[2])   # <0 = indeterminate/busy
                except Exception:
                    pass
            continue
        if line:
            log.append(line)
            for prefix, friendly in _PHASES:
                if prefix in line:
                    on_progress(None, friendly)   # None = keep bar busy, just update text
                    break
    p.wait()
    ok = p.returncode == 0
    return ok, ("" if ok else "\n".join(log[-10:]) or f"exited {p.returncode}")


def run_build(uspp, target, out_spp, on_progress=None, target_binary=None):
    # Builds for "Open" produce a temp file Painter loads then discards -> fast (low)
    # compression. SPP_FAST is honored by the builder; the CLI default stays unchanged.
    # target_binary = the exact running Painter exe, so the member-allowlist filter reads
    # the version we're actually opening into (no path guessing).
    env = {"SPP_FAST": "1"}
    if target_binary:
        env["SPP_TARGET_BINARY"] = target_binary
    if on_progress is None:
        r = _run("build", "--uspp", uspp, "--target", target, "-o", out_spp, env_extra=env)
        return r.returncode == 0, (r.stderr or "")
    return _run_streaming(("build", "--uspp", uspp, "--target", target, "-o", out_spp),
                          on_progress, env_extra=env)


def run_pack(spp, out_uspp, on_progress=None):
    if on_progress is None:
        r = _run("pack", spp, "-o", out_uspp)
        return r.returncode == 0, (r.stderr or "")
    return _run_streaming(("pack", spp, "-o", out_uspp), on_progress)


if __name__ == "__main__":
    # headless smoke: build argv only (no exe needed)
    os.environ["USPP_TOOL"] = "C:/Program Files/x/uspp_tool.py"
    assert _argv("plan", "--uspp", "a.uspp") == [sys.executable, "C:/Program Files/x/uspp_tool.py", "plan", "--uspp", "a.uspp"], _argv("plan")
    os.environ["USPP_TOOL"] = "C:/Program Files/x/uspp_tool.exe"
    assert _argv("info") == ["C:/Program Files/x/uspp_tool.exe", "info"], _argv("info")
    del os.environ["USPP_TOOL"]
    print("runner argv self-check OK; tool_path ->", tool_path())
