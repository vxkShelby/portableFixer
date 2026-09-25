"""Intake / outtake forms, work time and branding (research G20): what is
stored, how it is validated, and what is never stored (a password)."""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from portablefix import branding, intake
from portablefix.audit_log import append_entry, make_entry
from portablefix.report import read_audit_entries
from portablefix.settings import Settings, load_settings, save_settings, settings_path

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x01" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x02" * 32


def _event(tmp_path, run_id, kind, output="", **fields):
    append_entry(tmp_path, run_id, make_entry("_system", kind, "", 0, output, False, run_id, **fields))


# --- Intake -----------------------------------------------------------------


def test_intake_form_keeps_only_known_fields_and_choices():
    form = intake.IntakeForm.from_dict({
        "problem": "  Pomalý štart  ",
        "condition": "Škrabanec na veku",
        "condition_flags": ["liquid_damage", "scratches", "scratches", "bent_hinge", 5],
        "accessories": "nabíjačka",
        "backup": "waiver",
        "password_handling": "given_by_client",
        # Never stored - there is no such field.
        "password": "Heslo123",
        "password_value": "Heslo123",
    })
    assert form.problem == "Pomalý štart"
    # Catalog order, each once, unknown flags dropped.
    assert form.condition_flags == ["scratches", "liquid_damage"]
    assert form.backup == "waiver"
    assert form.password_handling == "given_by_client"
    stored = intake.form_to_output(form)
    assert "Heslo123" not in stored
    assert set(json.loads(stored)) == {
        "problem", "condition", "condition_flags", "accessories", "backup", "password_handling",
    }


def test_intake_form_has_no_field_for_a_password_value():
    # The dataclass itself: only *how* the password was handled.
    fields = set(intake.IntakeForm.__dataclass_fields__)
    assert fields == {"problem", "condition", "condition_flags", "accessories", "backup", "password_handling"}
    assert intake.PASSWORD_CHOICES == ("not_needed", "given_by_client", "reset")


@pytest.mark.parametrize("raw", [None, [], "text", {"backup": "maybe", "password_handling": "secret", "problem": 5}])
def test_intake_form_from_bad_data_is_empty(raw):
    assert intake.IntakeForm.from_dict(raw).is_empty()


def test_intake_form_text_is_cut_to_its_limit():
    form = intake.IntakeForm.from_dict({"problem": "x" * 5000, "accessories": "y" * 900})
    assert len(form.problem) == intake.MAX_PROBLEM_LENGTH
    assert len(form.accessories) == intake.MAX_ACCESSORIES_LENGTH


def test_latest_intake_is_the_last_save_and_a_cleared_form_hides_it(tmp_path):
    run = "run_in"
    _event(tmp_path, run, intake.INTAKE_EVENT, intake.form_to_output(intake.IntakeForm(problem="Prvý")))
    _event(tmp_path, run, intake.INTAKE_EVENT, intake.form_to_output(intake.IntakeForm(problem="Druhý")))
    form, timestamp = intake.latest_intake(read_audit_entries(tmp_path, run))
    assert form.problem == "Druhý"
    assert timestamp
    _event(tmp_path, run, intake.INTAKE_EVENT, intake.form_to_output(intake.IntakeForm()))
    assert intake.latest_intake(read_audit_entries(tmp_path, run)) is None


def test_latest_intake_skips_an_event_whose_output_is_not_a_form(tmp_path):
    run = "run_in2"
    _event(tmp_path, run, intake.INTAKE_EVENT, intake.form_to_output(intake.IntakeForm(problem="Dobrý")))
    _event(tmp_path, run, intake.INTAKE_EVENT, "{torn")
    _event(tmp_path, run, intake.INTAKE_EVENT, "[1, 2]")
    form, _ = intake.latest_intake(read_audit_entries(tmp_path, run))
    assert form.problem == "Dobrý"


def test_forms_ignore_events_of_other_modules(tmp_path):
    run = "run_in3"
    append_entry(tmp_path, run, make_entry(
        "m01", intake.INTAKE_EVENT, "", 0, intake.form_to_output(intake.IntakeForm(problem="x")), False, run,
    ))
    assert intake.latest_intake(read_audit_entries(tmp_path, run)) is None


