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
    items = list(plan.get("lost_features", []) or [])
    missing_raster = list(plan.get("missing_raster_fallbacks", []) or [])
    if missing_raster:
        items.append(
            f"{len(missing_raster)} raster fallback(s) are missing; affected unsupported "
            "layers or folders may open blank."
        )
    editable_loss = list(plan.get("editable_loss", []) or [])
    if editable_loss:
        items.append("Some fallback areas will be flattened: " + ", ".join(editable_loss))
    if not items:
        items.append("Some unsupported data may be changed.")
    bullets = "\n".join(f"  - {f}" for f in items)
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


def plugin_settings(settings, last_checked_text, releases_url):
    """Show plugin settings. Returns (action, new_settings)."""
    QtCore, QtGui, QtWidgets = _qt_full()
    new_settings = dict(settings)
    state = {
        "action": "close",
        "skipped_version": settings.get("skipped_version") or "",
    }

    dlg = QtWidgets.QDialog(_parent())
    dlg.setWindowTitle("Universal SPP Plugin Settings")
    dlg.setMinimumWidth(470)

    layout = QtWidgets.QVBoxLayout(dlg)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)

    raster_group = QtWidgets.QGroupBox("Rasterization")
    raster_form = QtWidgets.QFormLayout(raster_group)

    raster_enabled_cb = QtWidgets.QCheckBox("Capture raster fallbacks when saving")
    raster_enabled_cb.setChecked(bool(settings.get("raster_capture_enabled", True)))
    raster_form.addRow(raster_enabled_cb)

    depth_combo = QtWidgets.QComboBox()
    depth_combo.addItem("Match source channel", "source")
    depth_combo.addItem("8-bit", "8")
    depth_combo.addItem("16-bit", "16")
    depth_index = depth_combo.findData(settings.get("raster_content_bit_depth", "source"))
    depth_combo.setCurrentIndex(max(0, depth_index))
    raster_form.addRow("Content bit depth:", depth_combo)

    padding_combo = QtWidgets.QComboBox()
    padding_combo.addItem("Transparent", "transparent")
    padding_combo.addItem("Infinite", "infinite")
    padding_index = padding_combo.findData(settings.get("raster_padding", "transparent"))
    padding_combo.setCurrentIndex(max(0, padding_index))
    raster_form.addRow("Padding:", padding_combo)

    budget_spin = QtWidgets.QSpinBox()
    budget_spin.setRange(64, 4096)
    budget_spin.setSingleStep(64)
    budget_spin.setSuffix(" MB")
    budget_spin.setValue(int(settings.get("raster_budget_mb", 512)))
    raster_form.addRow("Archive budget:", budget_spin)

    timeout_spin = QtWidgets.QSpinBox()
    timeout_spin.setRange(5, 300)
    timeout_spin.setSuffix(" seconds")
    timeout_spin.setValue(int(settings.get("raster_evaluation_timeout_seconds", 30)))
    raster_form.addRow("Painter evaluation timeout:", timeout_spin)

    keep_failed_cb = QtWidgets.QCheckBox("Keep failed capture diagnostics")
    keep_failed_cb.setChecked(bool(settings.get("keep_failed_raster_captures", True)))
    raster_form.addRow(keep_failed_cb)

    def set_raster_controls_enabled(enabled):
        for widget in (depth_combo, padding_combo, budget_spin, timeout_spin, keep_failed_cb):
            widget.setEnabled(enabled)

    raster_enabled_cb.toggled.connect(set_raster_controls_enabled)
    set_raster_controls_enabled(raster_enabled_cb.isChecked())
    layout.addWidget(raster_group)

    update_group = QtWidgets.QGroupBox("Updates")
    update_layout = QtWidgets.QVBoxLayout(update_group)

    auto_cb = QtWidgets.QCheckBox("Automatically check for updates daily")
    auto_cb.setChecked(bool(settings.get("auto_check_enabled", True)))
    update_layout.addWidget(auto_cb)

    prerelease_cb = QtWidgets.QCheckBox("Include prerelease versions")
    prerelease_cb.setChecked(bool(settings.get("include_prereleases", False)))
    update_layout.addWidget(prerelease_cb)

    last_checked = QtWidgets.QLabel(f"Last checked: {last_checked_text}")
    update_layout.addWidget(last_checked)

    skipped_text = state["skipped_version"] or "None"
    skipped_label = QtWidgets.QLabel(f"Skipped version: {skipped_text}")
    update_layout.addWidget(skipped_label)

    clear_skip_btn = QtWidgets.QPushButton("Clear Skipped Version")
    clear_skip_btn.setEnabled(bool(state["skipped_version"]))

    def clear_skip():
        state["skipped_version"] = ""
        skipped_label.setText("Skipped version: None")
        clear_skip_btn.setEnabled(False)

    clear_skip_btn.clicked.connect(clear_skip)
    update_layout.addWidget(clear_skip_btn)
    layout.addWidget(update_group)

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
        new_settings["raster_capture_enabled"] = raster_enabled_cb.isChecked()
        new_settings["raster_content_bit_depth"] = depth_combo.currentData()
        new_settings["raster_padding"] = padding_combo.currentData()
        new_settings["raster_budget_mb"] = budget_spin.value()
        new_settings["raster_evaluation_timeout_seconds"] = timeout_spin.value()
        new_settings["keep_failed_raster_captures"] = keep_failed_cb.isChecked()
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
