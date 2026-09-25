from pathlib import Path

import yaml

from .models import ActionDef, ModuleCategory, ModuleDef, RiskLevel

REQUIRED_ACTION_FIELDS = ("id", "label_sk", "label_en", "risk", "command")


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


def _string_list(path: Path, action_id: str, raw: dict, key: str) -> list[str]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ModuleLoadError(f"{path}: action '{action_id}' has invalid {key} (expected a list of strings)")
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
        if not isinstance(raw["command"], str) or not raw["command"].strip():
            raise ModuleLoadError(f"{actions_yaml_path}: action '{action_id}' has an empty command")
        changes_system = _optional_bool(actions_yaml_path, action_id, raw, "changes_system")
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
                command=raw["command"],
                description_sk=raw.get("description_sk", ""),
                description_en=raw.get("description_en", ""),
                preview_command=raw.get("preview_command"),
                undo_command=raw.get("undo_command"),
                inactivity_timeout_sec=_optional_positive_int(
                    actions_yaml_path, action_id, raw, "inactivity_timeout_sec"
                ),
                hard_cap_sec=_optional_positive_int(actions_yaml_path, action_id, raw, "hard_cap_sec"),
                problem_keywords=_string_list(actions_yaml_path, action_id, raw, "problem_keywords"),
                recommended_action_ids=_string_list(actions_yaml_path, action_id, raw, "recommended_action_ids"),
                exclude_from_select_all=raw.get("exclude_from_select_all", False) is True,
                changes_system=changes_system,
                stresses_disk=_optional_bool(actions_yaml_path, action_id, raw, "stresses_disk") is True,
                restarts_pc=restarts_pc,
                restart_before_next=restart_before_next,
            )
        )
    return ModuleDef(module_id=module_id, actions=actions, category=category)


def load_all_modules(modules_dir: Path) -> tuple[list[ModuleDef], list[str]]:
    modules: list[ModuleDef] = []
    errors: list[str] = []
    seen_action_ids: dict[str, Path] = {}
    for path in sorted(modules_dir.glob("*/actions.yaml")):
        try:
            module = load_module(path)
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
            modules.append(module)
        except (ModuleLoadError, yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            errors.append(f"{path}: {exc}")
    return modules, errors
