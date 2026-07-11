"""Universal SPP plugin for Substance Painter.

Adds a "Universal" menu with Open/Save that round-trips a version-independent .uspp:
opening converts it to the running Painter version (downgrading via the bundled
uspp_tool, or letting Painter upgrade natively), after a plain-English lossy warning.

Install: drop this folder into  Documents/Adobe/Adobe Substance 3D Painter/python/plugins/
Requires the native bin/uspp_tool[.exe] beside this file (the bundled converter).
"""
import os
import json
import re
import shutil
import time
import tempfile
import traceback
import contextlib
import importlib
import uuid
import zipfile

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

_HOT_RELOAD = "_menu" in globals()

from .lib import runner, version, dialogs, progress, updater, legacy_uv_export

if _HOT_RELOAD:
    for _module in (runner, version, dialogs, progress, updater, legacy_uv_export):
        importlib.reload(_module)

_menu = None
_actions = []   # strong refs so Qt doesn't garbage-collect the menu actions
_file_action = None
_handled_launch = False   # open the launch-arg .uspp once per session, not on every reload
_operation_active = False
_update_check_running = False
_CACHE = os.path.join(tempfile.gettempdir(), "USPPCache")
_TEMP_OPEN_MARK_CONTEXT = "UniversalSPP"
_FILENAME_SAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_RASTER_CAPTURE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "raster_capture_companion", "raster_capture.js")


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


@contextlib.contextmanager
def _busy_operation():
    global _operation_active
    previous = _operation_active
    _operation_active = True
    try:
        yield
    finally:
        _operation_active = previous


def _prune_cache(max_age_days=7):
    try:
        if not os.path.isdir(_CACHE):
            return
        cutoff = time.time() - max_age_days * 86400
        for name in os.listdir(_CACHE):
            p = os.path.join(_CACHE, name)
            if os.path.getmtime(p) < cutoff:
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p)
                    else:
                        os.remove(p)
                except OSError:
                    pass
    except Exception:
        pass


def _path_stem(path):
    text = str(path or "").replace("\\", "/")
    base = text.rsplit("/", 1)[-1]
    stem, _ext = os.path.splitext(base)
    return stem


def _safe_project_stem(path):
    stem = _path_stem(path).strip() or "Universal Project"
    stem = _FILENAME_SAFE_RE.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    return (stem or "Universal Project")[:80]


def _read_uspp_source_file(uspp):
    try:
        with zipfile.ZipFile(uspp) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        return manifest.get("source_file") or None
    except Exception:
        return None


def _representative_source_path(src, uspp):
    if src.lower().endswith(".spp"):
        return src
    return _read_uspp_source_file(uspp) or src


def _temp_named_path(source_path, filename):
    os.makedirs(_CACHE, exist_ok=True)
    stem = _safe_project_stem(source_path)
    folder = os.path.join(_CACHE, f"open_{uuid.uuid4().hex}")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, filename(stem))


def _temp_spp(source_path, target):
    def filename(stem):
        version = f" v{target}" if target else ""
        return f"{stem} - Universal{version}.spp"

    return _temp_named_path(source_path, filename)


def _temp_uspp(source_path):
    return _temp_named_path(source_path, lambda stem: f"{stem} - Universal Package.uspp")


def _mark_opened_copy_dirty(source_path, target):
    """Best-effort: make the converted temp project behave like an unsaved copy."""
    try:
        metadata_cls = getattr(sp_project, "Metadata", None)
        if metadata_cls is None:
            return False
        metadata = metadata_cls(_TEMP_OPEN_MARK_CONTEXT)
        stamp = f"{_safe_project_stem(source_path)}|v{target}|{uuid.uuid4().hex}"
        metadata.set("opened_copy", stamp)
        return True
    except Exception as e:
        _log(f"opened-copy save marker skipped: {e}")
        return False


