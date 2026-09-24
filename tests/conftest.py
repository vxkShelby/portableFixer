import pytest

try:
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
except ImportError:  # pragma: no cover - non-GUI environments
    QMessageBox = QInputDialog = QFileDialog = None


class UnexpectedDialogError(AssertionError):
    pass


def _refuse(kind: str):
    def _raise(*args, **kwargs):
        text = " | ".join(str(a) for a in args[1:4] if isinstance(a, str))
        raise UnexpectedDialogError(f"unexpected modal {kind} in a test: {text!r}")

    return _raise


@pytest.fixture(autouse=True)
def _no_blocking_modal_dialogs(monkeypatch):
    """Fail fast instead of hanging forever on an unexpected modal dialog.

    Headless (CI, offscreen) nobody can click a static QMessageBox, so a
    test that ended with a batch still running blocked in closeEvent's
    "a batch is running, close anyway?" question during qtbot teardown -
    the process never exited and the per-test CI loop hung until GitHub's
    6h job limit. Tests that expect a dialog still monkeypatch it
    themselves; that later patch overrides this default.
    """
    if QMessageBox is None:
        return
    for name in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, _refuse(f"QMessageBox.{name}"))
    for name in ("getText", "getItem", "getInt", "getDouble"):
        monkeypatch.setattr(QInputDialog, name, _refuse(f"QInputDialog.{name}"))
    for name in ("getSaveFileName", "getOpenFileName", "getExistingDirectory"):
        monkeypatch.setattr(QFileDialog, name, _refuse(f"QFileDialog.{name}"))
