"""Universal SPP plugin for Substance Painter.

Adds a "Universal" menu with Open/Save that round-trips a version-independent .uspp:
opening converts it to the running Painter version (downgrading via the bundled
uspp_tool, or letting Painter upgrade natively), after a plain-English lossy warning.

Install: drop this folder into  Documents/Adobe/Adobe Substance 3D Painter/python/plugins/
Requires bin/uspp_tool.exe beside this file (the bundled converter).
"""
import os
import time
import tempfile
import traceback

import substance_painter.ui as sp_ui
import substance_painter.project as sp_project
import substance_painter.logging as sp_log
import substance_painter.event as sp_event

try:
    from PySide6 import QtWidgets, QtCore, QtGui
    _PYSIDE = 6
    _QAction = QtGui.QAction          # QAction moved to QtGui in PySide6
except ImportError:
    from PySide2 import QtWidgets, QtCore
    _PYSIDE = 2
    _QAction = QtWidgets.QAction

from .lib import runner, version, dialogs, progress

_menu = None
_actions = []   # strong refs so Qt doesn't garbage-collect the menu actions
_file_action = None
_handled_launch = False   # open the launch-arg .uspp once per session, not on every reload
_CACHE = os.path.join(tempfile.gettempdir(), "USPPCache")


def _parent():
    try:
        return sp_ui.get_main_window()
    except Exception:
        return None


def _log(msg):
    try:
        sp_log.info(f"[UniversalSPP] {msg}")
    except Exception:
        print(f"[UniversalSPP] {msg}")


def _prune_cache(max_age_days=7):
    try:
        if not os.path.isdir(_CACHE):
            return
        cutoff = time.time() - max_age_days * 86400
        for f in os.listdir(_CACHE):
            p = os.path.join(_CACHE, f)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                try:
                    os.remove(p)
                except OSError:
                    pass
    except Exception:
        pass


def _temp_spp():
    os.makedirs(_CACHE, exist_ok=True)
    return os.path.join(_CACHE, f"uspp_{int(time.time())}.spp")


# ------------------------------------------------------- double-click (launch argument)

def _launch_uspp_arg():
    """The .uspp path Painter was launched with, or None. Double-clicking a .uspp runs
    `Painter.exe "<path.uspp>"` (see runner.ensure_association); Painter exposes that to its
    embedded Qt app, so we read it from QApplication.arguments() -- the same source the QML
    `Qt.application.arguments` exposes."""
    try:
        app = QtWidgets.QApplication.instance()
        args = list(app.arguments()) if app else []
    except Exception:
        args = []
    for a in args[1:]:
        if a and a.lower().endswith(".uspp") and os.path.exists(a):
            return a
    return None


def _on_gui_started(_evt=None):
    """Painter's GUI is ready (modals/opening are safe now). If we were launched on a .uspp,
    open it -- exactly once per session."""
    global _handled_launch
    if _handled_launch:
        return
    _handled_launch = True
    uspp = _launch_uspp_arg()
    if uspp:
        _log(f"launch arg: opening {uspp}")
        _open_path(uspp)


def _open_launch_if_gui_up():
    """Plugin reloaded while Painter is already running: the GUI-started event already fired,
    so trigger off a visible main window instead (not visible during a fresh-launch load)."""
    try:
        mw = sp_ui.get_main_window()
        if mw is not None and mw.isVisible():
            _on_gui_started()
    except Exception:
        pass


# ------------------------------------------------------------------- handlers

def on_open():
    if not runner.available():
        dialogs.error("Converter not found.\nExpected bin/uspp_tool.exe beside the plugin.")
        return
    uspp = dialogs.open_uspp()
    if uspp:
        _open_path(uspp)


def _open_path(uspp):
    """Convert `uspp` to the running Painter version and open it. Shared by the menu and the
    double-click launch-argument handler."""
    try:
        if not runner.available():
            dialogs.error("Converter not found.\nExpected bin/uspp_tool.exe beside the plugin.")
            return
        target = version.detect_running()
        if not target:
            dialogs.error("Could not detect the running Painter version.")
            return
        plan = runner.run_plan(uspp, target)

        if not plan.get("supported"):
            dialogs.error(
                f"This project was created in v{plan.get('source_version')} and cannot be "
                f"converted to your version (v{plan.get('target_version')})."
            )
            return
        if plan.get("lossy"):
            if not dialogs.confirm_lossy(plan):
                return
        elif plan.get("direction") == "native_upgrade":
            dialogs.info(
                f"This project was created in v{plan['source_version']}. "
                "Substance Painter will upgrade it to your version on open."
            )

        out = _temp_spp()
        target_binary = version.running_binary()
        _log(f"open: target=v{target}  binary={target_binary}")
        ok, err = progress.run_with_progress(
            _parent(), f"Converting project to v{target}",
            lambda on_p: runner.run_build(uspp, target, out, on_progress=on_p,
                                          target_binary=target_binary))
        if not ok:
            dialogs.error(f"Conversion failed.\n{err}")
            return
        # Don't pre-check is_open() (some builds report the empty/home state as open).
        # Just open; if a project is genuinely loaded, Painter refuses -> close & retry.
        try:
            sp_project.open(out)
        except Exception:
            try:
                dirty = sp_project.needs_saving()
            except Exception:
                dirty = False
            if dirty and not dialogs.confirm(
                "Close the current project and open the Universal file?\n"
                "Unsaved changes in the current project will be lost."):
                return
            try:
                sp_project.close()
            except Exception:
                pass
            sp_project.open(out)
        _log(f"opened {uspp} as v{target}")
    except Exception as e:
        _log("on_open error: " + traceback.format_exc())
        dialogs.error(f"Unexpected error opening Universal project:\n{e}")