def _mark_opened_copy_dirty_soon(source_path, target):
    if _mark_opened_copy_dirty(source_path, target):
        return

    def retry():
        _mark_opened_copy_dirty(source_path, target)

    try:
        execute_when_not_busy = getattr(sp_project, "execute_when_not_busy", None)
        if execute_when_not_busy:
            execute_when_not_busy(retry)
        else:
            QtCore.QTimer.singleShot(250, retry)
    except Exception as e:
        _log(f"opened-copy save marker retry skipped: {e}")


def _suggest_opened_spp_path(source, opened_from, target):
    folder = None
    for candidate in (opened_from, source):
        if candidate:
            candidate_dir = os.path.dirname(os.path.abspath(candidate))
            if os.path.isdir(candidate_dir):
                folder = candidate_dir
                break
    if folder is None:
        folder = os.path.expanduser("~/Documents")
    version = f" v{target}" if target else ""
    filename = f"{_safe_project_stem(source)} - Universal{version}.spp"
    return os.path.join(folder, filename)


def _same_path(a, b):
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def _is_original_spp_path(path, source, opened_from):
    if not path.lower().endswith(".spp"):
        return False
    return any(
        candidate and candidate.lower().endswith(".spp") and _same_path(path, candidate)
        for candidate in (source, opened_from)
    )


def _choose_opened_spp_path(source, opened_from, target):
    suggested = _suggest_opened_spp_path(source, opened_from, target)
    while True:
        out = dialogs.save_spp(suggested)
        if not out:
            return None
        if not out.lower().endswith(".spp"):
            out += ".spp"
        if _is_original_spp_path(out, source, opened_from):
            dialogs.error("Choose a new file name for the opened copy.\nThe original project will not be overwritten.")
            suggested = _suggest_opened_spp_path(source, opened_from, target)
            continue
        return out


def _copy_to_open_destination(temp_spp, source, opened_from, target):
    out = _choose_opened_spp_path(source, opened_from, target)
    if not out:
        return None
    try:
        shutil.copy2(temp_spp, out)
    except Exception as e:
        dialogs.error(f"Could not save the converted project.\n{e}")
        return None
    return out


def _load_json_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _js_path(path):
    return os.path.abspath(path).replace("\\", "/")


def _raster_js_call(invocation):
    try:
        import substance_painter.js as sp_js
    except Exception as e:
        raise RuntimeError(f"Raster capture is not available in this Painter build: {e}")
    with open(_RASTER_CAPTURE_SCRIPT, "r", encoding="utf-8") as f:
        script = f.read()
    code = (
        "(function(){\n"
        + script
        + "\n"
        + invocation
        + "\n"
        + "return {ok:true};\n"
        + "})()"
    )
    return sp_js.evaluate(code)


def _wait_for_painter_evaluation(timeout_ms=30000):
    loop = QtCore.QEventLoop()
    state = {"done": False, "timed_out": False}

    def finish():
        state["done"] = True
        if loop.isRunning():
            loop.quit()

    def evaluated():
        QtCore.QTimer.singleShot(250, finish)

    def timed_out():
        state["timed_out"] = True
        finish()

    def arm():
        execute_when_not_busy = getattr(sp_project, "execute_when_not_busy", None)
        if execute_when_not_busy:
            execute_when_not_busy(evaluated)
        else:
            QtCore.QTimer.singleShot(1000, finish)

    timeout = QtCore.QTimer(loop)
    timeout.setSingleShot(True)
    timeout.timeout.connect(timed_out)
    timeout.start(timeout_ms)
    QtCore.QTimer.singleShot(0, arm)
    if not state["done"]:
        run = getattr(loop, "exec", None) or loop.exec_
        run()
    if state["timed_out"]:
        raise RuntimeError("Painter did not finish evaluating the temporary mask channel.")


