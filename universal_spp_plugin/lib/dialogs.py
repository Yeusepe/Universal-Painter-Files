"""Qt dialogs for the plugin. PySide imported lazily (PySide6 on newer Painter,
PySide2 on older) so the module can be imported anywhere; UI calls only run in Painter."""


def _qt():
    try:
        from PySide6 import QtWidgets
    except ImportError:
        from PySide2 import QtWidgets
    return QtWidgets


def _parent():
    try:
        import substance_painter.ui as ui
        return ui.get_main_window()
    except Exception:
        return None


def open_uspp():
    QtWidgets = _qt()
    path, _ = QtWidgets.QFileDialog.getOpenFileName(_parent(), "Open Universal Project", "", "Universal SPP (*.uspp)")
    return path or None


def save_uspp(suggested=""):
    QtWidgets = _qt()
    path, _ = QtWidgets.QFileDialog.getSaveFileName(_parent(), "Save as Universal Project", suggested, "Universal SPP (*.uspp)")
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
    return box.exec() == QtWidgets.QMessageBox.Ok