# --- Outtake ----------------------------------------------------------------


def test_outtake_form_keeps_known_checks_and_results():
    form = intake.OuttakeForm.from_dict({
        "checks": {"wifi": "pass", "sound": "fail", "camera": "na", "usb": "maybe", "bluetooth": "pass"},
        "handed_to": " Ján Novák ",
        "handed_at": "2026-09-25 16:30",
    })
    assert form.checks == {"wifi": "pass", "sound": "fail", "camera": "na"}
    assert form.handed_to == "Ján Novák"
    assert form.handed_at == "2026-09-25 16:30"


def test_outtake_time_without_a_name_is_not_kept():
    form = intake.OuttakeForm.from_dict({"checks": {}, "handed_to": "  ", "handed_at": "2026-09-25 16:30"})
    assert form.handed_at == ""
    assert form.is_empty()


def test_latest_outtake_round_trips_through_the_audit_log(tmp_path):
    run = "run_out"
    saved = intake.OuttakeForm(checks={"display": "pass", "charging": "fail"}, handed_to="Eva", handed_at="2026-09-25 10:00")
    _event(tmp_path, run, intake.OUTTAKE_EVENT, intake.form_to_output(saved))
    form, _ = intake.latest_outtake(read_audit_entries(tmp_path, run))
    assert form == intake.OuttakeForm.from_dict(saved.to_dict())


# --- Work time --------------------------------------------------------------


def test_batch_seconds_sums_valid_durations_only(tmp_path):
    run = "run_bd"
    for output in (
        intake.batch_duration_output(61.4), intake.batch_duration_output(120),
        '{"seconds": -5}', '{"seconds": "10"}', '{"seconds": true}', "garbage",
        json.dumps({"seconds": 30 * 24 * 3600}),
    ):
        _event(tmp_path, run, intake.BATCH_DURATION_EVENT, output)
    assert intake.batch_seconds(read_audit_entries(tmp_path, run)) == (181, 2)


def test_batch_duration_output_never_goes_negative():
    assert json.loads(intake.batch_duration_output(-3)) == {"seconds": 0}


def _timer(decision, when):
    return {"module_id": "_system", "action_id": intake.WORK_TIMER_EVENT, "decision": decision,
            "timestamp": when.isoformat()}


def test_timer_state_pairs_starts_and_stops():
    t0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    entries = [
        _timer("start", t0),
        _timer("start", t0 + timedelta(minutes=5)),  # double click: ignored
        _timer("stop", t0 + timedelta(minutes=30)),
        _timer("stop", t0 + timedelta(minutes=31)),  # already stopped: ignored
        _timer("start", t0 + timedelta(hours=1)),
        _timer("stop", t0 + timedelta(hours=1, minutes=10)),
    ]
    state = intake.timer_state(entries)
    assert state.running_since is None
    assert state.total_seconds() == 40 * 60


def test_running_timer_counts_up_to_now_and_a_backwards_clock_counts_zero():
    t0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    state = intake.timer_state([
        _timer("start", t0), _timer("stop", t0 - timedelta(minutes=10)),
        _timer("start", t0 + timedelta(minutes=1)),
        {"module_id": "_system", "action_id": intake.WORK_TIMER_EVENT, "decision": "stop", "timestamp": "not a time"},
    ])
    assert state.stopped_seconds == 0
    assert state.running_since == t0 + timedelta(minutes=1)
    assert state.total_seconds(t0 + timedelta(minutes=16)) == 15 * 60


@pytest.mark.parametrize("seconds, text", [(0, "0 s"), (45, "45 s"), (60, "1 min"), (719, "11 min"), (3900, "1 h 05 min")])
def test_format_duration(seconds, text):
    assert intake.format_duration(seconds) == text


def test_format_clock():
    assert intake.format_clock(3 * 3600 + 7 * 60 + 5) == "3:07:05"


# --- Branding ---------------------------------------------------------------


