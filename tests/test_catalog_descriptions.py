from pathlib import Path

from portablefix.module_engine import load_all_modules

MODULES_DIR = Path(__file__).resolve().parent.parent / "Modules"


def test_all_catalogs_load():
    modules = load_all_modules(MODULES_DIR)
    assert len(modules) == 12


def test_every_action_has_both_descriptions():
    for module in load_all_modules(MODULES_DIR):
        for action in module.actions:
            assert action.description_sk, f"{module.module_id}/{action.id}: missing description_sk"
            assert action.description_en, f"{module.module_id}/{action.id}: missing description_en"


def test_every_action_has_distinct_sk_en_labels():
    for module in load_all_modules(MODULES_DIR):
        for action in module.actions:
            assert action.label_sk, f"{module.module_id}/{action.id}: empty label_sk"
            assert action.label_en, f"{module.module_id}/{action.id}: empty label_en"


def test_action_ids_unique_across_all_catalogs():
    seen = {}
    for module in load_all_modules(MODULES_DIR):
        for action in module.actions:
            assert action.id not in seen, f"duplicate action id '{action.id}' in {module.module_id} and {seen[action.id]}"
            seen[action.id] = module.module_id