def _capture_raster_js(plan_path, manifest_path, raster_settings):
    preparation_path = os.path.join(os.path.dirname(manifest_path), "preparation.json")
    options_path = os.path.join(os.path.dirname(manifest_path), "capture_options.json")
    with open(options_path, "w", encoding="utf-8") as f:
        json.dump({
            "content_bit_depth": raster_settings["raster_content_bit_depth"],
            "padding": raster_settings["raster_padding"],
        }, f)
    _raster_js_call(
        "prepareCapture({}, {});".format(
            json.dumps(_js_path(plan_path)), json.dumps(_js_path(preparation_path))
        )
    )
    try:
        _wait_for_painter_evaluation(
            timeout_ms=raster_settings["raster_evaluation_timeout_seconds"] * 1000
        )
        executable = version.running_binary()
        if not executable:
            raise RuntimeError("Could not locate the running Painter executable for raster capture.")
        # The legacy UV-tile guard locator is PE/Win32-specific. Linux Painter can still
        # use its native map exporter; if that exporter rejects a particular legacy UV
        # workflow, capture reports the normal Painter error instead of failing in WinDLL.
        with legacy_uv_export.temporary_guard_bypass(executable, required=(os.name == "nt")):
            result = _raster_js_call(
                "capture({}, {}, {}, {});".format(
                    json.dumps(_js_path(plan_path)),
                    json.dumps(_js_path(manifest_path)),
                    json.dumps(_js_path(preparation_path)),
                    json.dumps(_js_path(options_path)),
                )
            )
    except Exception:
        try:
            _raster_js_call(
                "cleanupCapture({});".format(json.dumps(_js_path(preparation_path)))
            )
        except Exception as cleanup_error:
            _log(f"temporary raster channel cleanup failed: {cleanup_error}")
        raise
    legacy_uv_export.expand_manifest_uv_tiles(manifest_path)
    return result


def _run_capture_with_dialog(plan_path, manifest_path, raster_settings):
    dlg = QtWidgets.QProgressDialog("Capturing raster fallback pixels...", None, 0, 0, _parent())
    dlg.setWindowTitle("Universal SPP")
    dlg.setWindowModality(QtCore.Qt.ApplicationModal)
    dlg.setCancelButton(None)
    dlg.setMinimumDuration(0)
    dlg.show()
    app = QtWidgets.QApplication.instance()
    if app:
        app.processEvents()
    try:
        return _capture_raster_js(plan_path, manifest_path, raster_settings)
    finally:
        dlg.close()
        dlg.deleteLater()


def _raster_capture_for_pack(spp, settings=None):
    """Return a capture directory for pack --raster-capture-dir, or None if no
    raster fallbacks are required. Raises if required fallbacks cannot be captured."""
    settings = updater._clean_settings(settings or updater.load_settings())
    if not settings["raster_capture_enabled"]:
        return None
    keep_failed = settings["keep_failed_raster_captures"]
    os.makedirs(_CACHE, exist_ok=True)
    capture_dir = tempfile.mkdtemp(prefix="raster_capture_", dir=_CACHE)
    plan_path = os.path.join(capture_dir, "plan.json")
    manifest_path = os.path.join(capture_dir, "manifest.json")
    argv, env = runner.raster_plan_args(spp, plan_path)
    ok, err = progress.run_with_progress(_parent(), "Planning raster fallbacks", argv, env)
    if not ok:
        shutil.rmtree(capture_dir, ignore_errors=True)
        raise RuntimeError(f"Could not plan raster fallbacks.\n{err}")
    plan = _load_json_file(plan_path)
    requests = list(plan.get("requests") or [])
    if not requests:
        shutil.rmtree(capture_dir, ignore_errors=True)
        return None
    try:
        _run_capture_with_dialog(plan_path, manifest_path, settings)
    except Exception:
        if not keep_failed:
            shutil.rmtree(capture_dir, ignore_errors=True)
        raise
    manifest = _load_json_file(manifest_path)
    have = {a.get("request_id") for a in manifest.get("assets") or [] if a.get("request_id")}
    missing = [r.get("id") for r in requests if r.get("id") not in have]
    warnings = list(manifest.get("warnings") or [])
    if missing or warnings:
        detail = []
        if missing:
            detail.append(f"{len(missing)} fallback request(s) had no captured pixels")
        if warnings:
            detail.extend(warnings[:5])
        if keep_failed:
            detail.append(f"Capture diagnostics were kept at {capture_dir}")
        else:
            shutil.rmtree(capture_dir, ignore_errors=True)
        raise RuntimeError("Could not capture every required raster fallback.\n" + "\n".join(detail))
    _log(f"captured {len(manifest.get('assets') or [])} raster fallback asset(s)")
    return capture_dir


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
        dialogs.error(runner.missing_tool_message())
        return
    uspp = dialogs.open_uspp()
    if uspp:
        _open_path(uspp)


