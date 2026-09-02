from pathlib import Path

import yaml

from .models import ActionDef, ModuleCategory, ModuleDef, RiskLevel

REQUIRED_ACTION_FIELDS = ("id", "label_sk", "label_en", "risk", "command")


class ModuleLoadError(ValueError):
    pass


def load_module(actions_yaml_path: Path) -> ModuleDef:
    data = yaml.safe_load(actions_yaml_path.read_text(encoding="utf-8")) or {}
    module_id = data.get("module_id")
    if not module_id:
        raise ModuleLoadError(f"{actions_yaml_path}: missing module_id")

    category_raw = data.get("category", ModuleCategory.DIAGNOSTICS.value)
    try:
        category = ModuleCategory(category_raw)
    except ValueError:
        raise ModuleLoadError(f"{actions_yaml_path}: unknown category '{category_raw}'") from None

    actions = []
    for raw in data.get("actions", []):
        missing = [f for f in REQUIRED_ACTION_FIELDS if f not in raw]
        if missing:
            raise ModuleLoadError(f"{actions_yaml_path}: action missing fields {missing}")
        try:
            risk = RiskLevel(raw["risk"])
        except ValueError:
            raise ModuleLoadError(f"{actions_yaml_path}: unknown risk '{raw['risk']}'") from None
        actions.append(
            ActionDef(
                id=raw["id"],
                label_sk=raw["label_sk"],
                label_en=raw["label_en"],
                risk=risk,
                command=raw["command"],
                description_sk=raw.get("description_sk", ""),
                description_en=raw.get("description_en", ""),
                preview_command=raw.get("preview_command"),
                undo_command=raw.get("undo_command"),
            )
        )
    return ModuleDef(module_id=module_id, actions=actions, category=category)


def load_all_modules(modules_dir: Path) -> tuple[list[ModuleDef], list[str]]:
    modules: list[ModuleDef] = []
    errors: list[str] = []
    for path in sorted(modules_dir.glob("*/actions.yaml")):
        try:
            modules.append(load_module(path))
        except (ModuleLoadError, yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            errors.append(f"{path}: {exc}")
    return modules, errors
