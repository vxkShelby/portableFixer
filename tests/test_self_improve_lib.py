from pathlib import Path

from scripts.self_improve_lib import (
    append_soul_entry,
    count_pytest_failures,
    find_repeated_fix_file,
    has_signal,
    load_state,
    parse_fix_commit_files,
    read_new_crash_log_entries,
    save_state,
)


def test_count_pytest_failures_parses_failed_count():
    assert count_pytest_failures("3 failed, 490 passed in 12.3s") == 3


def test_count_pytest_failures_zero_when_all_passed():
    assert count_pytest_failures("493 passed in 10.1s") == 0


def test_parse_fix_commit_files_extracts_first_file_per_fix_commit():
    git_log_output = (
        "fix: race condition in executor\n"
        "portablefix/executor.py\n\n"
        "feat: add button\n"
        "portablefix/gui/main_window.py\n\n"
        "fix: race condition again\n"
        "portablefix/executor.py\n"
    )
    assert parse_fix_commit_files(git_log_output) == [
        "portablefix/executor.py",
        "portablefix/executor.py",
    ]


def test_find_repeated_fix_file_returns_file_at_threshold():
    files = ["a.py", "a.py", "a.py", "b.py"]
    assert find_repeated_fix_file(files, min_repeats=3) == "a.py"


def test_find_repeated_fix_file_returns_none_below_threshold():
    files = ["a.py", "a.py", "b.py"]
    assert find_repeated_fix_file(files, min_repeats=3) is None


def test_read_new_crash_log_entries_returns_only_new_text(tmp_path):
    log = tmp_path / "crash.log"
    log.write_text("first crash\n", encoding="utf-8")
    _, offset_after_first = read_new_crash_log_entries(log, 0)

    with log.open("a", encoding="utf-8") as f:
        f.write("second crash\n")

    new_text, new_offset = read_new_crash_log_entries(log, offset_after_first)

    assert new_text == "second crash\n"
    assert new_offset > offset_after_first


def test_read_new_crash_log_entries_missing_file_returns_empty(tmp_path):
    missing = tmp_path / "does_not_exist.log"
    text, offset = read_new_crash_log_entries(missing, 5)
    assert text == ""
    assert offset == 5


def test_has_signal_true_when_any_source_fires():
    assert has_signal(1, None, "") is True
    assert has_signal(0, "a.py", "") is True
    assert has_signal(0, None, "crash!") is True


def test_has_signal_false_when_nothing_fires():
    assert has_signal(0, None, "") is False
    assert has_signal(0, None, "   ") is False


def test_state_round_trip(tmp_path):
    state_file = tmp_path / "state.json"
    save_state(state_file, {"crash_log_offset": 42})
    assert load_state(state_file) == {"crash_log_offset": 42}


def test_load_state_defaults_when_missing(tmp_path):
    state_file = tmp_path / "missing.json"
    assert load_state(state_file) == {"crash_log_offset": 0}


def test_append_soul_entry_creates_file_with_header(tmp_path):
    soul = tmp_path / "SOUL.md"
    append_soul_entry(soul, "2 failing tests", "PR: https://github.com/x/y/pull/1")
    content = soul.read_text(encoding="utf-8")
    assert "self-improve loop log" in content
    assert "2 failing tests" in content
    assert "pull/1" in content


def test_append_soul_entry_appends_not_overwrites(tmp_path):
    soul = tmp_path / "SOUL.md"
    append_soul_entry(soul, "first trigger", "output1")
    append_soul_entry(soul, "second trigger", "output2")
    content = soul.read_text(encoding="utf-8")
    assert "first trigger" in content
    assert "second trigger" in content