def _open_path(src):
    with _busy_operation():
        try:
            packed_from_raw_spp = False
            if not runner.available():
                dialogs.error(runner.missing_tool_message())
                return
            if src.lower().endswith(".spp"):
                packed_from_raw_spp = True
                source_for_name = src
                uspp = _temp_uspp(source_for_name)
                argv, env = runner.pack_args(src, uspp)
                ok, err = progress.run_with_progress(_parent(), "Reading project", argv, env)
                if not ok:
                    dialogs.error(f"Could not read the project.\n{err}")
                    return
            else:
                uspp = src
                source_for_name = _representative_source_path(src, uspp)
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
                if packed_from_raw_spp and plan.get("missing_raster_fallbacks"):
                    dialogs.error(
                        "This project needs raster fallback pixels for this Painter version.\n\n"
                        "Open the project in a Painter version that can read it, then use "
                        "Universal > Save as Universal so the fallback pixels can be captured."
                    )
                    return
                if not dialogs.confirm_lossy(plan):
                    return
            elif plan.get("direction") == "native_upgrade":
                dialogs.info(
                    f"This project was created in v{plan['source_version']}. "
                    "Substance Painter will upgrade it to your version on open."
                )

            temp_out = _temp_spp(source_for_name, target)
            target_binary = version.running_binary()
            _log(f"open: target=v{target}  binary={target_binary}")
            argv, env = runner.build_args(uspp, target, temp_out, target_binary=target_binary)
            ok, err = progress.run_with_progress(_parent(), f"Converting project to v{target}", argv, env)
            if not ok:
                dialogs.error(f"Conversion failed.\n{err}")
                return
            out = _copy_to_open_destination(temp_out, source_for_name, src, target)
            if not out:
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
            _mark_opened_copy_dirty_soon(source_for_name, target)
            _log(f"opened {uspp} as v{target}")
        except Exception as e:
            _log("on_open error: " + traceback.format_exc())
            dialogs.error(f"Unexpected error opening Universal project:\n{e}")


def on_save():
    with _busy_operation():
        try:
            if not runner.available():
                dialogs.error(runner.missing_tool_message())
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
            capture_dir = None
            settings = updater.load_settings()
            try:
                capture_dir = _raster_capture_for_pack(spp, settings=settings)
                argv, env = runner.pack_args(
                    spp,
                    out,
                    raster_capture_dir=capture_dir,
                    raster_budget_mb=settings["raster_budget_mb"],
                )
                ok, err = progress.run_with_progress(_parent(), "Saving Universal Project", argv, env)
                if ok:
                    dialogs.info(f"Saved Universal project:\n{out}")
                else:
                    dialogs.error(f"Export failed.\n{err}")
            except Exception as e:
                _log("raster capture error: " + traceback.format_exc())
                dialogs.error(f"Export failed while capturing raster fallbacks.\n{e}")
            finally:
                if capture_dir:
                    shutil.rmtree(capture_dir, ignore_errors=True)
        except Exception as e:
            _log("on_save error: " + traceback.format_exc())
            dialogs.error(f"Unexpected error saving Universal project:\n{e}")


