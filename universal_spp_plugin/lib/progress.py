"""A non-blocking progress dialog for the long-running pack/build subprocess.

The converter runs as a QProcess driven by Qt's event loop on the main thread -- no Python
background thread, so there is no extra thread/thread-state for the embedded interpreter to
tear down at shutdown (a leftover one crashes Painter in PyErr_Fetch on exit). PySide6 on
newer Painter, PySide2 on older.
"""
from . import runner


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


_STYLE = """
QDialog { background: #2b2b2e; }
QLabel#title { color: #f2f2f4; font-size: 15px; font-weight: 600; }
QLabel#status { color: #a9a9b2; font-size: 12px; }
"""


def _make_bar(QtCore, QtGui, QtWidgets):
    """Self-drawn rounded bar. QProgressBar falls back to the native Windows style
    on Painter (the 'row of pills' look) no matter the stylesheet, so we paint it
    ourselves and fully control the geometry."""
    class _Bar(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.setFixedHeight(12)
            self._frac = None   # None => indeterminate (animated marquee)
            self._pos = 0.0

        def set_fraction(self, frac):
            self._frac = max(0.0, min(1.0, float(frac)))
            self.update()

        def set_busy(self):
            self._frac = None

        def pulse(self):                # advance marquee while indeterminate
            if self._frac is None:
                self._pos = (self._pos + 0.025) % 1.0
                self.update()

        def paintEvent(self, _):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            w, h = self.width(), self.height()
            r = h / 2.0
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#1f1f22"))
            p.drawRoundedRect(QtCore.QRectF(0, 0, w, h), r, r)
            p.setBrush(QtGui.QColor("#4c8bf5"))
            if self._frac is None:
                bw = w * 0.35
                x = self._pos * (w + bw) - bw
                x0, x1 = max(0.0, x), min(float(w), x + bw)
                if x1 > x0:
                    p.drawRoundedRect(QtCore.QRectF(x0, 0, x1 - x0, h), r, r)
            elif self._frac > 0:
                p.drawRoundedRect(QtCore.QRectF(0, 0, w * self._frac, h), r, r)
            p.end()

    return _Bar()


def run_with_progress(parent, title, argv, env_extra=None):
    """Show a modal progress dialog while `argv` runs as a QProcess. Parses the tool's
    __USPP_PROGRESS__ lines (and phase prefixes) to drive the bar/status. Returns (ok, err)."""
    QtCore, QtGui, QtWidgets = _qt()
    out = {"ok": False, "err": ""}

    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle("Universal SPP")
    dlg.setModal(True)   # block Painter while converting, so the project can't be touched
    dlg.setMinimumWidth(440)
    # Frameless-ish: keep it clean, no help button, can't be closed mid-run.
    dlg.setWindowFlags(QtCore.Qt.Dialog | QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowTitleHint)
    dlg.setStyleSheet(_STYLE)

    lay = QtWidgets.QVBoxLayout(dlg)
    lay.setContentsMargins(22, 20, 22, 20)
    lay.setSpacing(12)

    title_lbl = QtWidgets.QLabel(title)
    title_lbl.setObjectName("title")
    lay.addWidget(title_lbl)

    status_lbl = QtWidgets.QLabel("Starting…")
    status_lbl.setObjectName("status")
    lay.addWidget(status_lbl)

    bar = _make_bar(QtCore, QtGui, QtWidgets)   # starts indeterminate (busy)
    lay.addWidget(bar)

    proc = QtCore.QProcess(dlg)
    proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)   # one stream, no pipe deadlock
    env = QtCore.QProcessEnvironment.systemEnvironment()
    env.insert("USPP_PROGRESS", "1")
    for k, v in (env_extra or {}).items():
        env.insert(k, v)
    proc.setProcessEnvironment(env)
    # Don't flash a console window for the child console exe (Windows).
    if hasattr(proc, "setCreateProcessArgumentsModifier"):
        proc.setCreateProcessArgumentsModifier(
            lambda a: setattr(a, "flags", a.flags | 0x08000000))   # CREATE_NO_WINDOW

    log = []
    buf = {"s": ""}

    def handle(line):
        if line.startswith(runner._PROGRESS_TAG):
            parts = line.split("\t")
            if len(parts) >= 3:
                try:
                    f = float(parts[1])
                    bar.set_busy() if f < 0 else bar.set_fraction(f)
                    if parts[2]:
                        status_lbl.setText(parts[2])
                except Exception:
                    pass
            return
        if line:
            log.append(line)
            for prefix, friendly in runner._PHASES:
                if prefix in line:
                    status_lbl.setText(friendly)
                    break

    def on_read():
        buf["s"] += bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        while "\n" in buf["s"]:
            line, buf["s"] = buf["s"].split("\n", 1)
            handle(line.rstrip("\r"))

    def on_finished(code, status):
        on_read()
        out["ok"] = (code == 0 and status == QtCore.QProcess.NormalExit)
        out["err"] = "" if out["ok"] else ("\n".join(log[-10:]) or f"exited {code}")
        timer.stop()
        dlg.accept()

    proc.readyReadStandardOutput.connect(on_read)
    proc.finished.connect(on_finished)

    timer = QtCore.QTimer(dlg)          # UI-only: animate the marquee while indeterminate
    timer.timeout.connect(bar.pulse)
    timer.start(40)

    proc.start(argv[0], list(argv[1:]))
    (dlg.exec if hasattr(dlg, "exec") else dlg.exec_)()
    # Destroy promptly rather than leaving these parented to the main window until app
    # shutdown, where PySide teardown against the finalizing interpreter can crash.
    proc.deleteLater()
    dlg.deleteLater()
    return out["ok"], out["err"]
