from pathlib import Path

import yaml

from . import ops as ops_engine
from .models import ActionDef, ModuleCategory, ModuleDef, RiskLevel

# Plus exactly one of "command" and "ops" (research G10).
REQUIRED_ACTION_FIELDS = ("id", "label_sk", "label_en", "risk")


class ModuleLoadError(ValueError):
    pass


def _optional_positive_int(path: Path, action_id: str, raw: dict, key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    # bool is an int subclass - `true` in YAML must not become a 1-second timeout.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ModuleLoadError(f"{path}: action '{action_id}' has invalid {key} {value!r}")
    return value


def _optional_bool(path: Path, action_id: str, raw: dict, key: str) -> bool | None:
    value = raw.get(key)
    if value is None:
        return None
    # Strict: a quoted "false" is a truthy string, and a typo must fail the
    # load loudly rather than silently decide the restore point.
    if not isinstance(value, bool):
        raise ModuleLoadError(f"{path}: action '{action_id}' has invalid {key} {value!r} (expected true or false)")
    return value


def _optional_command(path: Path, action_id: str, raw: dict, key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ModuleLoadError(f"{path}: action '{action_id}' has an empty or non-text {key}")
    return value


def _items_command(path: Path, action_id: str, raw: dict) -> str | None:
    """Research G05: the SAFE listing command of a per-item action."""
    items_command = _optional_command(path, action_id, raw, "items_command")
    if items_command is not None and "ops" in raw:
        # ops: act on fixed values; items are picked at run time.
        raise ModuleLoadError(f"{path}: action '{action_id}' cannot have both items_command and ops")
    return items_command


def _command_or_ops(path: Path, action_id: str, raw: dict, risk: RiskLevel, changes_system: bool | None):
    """(command, preview_command, undo_command, ops) of one action: either
    the hand-written command, or everything generated from `ops:`."""
    has_command = "command" in raw
    has_ops = "ops" in raw
    if has_command == has_ops:
        raise ModuleLoadError(f"{path}: action '{action_id}' must have either 'command' or 'ops' (exactly one)")
    if has_command:
        if "ops_message" in raw:
            raise ModuleLoadError(f"{path}: action '{action_id}' has ops_message but no ops")
        if not isinstance(raw["command"], str) or not raw["command"].strip():
            raise ModuleLoadError(f"{path}: action '{action_id}' has an empty command")
        return raw["command"], raw.get("preview_command"), raw.get("undo_command"), []
    for generated in ("preview_command", "undo_command"):
        if generated in raw:
            # A hand-written undo next to ops would restore a fixed default
            # over the exact captured state - the thing ops exist to avoid.
            raise ModuleLoadError(
                f"{path}: action '{action_id}' has ops, so its {generated} is generated - remove it"
            )
    if risk == RiskLevel.SAFE or changes_system is False:
        # Every op changes the system: it needs a confirmation and the
        # restore point like any other change.
        raise ModuleLoadError(
            f"{path}: action '{action_id}' has ops, which change the system - it cannot be SAFE "
            "or set changes_system: false"
        )
    try:
        op_list = ops_engine.parse_ops(raw["ops"])
        message = ops_engine.check_message(raw.get("ops_message"))
        command, preview_command = ops_engine.build(action_id, op_list, message)
    except ops_engine.OpsError as exc:
        raise ModuleLoadError(f"{path}: action '{action_id}': {exc}") from None
    return command, preview_command, None, op_list


def load_module(actions_yaml_path: Path) -> ModuleDef:
    data = yaml.safe_load(actions_yaml_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ModuleLoadError(f"{actions_yaml_path}: top level must be a mapping")
    module_id = data.get("module_id")
    if not module_id:
        raise ModuleLoadError(f"{actions_yaml_path}: missing module_id")

    category_raw = data.get("category", ModuleCategory.DIAGNOSTICS.value)
    try:
        category = ModuleCategory(category_raw)
    except ValueError:
        raise ModuleLoadError(f"{actions_yaml_path}: unknown category '{category_raw}'") from None

    raw_actions = data.get("actions") or []
    if not isinstance(raw_actions, list):
        raise ModuleLoadError(f"{actions_yaml_path}: 'actions' must be a list")

    actions = []
    for raw in raw_actions:
        # A malformed entry (e.g. a bare string) used to raise TypeError or,
        # worse, pass the field check via substring matching - either way it
        # escaped load_all_modules' error collection and broke startup.
        if not isinstance(raw, dict):
            raise ModuleLoadError(f"{actions_yaml_path}: action entry must be a mapping, got {raw!r}")
        missing = [f for f in REQUIRED_ACTION_FIELDS if f not in raw]
        if missing:
            raise ModuleLoadError(f"{actions_yaml_path}: action missing fields {missing}")
        try:
            risk = RiskLevel(raw["risk"])
        except ValueError:
            raise ModuleLoadError(f"{actions_yaml_path}: unknown risk '{raw['risk']}'") from None
        action_id = raw["id"]
        if not isinstance(action_id, str) or not action_id:
            raise ModuleLoadError(f"{actions_yaml_path}: action id must be a non-empty string, got {action_id!r}")
        for retired in ("problem_keywords", "recommended_action_ids"):
            if retired in raw:
                # Research G02: substring rules broke on localized Windows;
                # the command prints PFJSON findings with their fixes now.
                raise ModuleLoadError(
                    f"{actions_yaml_path}: action '{action_id}': {retired} was replaced by PFJSON findings "
                    "(portablefix/pfjson.py)"
                )
        changes_system = _optional_bool(actions_yaml_path, action_id, raw, "changes_system")
        items_command = _items_command(actions_yaml_path, action_id, raw)
        command, preview_command, undo_command, op_list = _command_or_ops(
            actions_yaml_path, action_id, raw, risk, changes_system,
        )
        check_command = _optional_command(actions_yaml_path, action_id, raw, "check_command")
        if op_list:
            if check_command is not None:
                raise ModuleLoadError(
                    f"{actions_yaml_path}: action '{action_id}' has ops, so its check_command is generated - remove it"
                )
            check_command = ops_engine.check_script(op_list)
        elif check_command is not None and (risk == RiskLevel.SAFE or items_command is not None):
            # A read-only action has nothing to be "already applied"; a
            # per-item action is checked item by item in its own listing.
            raise ModuleLoadError(
                f"{actions_yaml_path}: action '{action_id}' cannot have a check_command (SAFE or per-item action)"
            )
        if changes_system is False and risk == RiskLevel.DESTRUCTIVE:
            # A DESTRUCTIVE action always gets the restore point - there is
            # no "irreversible but not worth a safety net".
            raise ModuleLoadError(
                f"{actions_yaml_path}: action '{action_id}' is DESTRUCTIVE and cannot set changes_system: false"
            )
        restarts_pc = _optional_bool(actions_yaml_path, action_id, raw, "restarts_pc") is True
        restart_before_next = _optional_bool(actions_yaml_path, action_id, raw, "restart_before_next") is True
        if (restarts_pc or restart_before_next) and risk != RiskLevel.REQUIRES_REBOOT:
            # The review screen and the report explain restarts by the risk
            # tier - a restart hidden behind SAFE/MODERATE would surprise.
            raise ModuleLoadError(
                f"{actions_yaml_path}: action '{action_id}' restarts the PC but its risk is not REQUIRES_REBOOT"
            )
        actions.append(
            ActionDef(
                id=action_id,
                label_sk=raw["label_sk"],
                label_en=raw["label_en"],
                risk=risk,
                command=command,
                description_sk=raw.get("description_sk", ""),
                description_en=raw.get("description_en", ""),
                preview_command=preview_command,
                undo_command=undo_command,
                inactivity_timeout_sec=_optional_positive_int(
                    actions_yaml_path, action_id, raw, "inactivity_timeout_sec"
                ),
                hard_cap_sec=_optional_positive_int(actions_yaml_path, action_id, raw, "hard_cap_sec"),
                exclude_from_select_all=raw.get("exclude_from_select_all", False) is True,
                changes_system=changes_system,
                stresses_disk=_optional_bool(actions_yaml_path, action_id, raw, "stresses_disk") is True,
                restarts_pc=restarts_pc,
                restart_before_next=restart_before_next,
                ops=op_list,
                items_command=items_command,
                check_command=check_command,
            )
        )
    return ModuleDef(module_id=module_id, actions=actions, category=category)


USER_MODULES_DIR = "UserModules"


def load_catalog(assets_dir: Path) -> tuple[list[ModuleDef], list[str]]:
    """Modules/ plus the shop's own UserModules/ (research G32) - a folder
    the updater never replaces, its modules marked custom. A user module
    cannot reuse a built-in module or action id: the built-in one wins."""
    return load_all_modules(Path(assets_dir) / "Modules", Path(assets_dir) / USER_MODULES_DIR)


def load_all_modules(modules_dir: Path, user_modules_dir: Path | None = None) -> tuple[list[ModuleDef], list[str]]:
    modules: list[ModuleDef] = []
    errors: list[str] = []
    seen_action_ids: dict[str, Path] = {}
    seen_module_ids: dict[str, Path] = {}
    paths = [(path, False) for path in sorted(modules_dir.glob("*/actions.yaml"))]
    if user_modules_dir is not None:
        paths += [(path, True) for path in sorted(user_modules_dir.glob("*/actions.yaml"))]
    for path, custom in paths:
        try:
            module = load_module(path)
            module.custom = custom
            if module.module_id in seen_module_ids:
                raise ModuleLoadError(f"module_id '{module.module_id}' already used by {seen_module_ids[module.module_id]}")
            ids_in_module = [a.id for a in module.actions]
            same_file_dupes = {i for i in ids_in_module if ids_in_module.count(i) > 1}
            if same_file_dupes:
                raise ModuleLoadError(f"duplicate action id(s) {sorted(same_file_dupes)} within {path}")
            collisions = [a.id for a in module.actions if a.id in seen_action_ids]
            if collisions:
                first_path = seen_action_ids[collisions[0]]
                raise ModuleLoadError(
                    f"duplicate action id(s) {collisions} already used by {first_path}"
                )
            for action in module.actions:
                seen_action_ids[action.id] = path
            seen_module_ids[module.module_id] = path
            modules.append(module)
        except (ModuleLoadError, yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            errors.append(f"{path}: {exc}")
    return modules, errors