def _update_progress_dialog():
    dlg = QtWidgets.QProgressDialog("Downloading update...", None, 0, 0, _parent())
    dlg.setWindowTitle("Universal SPP")
    dlg.setWindowModality(QtCore.Qt.ApplicationModal)
    dlg.setCancelButton(None)
    dlg.setMinimumDuration(0)
    dlg.show()

    def update(message, fraction):
        dlg.setLabelText(message)
        if fraction is None:
            dlg.setRange(0, 0)
        else:
            dlg.setRange(0, 100)
            dlg.setValue(max(0, min(100, int(fraction * 100))))
        app = QtWidgets.QApplication.instance()
        if app:
            app.processEvents()

    return dlg, update


def _install_update(info):
    dlg, update_progress = _update_progress_dialog()
    try:
        updater.install_update(
            info,
            plugin_root=os.path.dirname(os.path.abspath(__file__)),
            timeout=120,
            progress=update_progress,
        )
    finally:
        dlg.close()
        dlg.deleteLater()
    dialogs.update_installed(info.version)


def _check_for_updates(manual=False):
    global _update_check_running
    if _update_check_running:
        return
    if _operation_active:
        if manual:
            dialogs.info("Try again after the current Universal SPP operation finishes.", "Universal SPP Updates")
        else:
            QtCore.QTimer.singleShot(60000, _auto_check_updates)
        return

    _update_check_running = True
    settings = updater.load_settings()
    try:
        if manual or updater.should_auto_check(settings):
            settings = updater.mark_checked(settings)
        else:
            return

        try:
            info = updater.get_latest_update(
                current_version=updater.PLUGIN_VERSION,
                include_prereleases=settings.get("include_prereleases", False),
                timeout=10,
            )
        except Exception as e:
            if manual:
                dialogs.error(f"Could not check for updates.\n{e}", "Universal SPP Updates")
            else:
                _log(f"update check skipped: {e}")
            return

        if info is None:
            if manual:
                dialogs.up_to_date(updater.PLUGIN_VERSION)
            return

        if not manual and settings.get("skipped_version") == info.version:
            _log(f"update v{info.version} skipped by user preference")
            return

        action, disable_auto = dialogs.prompt_update(info.version, updater.PLUGIN_VERSION, automatic=not manual)
        if disable_auto:
            settings["auto_check_enabled"] = False
            updater.save_settings(settings)
        if action == "skip":
            settings["skipped_version"] = info.version
            updater.save_settings(settings)
            return
        if action != "install":
            return

        try:
            _install_update(info)
        except Exception as e:
            _log("update install error: " + traceback.format_exc())
            dialogs.error(f"Update failed.\n{e}", "Universal SPP Updates")
    finally:
        _update_check_running = False


def on_check_updates():
    _check_for_updates(manual=True)


def on_plugin_settings():
    settings = updater.load_settings()
    action, settings = dialogs.plugin_settings(
        settings,
        updater.format_last_checked(settings),
        updater.RELEASES_PAGE_URL,
    )
    updater.save_settings(settings)
    if action == "check_now":
        _check_for_updates(manual=True)


def _auto_check_updates():
    try:
        mw = _parent()
        if mw is not None and hasattr(mw, "isVisible") and not mw.isVisible():
            QtCore.QTimer.singleShot(10000, _auto_check_updates)
            return
    except Exception:
        pass
    _check_for_updates(manual=False)


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
    _menu.addSeparator()
    a_check_updates = _menu.addAction("Check for Updates...")
    a_check_updates.triggered.connect(on_check_updates)
    a_plugin_settings = _menu.addAction("Plugin Settings...")
    a_plugin_settings.triggered.connect(on_plugin_settings)
    _actions = [a_open, a_save, a_check_updates, a_plugin_settings]
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
    try:
        QtCore.QTimer.singleShot(8000, _auto_check_updates)
    except Exception as e:
        _log(f"automatic update check setup failed: {e}")
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