def on_save():
    try:
        if not runner.available():
            dialogs.error("Converter not found.\nExpected bin/uspp_tool.exe beside the plugin.")
            return
        if not sp_project.is_open():
            dialogs.error("Open a project first.")
            return
        # We pack from the .spp on disk, so the only hard requirement is that the
        # project has been saved to a file. needs_saving() is NOT a blocker (Painter
        # often reports it True for trivial view state); we just best-effort flush.
        try:
            spp = sp_project.file_path()
        except Exception:
            spp = None
        if not spp or not os.path.exists(spp):
            dialogs.info("Save your project to a .spp first (File > Save), then export to Universal.")
            return
        try:
            if sp_project.needs_saving():
                sp_project.save()
        except Exception as e:
            _log(f"save flush skipped: {e}")
        out = dialogs.save_uspp(os.path.splitext(spp)[0] + ".uspp")
        if not out:
            return
        ok, err = progress.run_with_progress(
            _parent(), "Saving Universal Project",
            lambda on_p: runner.run_pack(spp, out, on_progress=on_p))
        if ok:
            dialogs.info(f"Saved Universal project:\n{out}")
        else:
            dialogs.error(f"Export failed.\n{err}")
    except Exception as e:
        _log("on_save error: " + traceback.format_exc())
        dialogs.error(f"Unexpected error saving Universal project:\n{e}")


# --------------------------------------------------------------- lifecycle

def start_plugin():
    global _menu, _actions
    _prune_cache()
    # Parent the menu to the main window — Painter's menu bar can't render the popup
    # of an orphan QMenu (shows the title but the dropdown is empty/dead).
    mw = getattr(sp_ui, "get_main_window", lambda: None)()
    _menu = QtWidgets.QMenu("Universal", mw) if mw is not None else QtWidgets.QMenu("Universal")
    # addAction(text) returns an action the menu OWNS; keep strong refs too.
    a_open = _menu.addAction("Open Universal...")
    a_open.triggered.connect(on_open)
    a_save = _menu.addAction("Save as Universal...")
    a_save.triggered.connect(on_save)
    _actions = [a_open, a_save]
    sp_ui.add_menu(_menu)
    global _file_action
    try:
        _file_action = _QAction("Save as Universal (.uspp)…", mw)
        _file_action.triggered.connect(on_save)
        sp_ui.add_action(sp_ui.ApplicationMenu.File, _file_action)
    except Exception as e:
        _log(f"File-menu action setup failed: {e}")
    try:
        if runner.ensure_association(version.running_binary()):
            _log("registered .uspp double-click association")
    except Exception as e:
        _log(f"association setup skipped: {e}")
    try:
        sp_event.DISPATCHER.connect(sp_event.GraphicalUserInterfaceStarted, _on_gui_started)
        QtCore.QTimer.singleShot(0, _open_launch_if_gui_up)
    except Exception as e:
        _log(f"launch-arg handler setup failed: {e}")
    _log(f"plugin started: '{_menu.title()}' with {len(_menu.actions())} actions, "
         f"main_window={'yes' if mw is not None else 'NO'}, PySide={_PYSIDE}")


def close_plugin():
    global _menu, _actions, _file_action
    try:
        sp_event.DISPATCHER.disconnect(sp_event.GraphicalUserInterfaceStarted, _on_gui_started)
    except Exception:
        pass
    if _file_action is not None:
        try:
            sp_ui.delete_ui_element(_file_action)
        except Exception:
            pass
        _file_action = None
    if _menu is not None:
        try:
            sp_ui.delete_ui_element(_menu)
        except Exception:
            pass
        _menu = None
    _actions = []
    _log("plugin closed")


if __name__ == "__main__":
    start_plugin()
