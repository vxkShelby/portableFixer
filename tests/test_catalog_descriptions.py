from pathlib import Path

from portablefix.module_engine import load_all_modules, load_module

MODULES_DIR = Path(__file__).resolve().parent.parent / "Modules"


def test_all_catalogs_load():
    modules, errors = load_all_modules(MODULES_DIR)
    assert errors == []
    assert len(modules) == 22


def test_every_action_has_both_descriptions():
    modules, errors = load_all_modules(MODULES_DIR)
    assert errors == []
    for module in modules:
        for action in module.actions:
            assert action.description_sk, f"{module.module_id}/{action.id}: missing description_sk"
            assert action.description_en, f"{module.module_id}/{action.id}: missing description_en"


def test_every_action_has_distinct_sk_en_labels():
    modules, errors = load_all_modules(MODULES_DIR)
    assert errors == []
    for module in modules:
        for action in module.actions:
            assert action.label_sk, f"{module.module_id}/{action.id}: empty label_sk"
            assert action.label_en, f"{module.module_id}/{action.id}: empty label_en"


def test_action_ids_unique_across_all_catalogs():
    modules, errors = load_all_modules(MODULES_DIR)
    assert errors == []
    seen = {}
    for module in modules:
        for action in module.actions:
            assert action.id not in seen, f"duplicate action id '{action.id}' in {module.module_id} and {seen[action.id]}"
            seen[action.id] = module.module_id


def test_finding_fixes_reference_real_actions_and_are_never_self():
    # Research G02: the fix ids a PFJSON finding points at (`fix = @(...)`
    # in the command) must be actions the catalog has, or the one-click fix
    # is a dead end.
    import re

    modules, errors = load_all_modules(MODULES_DIR)
    assert errors == []
    all_ids = {action.id for module in modules for action in module.actions}
    found = 0
    for module in modules:
        for action in module.actions:
            for group in re.findall(r"fix = @\(([^)]*)\)", action.command):
                for rid in re.findall(r"'([^']+)'", group):
                    found += 1
                    assert rid in all_ids, f"{module.module_id}/{action.id}: finding fix references unknown id '{rid}'"
                    assert rid != action.id, f"{module.module_id}/{action.id}: recommends itself"
    assert found >= 5


def test_no_action_keys_off_known_localized_tool_output():
    # Native Windows tools (vssadmin, powercfg, pnputil) print their labels
    # in the display language, so these English-only matches silently
    # misbehaved on non-English Windows - keep them from coming back.
    forbidden = ("'No items found'", "Select-String 'Ultimate Performance'", "Total driver packages:")
    for path in sorted(MODULES_DIR.glob("*/actions.yaml")):
        module = load_module(path)
        for action in module.actions:
            for field in ("command", "undo_command", "preview_command"):
                text = getattr(action, field) or ""
                for pattern in forbidden:
                    assert pattern not in text, f"{module.module_id}/{action.id}.{field}: contains {pattern}"
