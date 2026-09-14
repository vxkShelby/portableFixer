from unittest.mock import MagicMock, patch

from scripts import self_improve


def _fake_completed(stdout: str):
    result = MagicMock()
    result.stdout = stdout
    return result


@patch("scripts.self_improve.subprocess.run")
def test_gather_signal_detects_failing_tests(mock_run, tmp_path, monkeypatch):
    monkeypatch.setattr(self_improve, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(self_improve, "CRASH_LOG", tmp_path / "Logs" / "crash.log")
    monkeypatch.setattr(self_improve, "STATE_FILE", tmp_path / "docs" / ".self_improve_state.json")
    mock_run.side_effect = [
        _fake_completed("2 failed, 100 passed in 5.0s"),  # pytest
        _fake_completed(""),  # git log
    ]

    signal_found, trigger, _ = self_improve.gather_signal()

    assert signal_found is True
    assert "2 failing test(s)" in trigger


@patch("scripts.self_improve.subprocess.run")
def test_gather_signal_quiet_when_nothing_found(mock_run, tmp_path, monkeypatch):
    monkeypatch.setattr(self_improve, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(self_improve, "CRASH_LOG", tmp_path / "Logs" / "crash.log")
    monkeypatch.setattr(self_improve, "STATE_FILE", tmp_path / "docs" / ".self_improve_state.json")
    mock_run.side_effect = [
        _fake_completed("100 passed in 5.0s"),
        _fake_completed(""),
    ]

    signal_found, trigger, _ = self_improve.gather_signal()

    assert signal_found is False
    assert trigger == "none"


@patch("scripts.self_improve.save_state")
@patch("scripts.self_improve.append_soul_entry")
@patch("scripts.self_improve.run_archon")
@patch("scripts.self_improve.gather_signal")
def test_main_calls_archon_and_logs_soul_only_on_signal(
    mock_gather, mock_archon, mock_append_soul, mock_save_state
):
    mock_gather.return_value = (True, "2 failing test(s)", 42)
    mock_archon.return_value = "PR: https://github.com/x/y/pull/1"

    self_improve.main()

    mock_archon.assert_called_once_with("2 failing test(s)")
    mock_append_soul.assert_called_once()
    mock_save_state.assert_called_once_with(self_improve.STATE_FILE, {"crash_log_offset": 42})


@patch("scripts.self_improve.append_soul_entry")
@patch("scripts.self_improve.run_archon")
@patch("scripts.self_improve.gather_signal")
def test_main_skips_archon_and_soul_when_quiet(mock_gather, mock_archon, mock_append_soul):
    mock_gather.return_value = (False, "none", 10)

    self_improve.main()

    mock_archon.assert_not_called()
    mock_append_soul.assert_not_called()
