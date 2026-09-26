"""Headless batch run (research G21):

    PortableFix.exe --preset <name|file.json> [--live] [--out DIR]
                    [--accept-risk MODERATE|DESTRUCTIVE] [--job-client X]
    PortableFix.exe --export-preset <name> <file.json>

DRY-RUN unless --live. A live run refuses up front when the preset holds an
action above --accept-risk (none given: SAFE only), or a pre-flight blocker.
Every action goes through portablefix/action_service.py like in the window,
so the audit log, undo.ps1 and the report are the same files.

Exit codes follow Tron's convention: 0 OK, 1 error (an action failed or the
run was refused), 2 warning (something was skipped, or a diagnostic found
a problem - an Attention/Critical finding, research G02), 3 unsupported OS,
4 a restart is pending (before or after the run), 5 running from %TEMP%.

A preset file is {"name": "...", "actions": [ids], "items": {id: [item ids]}};
"items" picks the items of per-item actions (research G05) - such an action
without them is skipped, never run on everything.
"""

import argparse
import json
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import action_service, health, intake, paths, preflight, report, restore_point, snapshot, undo
from . import items as items_mod
from .audit_log import append_entry, make_entry
from .executor import PlanRun
from .models import ActionDef, ModuleDef, RiskLevel
from .module_engine import load_catalog
from .settings import PRESETS, Settings, load_settings

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_WARNING = 2
EXIT_UNSUPPORTED_OS = 3
EXIT_REBOOT_PENDING = 4
EXIT_FROM_TEMP = 5

CLI_SWITCHES = ("--preset", "--export-preset")
# What --accept-risk allows; REQUIRES_REBOOT counts as MODERATE.
_RISK_RANK = {RiskLevel.SAFE: 0, RiskLevel.MODERATE: 1, RiskLevel.REQUIRES_REBOOT: 1, RiskLevel.DESTRUCTIVE: 2}
_MAX_PRESET_ACTIONS = 500


def wants_cli(argv: list[str]) -> bool:
    """True when the command line asks for the headless run, not the window."""
    return any(arg == s or arg.startswith(s + "=") for arg in argv[1:] for s in CLI_SWITCHES)


class CliError(Exception):
    """A refused command line or preset - exit 1 with this message."""