@pytest.mark.parametrize("data, mime", [(PNG, "image/png"), (JPEG, "image/jpeg")])
def test_logo_is_recognised_by_magic_bytes(tmp_path, data, mime):
    # The extension says nothing - a PNG named .jpg is still a PNG.
    path = tmp_path / "logo.jpg"
    path.write_bytes(data)
    encoded = branding.load_logo_file(path)
    assert branding.decode_logo(encoded) == (mime, base64.b64encode(data).decode("ascii"))


@pytest.mark.parametrize("data", [b"GIF89a" + b"\x00" * 10, b"<svg onload=alert(1)>", b"", b"BM" + b"\x00" * 50])
def test_logo_that_is_not_png_or_jpeg_is_refused(tmp_path, data):
    path = tmp_path / "logo.png"
    path.write_bytes(data)
    with pytest.raises(branding.LogoError) as error:
        branding.load_logo_file(path)
    assert error.value.code == branding.LOGO_NOT_IMAGE


def test_logo_over_the_limit_is_refused(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(PNG + b"\x00" * branding.MAX_LOGO_BYTES)
    with pytest.raises(branding.LogoError) as error:
        branding.load_logo_file(path)
    assert error.value.code == branding.LOGO_TOO_LARGE
    # Exactly at the limit is fine.
    path.write_bytes(PNG + b"\x00" * (branding.MAX_LOGO_BYTES - len(PNG)))
    assert branding.load_logo_file(path)


def test_missing_logo_file_is_unreadable(tmp_path):
    with pytest.raises(branding.LogoError) as error:
        branding.load_logo_file(tmp_path / "nope.png")
    assert error.value.code == branding.LOGO_UNREADABLE


@pytest.mark.parametrize("value", [
    None, 5, "", "not base64!!", base64.b64encode(b"GIF89a").decode(),
    base64.b64encode(PNG + b"\x00" * branding.MAX_LOGO_BYTES).decode(),
    'iVBORw0KGgo=" onerror="alert(1)',
])
def test_decode_logo_refuses_anything_but_a_valid_image(value):
    assert branding.decode_logo(value) is None


def test_clean_branding_keeps_set_fields_only():
    cleaned = branding.clean_branding({
        "company": "  Servis s.r.o. ", "company_id": "12345678", "contact": "", "logo": "garbage", "extra": "x",
    })
    assert cleaned == {"company": "Servis s.r.o.", "company_id": "12345678"}
    assert branding.clean_branding(None) == {}


def test_branding_settings_round_trip(tmp_path):
    logo = branding.encode_logo(PNG)
    settings = Settings(
        branding_company="Servis s.r.o.", branding_company_id="12345678",
        branding_contact="0900 123 456\nservis@example.sk", branding_logo=logo,
    )
    save_settings(tmp_path, settings)
    loaded = load_settings(tmp_path)
    assert loaded.branding_company == "Servis s.r.o."
    assert loaded.branding_contact == "0900 123 456\nservis@example.sk"
    assert loaded.branding_logo == logo
    assert loaded.branding_info() == {
        "company": "Servis s.r.o.", "company_id": "12345678", "contact": "0900 123 456\nservis@example.sk",
        "logo_mime": "image/png", "logo": logo,
    }


def test_branding_defaults_to_nothing(tmp_path):
    assert Settings().branding_info() == {}
    assert load_settings(tmp_path).branding_info() == {}


def test_hand_edited_branding_settings_are_validated(tmp_path):
    path = settings_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "branding_company": ["not", "a", "string"],
        "branding_company_id": "1" * 100,
        "branding_contact": 42,
        "branding_logo": base64.b64encode(b"<script>").decode(),
        "dry_run": False,
    }), encoding="utf-8")
    loaded = load_settings(tmp_path)
    assert loaded.branding_company == ""
    assert loaded.branding_company_id == "1" * branding.MAX_COMPANY_ID_LENGTH
    assert loaded.branding_contact == ""
    assert loaded.branding_logo == ""
    # The rest of the file still counts.
    assert loaded.dry_run is False
