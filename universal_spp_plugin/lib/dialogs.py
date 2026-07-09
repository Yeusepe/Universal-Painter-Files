"""Qt dialogs for the plugin. PySide imported lazily (PySide6 on newer Painter,
PySide2 on older) so the module can be imported anywhere; UI calls only run in Painter."""


def _qt():
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide2 import QtWidgets
    return QtWidgets


def _qt_full():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


def _parent():
    try:
        import substance_painter.ui as ui
        return ui.get_main_window()
    except Exception:
        return None


def open_uspp():
    QtWidgets = _qt()
    path, _ = QtWidgets.QFileDialog.getOpenFileName(_parent(), "Open Universal Project", "", "Painter projects (*.uspp *.spp)")
    return path or None


def save_uspp(suggested=""):
    QtWidgets = _qt()
    path, _ = QtWidgets.QFileDialog.getSaveFileName(_parent(), "Save as Universal Project", suggested, "Universal SPP (*.uspp)")
    return path or None


def save_spp(suggested=""):
    QtWidgets = _qt()
    path, _ = QtWidgets.QFileDialog.getSaveFileName(_parent(), "Save Project", suggested, "Substance Painter (*.spp)")
    return path or None


def error(message, title="Universal SPP"):
    QtWidgets = _qt()
    QtWidgets.QMessageBox.critical(_parent(), title, message)


def info(message, title="Universal SPP"):
    QtWidgets = _qt()
    QtWidgets.QMessageBox.information(_parent(), title, message)


def confirm(message, title="Universal SPP"):
    """Yes/No question. Returns True on Yes."""
    QtWidgets = _qt()
    return QtWidgets.QMessageBox.question(
        _parent(), title, message,
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
    ) == QtWidgets.QMessageBox.Yes


def confirm_lossy(plan):
    """Show the plain-English lossy warning. Returns True if the user chooses Continue."""
    QtWidgets = _qt()
    bullets = "\n".join(f"  •  {f}" for f in plan.get("lost_features", []))
    text = (
        f"This project was created in Substance Painter v{plan['source_version']}, "
        f"and will be converted to v{plan['target_version']}.\n\n"
        "This is a one-way, lossy conversion. The following will be lost or changed:\n\n"
        f"{bullets}\n\n"
        "Your original .uspp is not modified. Continue?"
    )
    box = QtWidgets.QMessageBox(_parent())
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("Lossy conversion")
    box.setText(text)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
    box.button(QtWidgets.QMessageBox.Ok).setText("Continue")
    ok = box.exec() == QtWidgets.QMessageBox.Ok

    box.deleteLater()
    return ok


def _exec(dialog):
    return dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()


def prompt_update(latest, current, automatic=True):
    """Return (action, disable_auto). action is install/skip/later/cancel."""
    QtWidgets = _qt()
    box = QtWidgets.QMessageBox(_parent())
    box.setIcon(QtWidgets.QMessageBox.Information)
    box.setWindowTitle("Universal SPP Update Available")
    box.setText(
        f"Universal SPP {latest} is available. You are using {current}. "
        "Install the update now? Restart Substance 3D Painter after installation "
        "to finish using the new version."
    )
    install_btn = box.addButton("Install Update", QtWidgets.QMessageBox.AcceptRole)
    if automatic:
        skip_btn = box.addButton("Skip This Version", QtWidgets.QMessageBox.RejectRole)
        later_btn = box.addButton("Remind Me Later", QtWidgets.QMessageBox.ResetRole)
        auto_check = QtWidgets.QCheckBox("Do not check for updates automatically")
        box.setCheckBox(auto_check)
        box.setDefaultButton(later_btn)
        box.setEscapeButton(later_btn)
    else:
        skip_btn = None
        later_btn = box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
        auto_check = None
        box.setDefaultButton(install_btn)
        box.setEscapeButton(later_btn)

    _exec(box)
    clicked = box.clickedButton()
    disable_auto = bool(auto_check and auto_check.isChecked())
    box.deleteLater()
    if clicked == install_btn:
        return "install", disable_auto
    if automatic and clicked == skip_btn:
        return "skip", disable_auto
    if automatic:
        return "later", disable_auto
    return "cancel", disable_auto


def update_installed(latest):
    info(
        f"Update installed. Restart Substance 3D Painter to finish using Universal SPP {latest}.",
        "Universal SPP Update Installed",
    )


def up_to_date(current):
    info(f"Universal SPP is up to date. You are using {current}.", "Universal SPP Updates")


def update_settings(settings, last_checked_text, releases_url):
    """Show update settings. Returns (action, new_settings)."""
    QtCore, QtGui, QtWidgets = _qt_full()
    new_settings = dict(settings)
    state = {
        "action": "close",
        "skipped_version": settings.get("skipped_version") or "",
    }

    dlg = QtWidgets.QDialog(_parent())
    dlg.setWindowTitle("Universal SPP Update Settings")
    dlg.setMinimumWidth(430)

    layout = QtWidgets.QVBoxLayout(dlg)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)

    auto_cb = QtWidgets.QCheckBox("Automatically check for updates daily")
    auto_cb.setChecked(bool(settings.get("auto_check_enabled", True)))
    layout.addWidget(auto_cb)

    prerelease_cb = QtWidgets.QCheckBox("Include prerelease versions")
    prerelease_cb.setChecked(bool(settings.get("include_prereleases", False)))
    layout.addWidget(prerelease_cb)

    last_checked = QtWidgets.QLabel(f"Last checked: {last_checked_text}")
    layout.addWidget(last_checked)

    skipped_text = state["skipped_version"] or "None"
    skipped_label = QtWidgets.QLabel(f"Skipped version: {skipped_text}")
    layout.addWidget(skipped_label)

    clear_skip_btn = QtWidgets.QPushButton("Clear Skipped Version")
    clear_skip_btn.setEnabled(bool(state["skipped_version"]))

    def clear_skip():
        state["skipped_version"] = ""
        skipped_label.setText("Skipped version: None")
        clear_skip_btn.setEnabled(False)

    clear_skip_btn.clicked.connect(clear_skip)
    layout.addWidget(clear_skip_btn)

    buttons = QtWidgets.QHBoxLayout()
    check_btn = QtWidgets.QPushButton("Check Now")
    releases_btn = QtWidgets.QPushButton("Open Releases Page")
    close_btn = QtWidgets.QPushButton("Close")
    buttons.addWidget(check_btn)
    buttons.addWidget(releases_btn)
    buttons.addStretch(1)
    buttons.addWidget(close_btn)
    layout.addLayout(buttons)

    def apply_values(action):
        new_settings["auto_check_enabled"] = auto_cb.isChecked()
        new_settings["include_prereleases"] = prerelease_cb.isChecked()
        new_settings["skipped_version"] = state["skipped_version"]
        state["action"] = action
        dlg.accept()

    check_btn.clicked.connect(lambda: apply_values("check_now"))
    close_btn.clicked.connect(lambda: apply_values("close"))
    releases_btn.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(releases_url)))

    _exec(dlg)
    if dlg.result() != QtWidgets.QDialog.Accepted:
        apply_values("close")
    dlg.deleteLater()
    return state["action"], new_settings
