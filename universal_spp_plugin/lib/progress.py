"""An elegant, non-blocking progress dialog for the long-running pack/build subprocess.

The work runs on a plain background thread and pushes updates into a queue; a Qt timer
on the UI thread drains it and updates a status line + progress bar. This sidesteps the
cross-thread Qt signal pitfalls (and the 'define a QThread subclass after a lazy import'
awkwardness) while keeping the UI responsive. PySide6 on newer Painter, PySide2 on older.
"""
import threading
import queue


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


def run_with_progress(parent, title, work):
    """Show a modal progress dialog while `work(on_progress)` runs on a worker thread.

    work(on_progress) must return (ok: bool, err: str). It should call
    on_progress(frac, message): frac in [0,1] for a determinate bar, or None to keep
    the bar 'busy'/indeterminate while still updating the message.

    Returns (ok, err).
    """
    QtCore, QtGui, QtWidgets = _qt()
    q = queue.Queue()
    out = {"ok": False, "err": ""}

    def worker():
        try:
            ok, err = work(lambda frac, msg: q.put(("step", frac, msg)))
        except Exception as e:  # never let the worker die silently
            ok, err = False, str(e)
        q.put(("done", ok, err))

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

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    def tick():
        bar.pulse()                       # keep the marquee moving when indeterminate
        try:
            while True:
                item = q.get_nowait()
                if item[0] == "step":
                    _, frac, msg = item
                    if msg:
                        status_lbl.setText(msg)
                    if frac is None:
                        bar.set_busy()
                    else:
                        bar.set_fraction(frac)
                elif item[0] == "done":
                    out["ok"], out["err"] = item[1], item[2]
                    timer.stop()
                    dlg.accept()
                    return
        except queue.Empty:
            pass

    timer = QtCore.QTimer(dlg)
    timer.timeout.connect(tick)
    timer.start(40)

    (dlg.exec if hasattr(dlg, "exec") else dlg.exec_)()
    return out["ok"], out["err"]