class _Parser(argparse.ArgumentParser):
    # argparse exits with 2 on a bad argument, which is "warning" here.
    def error(self, message):
        raise CliError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="PortableFix", add_help=False)
    parser.add_argument("--preset")
    parser.add_argument("--export-preset", nargs=2, metavar=("NAME", "FILE"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--accept-risk", choices=("MODERATE", "DESTRUCTIVE"))
    parser.add_argument("--job-client", default="")
    parser.add_argument("--language", choices=("sk", "en"))
    return parser


@dataclass
class Preset:
    name: str
    action_ids: list[str]
    items: dict[str, list[str]] = field(default_factory=dict)


def _clean_ids(raw, what: str) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(i, str) and i for i in raw):
        raise CliError(f"{what} must be a list of ids")
    if len(raw) > _MAX_PRESET_ACTIONS:
        raise CliError(f"{what}: at most {_MAX_PRESET_ACTIONS}")
    return list(dict.fromkeys(raw))


def load_preset(value: str, settings: Settings) -> Preset:
    """A built-in preset, one of the technician's saved presets, or a JSON
    file (an existing path, or any value ending in .json)."""
    path = Path(value)
    if value.lower().endswith(".json") or path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CliError(f"cannot read preset file {value}: {exc}") from exc
        if not isinstance(raw, dict):
            raise CliError(f"{value}: a preset file is a JSON object")
        items_raw = raw.get("items") or {}
        if not isinstance(items_raw, dict):
            raise CliError(f"{value}: \"items\" must map action ids to item ids")
        try:
            chosen = {aid: items_mod.check_ids(ids) for aid, ids in items_raw.items()}
        except items_mod.ItemsError as exc:
            raise CliError(f"{value}: {exc}") from exc
        name = raw.get("name") if isinstance(raw.get("name"), str) else path.stem
        return Preset(name, _clean_ids(raw.get("actions"), f"{value}: \"actions\""), chosen)
    if value in PRESETS:
        return Preset(value, list(PRESETS[value]))
    if value in settings.custom_presets:
        return Preset(value, list(settings.custom_presets[value]))
    known = ", ".join(list(PRESETS) + list(settings.custom_presets))
    raise CliError(f"unknown preset {value!r} (known: {known})")


def export_preset(name: str, dest: Path, settings: Settings) -> Path:
    preset = load_preset(name, settings)
    dest.write_text(json.dumps({"name": preset.name, "actions": preset.action_ids, "items": preset.items},
                               indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


def _unsupported_os() -> bool:
    if sys.platform != "win32":
        return True
    return sys.getwindowsversion().major < 10


def _running_from_temp(app_dir: Path) -> bool:
    # The same test the temp cleanup protects itself with: a %TEMP% child
    # holding the app exists exactly when the app lives inside %TEMP%.
    return paths.compute_temp_protected_child(app_dir) is not None


@dataclass
class Deps:
    """What the run touches outside PowerShell - replaced in the tests."""
    unsupported_os: object = _unsupported_os
    running_from_temp: object = _running_from_temp
    is_admin: object = None
    target_user: object = None
    probes: object = None
    create_restore_point: object = restore_point.create_restore_point
    take_snapshot: object = None
    out: object = None


class _Run:
    def __init__(self, args, preset: Preset, modules: list[ModuleDef], state_dir: Path, settings: Settings,
                 language: str, deps: Deps, storage_fallback: bool):
        self.args, self.preset, self.modules, self.state_dir = args, preset, modules, state_dir
        self.settings, self.language, self.deps = settings, language, deps
        self.storage_fallback = storage_fallback
        self.dry_run = not args.live
        self.run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        self.admin = bool(deps.is_admin())
        self.target = deps.target_user()
        self.undo_steps: list[str] = []
        self.irreversible: list[str] = []
        self.checks: dict[str, str] = {}
        self.findings: dict[str, dict] = {}
        self.selected: dict[str, list[str]] = {}
        self.failed = False
        self.warned = False
        self.reboot_pending = False
        self._actions = {a.id: (m, a) for m in modules for a in m.actions}

    def say(self, line: str) -> None:
        self.deps.out(line)

    def find(self, action_id: str) -> tuple[ModuleDef, ActionDef]:
        return self._actions[action_id]

    def log_system(self, action_id: str, exit_code, output: str, **fields) -> None:
        entry = make_entry(
            "_system", action_id, fields.pop("command", ""), exit_code, output, self.dry_run, self.run_id,
            elevated=self.admin, **{**self.target.audit_fields(), **fields},
        )
        self._append(entry)

    def _append(self, entry) -> None:
        try:
            append_entry(self.state_dir, self.run_id, entry)
        except OSError:
            self.say("[PortableFix] Could not write the audit log.")
            self.failed = True

    def write_undo(self) -> None:
        try:
            undo.create_undo_script(
                self.state_dir, self.run_id, steps=list(reversed(self.undo_steps)), irreversible=self.irreversible,
            )
        except OSError:
            self.say("[PortableFix] Could not write undo.ps1.")
            self.failed = True

    # --- before the batch ---

    def plan_queue(self) -> list[str]:
        unknown = [aid for aid in self.preset.action_ids if aid not in self._actions]
        if unknown:
            raise CliError("not in the catalog: " + ", ".join(unknown))
        queue = list(self.preset.action_ids)
        restarting = [aid for aid in queue if self.find(aid)[1].restarts_pc]
        if restarting:
            # Unattended, a restart would cut off the report and undo.ps1
            # the window writes first - and whoever started the run.
            raise CliError("restarts Windows, not run headless: " + ", ".join(restarting))
        if not self.dry_run:
            limit = _RISK_RANK[RiskLevel(self.args.accept_risk)] if self.args.accept_risk else 0
            over = [aid for aid in queue if _RISK_RANK[self.find(aid)[1].risk] > limit]
            if over:
                raise CliError(
                    "above the accepted risk (--accept-risk " + (self.args.accept_risk or "not given, SAFE only") + "): "
                    + ", ".join(f"{aid} [{self.find(aid)[1].risk.value}]" for aid in over)
                )
        return queue

    def choose_items(self, queue: list[str]) -> list[str]:
        """Per-item actions (G05): the preset's ids, limited to what the
        listing shows now. Nothing left to work on: skipped, with a warning."""
        result = []
        for aid in queue:
            action = self.find(aid)[1]
            if not action.items_command:
                result.append(aid)
                continue
            wanted = self.preset.items.get(aid, [])
            if not wanted:
                self.say(f"[PortableFix] {aid}: the preset picks no items - skipped.")
                self.warned = True
                continue
            listed = action_service.list_items(action, self.target)
            if listed is None:
                self.say(f"[PortableFix] {aid}: the item list could not be read - skipped.")
                self.warned = True
                continue
            present = {item.id for item in listed}
            gone = [i for i in wanted if i not in present]
            if gone:
                self.say(f"[PortableFix] {aid}: not listed any more, skipped: {', '.join(gone)}")
                self.warned = True
            chosen = [i for i in wanted if i in present]
            if not chosen:
                continue
            self.selected[aid] = chosen
            result.append(aid)
        return result

    def run_preflight(self, queue: list[str]) -> int | None:
        """An exit code when a blocker refuses the batch, else None."""
        if self.dry_run:
            return None
        result = preflight.run_preflight(preflight.profile_for([self.find(aid) for aid in queue]), self.deps.probes())
        self.log_system("preflight", 1 if result.blockers else 0, result.summary())
        for issue in result.issues:
            self.say(f"[PortableFix] Pre-flight {issue.severity}: {issue.text(self.language)}")
        if result.warnings:
            self.warned = True
        if not result.blockers:
            return None
        if any(issue.code == "pending_reboot" for issue in result.blockers):
            return EXIT_REBOOT_PENDING
        return EXIT_ERROR

    # --- the batch ---

    def take_restore_point(self, module: ModuleDef, action: ActionDef, queue: list[str]) -> list[str]:
        """The one restore point before the first system change. Unattended,
        a failure cannot be asked about: the actions it guarded are skipped."""
        self.write_undo()
        success, detail = result = self.deps.create_restore_point(f"PortableFix {self.run_id}")
        info = getattr(result, "info", {}) or {}
        sequence = info.get("sequence_number") if success else None
        output = f"System Restore Point created (#{sequence})." if sequence is not None else (
            "System Restore Point created." if success else f"System Restore Point creation failed: {detail}".rstrip(": ")
        )
        subject = f"{module.module_id}/{action.id}"
        self.log_system(
            "restore_point", 0 if success else 1, output,
            command=f"Checkpoint-Computer -Description 'PortableFix {self.run_id}'", subject=subject,
            restore_point_sequence=sequence, restore_point_created=info.get("creation_time", "") if success else "",
        )
        self.say(f"[PortableFix] {output}")
        if success:
            return queue
        self.log_system(
            "restore_point_decision", 0,
            "Headless run: no restore point, so the actions that needed one were skipped.",
            subject=subject, decision="skip",
        )
        self.warned = True
        return [aid for aid in queue if not preflight.needs_restore_point(*self.find(aid))]

    def run_action(self, module: ModuleDef, action: ActionDef) -> int | None:
        """Runs one action; its exit code, or None when it was refused."""
        temp_protect, refusal = action_service.temp_protection(action)
        if refusal:
            self.say(f"[PortableFix] {action.id}: refused - the app's own folder is the temp folder.")
            self.failed = True
            return None
        item_ids = self.selected.get(action.id) if action.items_command else None
        try:
            prepared = action_service.prepare_plan(
                action, dry_run=self.dry_run, state_dir=self.state_dir, run_id=self.run_id,
                target_user=self.target, temp_protect=temp_protect, item_ids=item_ids,
            )
        except (OSError, items_mod.ItemsError) as exc:
            self.say(f"[PortableFix] {action.id}: could not prepare the run: {exc}")
            self.failed = True
            return None
        run = PlanRun(
            prepared.plan, inactivity_timeout_sec=action.inactivity_timeout_sec, hard_cap_sec=action.hard_cap_sec,
            check_plan=action_service.check_plan(action, dry_run=self.dry_run, target_user=self.target),
        )
        code = run.run(self.say)
        entry = action_service.audit_entry(
            module.module_id, action, code, run.captured_output, dry_run=self.dry_run, run_id=self.run_id,
            elevated=self.admin, target_user=self.target, payloads=run.pfjson, item_ids=item_ids,
            decision=action_service.ALREADY_APPLIED if run.skipped_applied else "",
        )
        entry.command = prepared.plan.display_command
        self._append(entry)
        for finding in entry.findings:
            self.findings.pop(finding["id"], None)
            self.findings[finding["id"]] = finding
            self.say(f"[PortableFix] Finding {finding['id']}: {finding['severity']} - {finding['msg_en']}")
        if run.check_state and code == 0:
            self.checks[action.id] = run.check_state
        if not self.dry_run and not run.skipped_applied:
            outcome = action_service.undo_outcome(
                action, code, language=self.language, target_user=self.target, ops_state=prepared.ops_state,
                item_ids=item_ids, payloads=run.pfjson,
            )
            if outcome.steps or outcome.irreversible:
                self.undo_steps.extend(outcome.steps)
                if outcome.irreversible:
                    self.irreversible.append(outcome.irreversible)
                self.write_undo()
        status = "already applied - skipped" if run.skipped_applied else ("OK" if code == 0 else f"FAILED (exit {code})")
        self.say(f"[PortableFix] {action.id}: {status}")
        return code

    def execute(self) -> int:
        queue = self.choose_items(self.plan_queue())
        refused = self.run_preflight(queue)
        if refused is not None:
            self.say("[PortableFix] Refused by the pre-flight check - nothing was run.")
            return refused
        started = time.monotonic()
        snapshot_before = self.deps.take_snapshot()
        restore_point_done = False
        while queue:
            aid = queue.pop(0)
            module, action = self.find(aid)
            if not self.dry_run and not restore_point_done and preflight.needs_restore_point(module, action):
                restore_point_done = True
                remaining = self.take_restore_point(module, action, [aid] + queue)
                queue = remaining[1:] if remaining[:1] == [aid] else remaining
                if remaining[:1] != [aid]:
                    continue
            self.say(f"[PortableFix] {aid} ...")
            code = self.run_action(module, action)
            if code is None:
                continue
            if code != 0:
                self.failed = True
            elif not self.dry_run and (action.restart_before_next or action.risk == RiskLevel.REQUIRES_REBOOT):
                self.reboot_pending = True
                if action.restart_before_next and queue:
                    # Research G03: the rest must not run before the restart.
                    self.log_system(
                        "restart_pending", 0,
                        f"Headless run stopped for a restart after {aid}; not run: {', '.join(queue)}.",
                    )
                    self.say(f"[PortableFix] Restart needed after {aid} - not run: {', '.join(queue)}")
                    self.warned = True
                    break
        self.log_system(intake.BATCH_DURATION_EVENT, 0, intake.batch_duration_output(time.monotonic() - started))
        snapshot_after = self.deps.take_snapshot(checks=self.checks)
        try:
            html_path, _ = report.generate_report(
                self.state_dir, self.run_id, self.modules, self.language, snapshot_before, snapshot_after,
                job={"technician": self.settings.technician_name, "client": self.args.job_client[:120], "note": ""},
                storage_fallback=self.storage_fallback, redact=self.settings.redact_for_client,
                branding=self.settings.branding_info(),
            )
            self.say(f"[PortableFix] Report: {html_path}")
        except OSError as exc:
            self.say(f"[PortableFix] Could not write the report: {exc}")
            self.failed = True
        if self.failed:
            return EXIT_ERROR
        if self.reboot_pending:
            return EXIT_REBOOT_PENDING
        # A problem a diagnostic found (latest state per finding) is what an
        # RMM that ran the preset wants to hear about.
        return EXIT_WARNING if self.warned or health.problems(self.findings) else EXIT_OK


def say(line: str) -> None:
    # A windowed (frozen) exe has no stdout unless the caller redirected it.
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except (OSError, UnicodeError):
            pass


def run(argv: list[str], *, assets_dir: Path | None = None, deps: Deps | None = None) -> int:
    """The headless run for sys.argv-style `argv`; returns the exit code."""
    deps = deps or Deps()
    out = deps.out = deps.out or say
    try:
        args = build_parser().parse_args(argv[1:])
        if deps.unsupported_os():
            out("[PortableFix] Windows 10 or later is required.")
            return EXIT_UNSUPPORTED_OS
        assets_dir = assets_dir or paths.get_base_dir()
        if deps.running_from_temp(assets_dir):
            out("[PortableFix] Refusing to run from the temp folder - copy PortableFix elsewhere first.")
            return EXIT_FROM_TEMP
        if args.out:
            state_dir, storage_fallback = Path(args.out), False
            state_dir.mkdir(parents=True, exist_ok=True)
        else:
            state_dir, storage_fallback = paths.resolve_writable_base_dir(assets_dir)
        settings = load_settings(state_dir)
        if args.export_preset:
            name, dest = args.export_preset
            out(f"[PortableFix] Preset saved: {export_preset(name, Path(dest), settings)}")
            return EXIT_OK
        if not args.preset:
            raise CliError("--preset is required")
        preset = load_preset(args.preset, settings)
        modules, errors = load_catalog(assets_dir)
        for error in errors:
            out(f"[PortableFix] Module not loaded: {error}")
        if deps.is_admin is None:
            from .elevation import is_admin
            deps.is_admin = is_admin
        if deps.target_user is None:
            from .target_user import detect
            deps.target_user = detect
        if deps.probes is None:
            deps.probes = lambda: preflight.system_probes(is_admin=deps.is_admin, busy_tasks=lambda: [])
        if deps.take_snapshot is None:
            deps.take_snapshot = lambda checks=None: snapshot.take_snapshot(disk_usage=shutil.disk_usage, checks=checks)
        language = args.language or settings.language
        batch = _Run(args, preset, modules, state_dir, settings, language, deps, storage_fallback)
        out(f"[PortableFix] {'LIVE' if args.live else 'DRY-RUN'} preset {preset.name}, run {batch.run_id}")
        code = batch.execute()
        if errors and code == EXIT_OK:
            code = EXIT_WARNING
        return code
    except CliError as exc:
        out(f"[PortableFix] {exc}")
        return EXIT_ERROR
    except OSError as exc:
        out(f"[PortableFix] {exc}")
        return EXIT_ERROR
