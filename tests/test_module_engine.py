from pathlib import Path

import pytest

from portablefix.module_engine import ModuleLoadError, load_all_modules, load_module
from portablefix.models import ModuleCategory, RiskLevel

VALID_YAML = """
module_id: m_test
actions:
  - id: a1
    label_sk: "Akcia 1"
    label_en: "Action 1"
    risk: SAFE
    command: "Write-Output 'hi'"
    description_sk: "Popis"
    description_en: "Description"
"""


def test_load_module_valid(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(VALID_YAML, encoding="utf-8")
    module = load_module(yaml_path)
    assert module.module_id == "m_test"
    assert len(module.actions) == 1
    action = module.actions[0]
    assert action.id == "a1"
    assert action.risk == RiskLevel.SAFE
    assert action.label("sk") == "Akcia 1"
    assert action.label("en") == "Action 1"


def test_load_module_missing_module_id(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text("actions: []\n", encoding="utf-8")
    with pytest.raises(ModuleLoadError):
        load_module(yaml_path)


def test_load_module_action_missing_field(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(
        "module_id: m_test\nactions:\n  - id: a1\n    risk: SAFE\n    command: x\n",
        encoding="utf-8",
    )
    with pytest.raises(ModuleLoadError):
        load_module(yaml_path)


def test_load_module_unknown_risk(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(
        "module_id: m_test\nactions:\n  - id: a1\n    label_sk: a\n    label_en: a\n"
        "    risk: SUPER_DANGEROUS\n    command: x\n",
        encoding="utf-8",
    )
    with pytest.raises(ModuleLoadError):
        load_module(yaml_path)


def test_load_all_modules_finds_m01(tmp_path):
    (tmp_path / "m01_diagnostics").mkdir()
    (tmp_path / "m01_diagnostics" / "actions.yaml").write_text(VALID_YAML, encoding="utf-8")
    (tmp_path / "m02_cleanup").mkdir()
    (tmp_path / "m02_cleanup" / "actions.yaml").write_text(
        VALID_YAML.replace("m_test", "m02_cleanup").replace("id: a1", "id: a2"), encoding="utf-8"
    )
    modules, errors = load_all_modules(tmp_path)
    assert [m.module_id for m in modules] == ["m_test", "m02_cleanup"]
    assert errors == []


def test_load_all_modules_skips_broken_file_and_reports_error(tmp_path):
    (tmp_path / "m01_diagnostics").mkdir()
    (tmp_path / "m01_diagnostics" / "actions.yaml").write_text(VALID_YAML, encoding="utf-8")
    (tmp_path / "m02_cleanup").mkdir()
    (tmp_path / "m02_cleanup" / "actions.yaml").write_text(
        "module_id: [unclosed\n  bad: yaml: syntax:\n", encoding="utf-8"
    )
    (tmp_path / "m03_disk").mkdir()
    (tmp_path / "m03_disk" / "actions.yaml").write_text(
        VALID_YAML.replace("m_test", "m03_disk").replace("id: a1", "id: a3"), encoding="utf-8"
    )

    modules, errors = load_all_modules(tmp_path)

    assert [m.module_id for m in modules] == ["m_test", "m03_disk"]
    assert len(errors) == 1
    assert "m02_cleanup" in errors[0]


def test_load_all_modules_rejects_module_with_duplicate_action_id(tmp_path):
    (tmp_path / "m01_diagnostics").mkdir()
    (tmp_path / "m01_diagnostics" / "actions.yaml").write_text(VALID_YAML, encoding="utf-8")
    (tmp_path / "m02_cleanup").mkdir()
    (tmp_path / "m02_cleanup" / "actions.yaml").write_text(
        VALID_YAML.replace("m_test", "m02_cleanup"), encoding="utf-8"
    )

    modules, errors = load_all_modules(tmp_path)

    assert [m.module_id for m in modules] == ["m_test"]
    assert len(errors) == 1
    assert "m02_cleanup" in errors[0]
    assert "a1" in errors[0]


def test_load_all_modules_rejects_same_file_duplicate_action_id(tmp_path):
    (tmp_path / "m01_diagnostics").mkdir()
    (tmp_path / "m01_diagnostics" / "actions.yaml").write_text(
        "module_id: m01_diagnostics\n"
        "actions:\n"
        "  - id: a1\n    label_sk: x\n    label_en: x\n    risk: SAFE\n    command: x\n"
        "  - id: a1\n    label_sk: y\n    label_en: y\n    risk: SAFE\n    command: y\n",
        encoding="utf-8",
    )

    modules, errors = load_all_modules(tmp_path)

    assert modules == []
    assert len(errors) == 1
    assert "a1" in errors[0]


def test_m01_actions_yaml_loads():
    module = load_module(Path(__file__).resolve().parent.parent / "Modules" / "m01_diagnostics" / "actions.yaml")
    assert module.module_id == "m01_diagnostics"
    assert len(module.actions) >= 5
    assert all(a.risk == RiskLevel.SAFE for a in module.actions)


def test_load_module_with_preview_command(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(
        "module_id: m_test\n"
        "actions:\n"
        "  - id: a1\n"
        "    label_sk: \"Akcia 1\"\n"
        "    label_en: \"Action 1\"\n"
        "    risk: SAFE\n"
        "    command: \"Remove-Item foo\"\n"
        "    preview_command: \"Write-Output preview\"\n",
        encoding="utf-8",
    )
    module = load_module(yaml_path)
    assert module.actions[0].preview_command == "Write-Output preview"


def test_load_module_without_preview_command_defaults_to_none(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(VALID_YAML, encoding="utf-8")
    module = load_module(yaml_path)
    assert module.actions[0].preview_command is None


def test_load_module_with_explicit_category(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(
        "module_id: m_test\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: a1\n"
        "    label_sk: \"Akcia 1\"\n"
        "    label_en: \"Action 1\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'hi'\"\n",
        encoding="utf-8",
    )
    module = load_module(yaml_path)
    assert module.category == ModuleCategory.REPAIR


def test_load_module_without_category_defaults_to_diagnostics(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(
        "module_id: m_test\n"
        "actions:\n"
        "  - id: a1\n"
        "    label_sk: \"Akcia 1\"\n"
        "    label_en: \"Action 1\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'hi'\"\n",
        encoding="utf-8",
    )
    module = load_module(yaml_path)
    assert module.category == ModuleCategory.DIAGNOSTICS


def test_load_module_unknown_category_raises(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(
        "module_id: m_test\n"
        "category: BOGUS\n"
        "actions:\n"
        "  - id: a1\n"
        "    label_sk: \"Akcia 1\"\n"
        "    label_en: \"Action 1\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'hi'\"\n",
        encoding="utf-8",
    )
    with pytest.raises(ModuleLoadError):
        load_module(yaml_path)


def test_m01_actions_yaml_has_diagnostics_category():
    module = load_module(Path(__file__).resolve().parent.parent / "Modules" / "m01_diagnostics" / "actions.yaml")
    assert module.category == ModuleCategory.DIAGNOSTICS


def test_load_module_parses_undo_command(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(
        "module_id: m_test\n"
        "actions:\n"
        "  - id: a1\n"
        "    label_sk: \"Akcia 1\"\n"
        "    label_en: \"Action 1\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'hi'\"\n"
        "    undo_command: \"Write-Output 'undo'\"\n",
        encoding="utf-8",
    )
    module = load_module(yaml_path)
    assert module.actions[0].undo_command == "Write-Output 'undo'"


def test_load_module_parses_inactivity_timeout_sec(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(
        "module_id: m_test\n"
        "actions:\n"
        "  - id: a1\n"
        "    label_sk: \"Akcia 1\"\n"
        "    label_en: \"Action 1\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'hi'\"\n"
        "    inactivity_timeout_sec: 2400\n",
        encoding="utf-8",
    )
    module = load_module(yaml_path)
    assert module.actions[0].inactivity_timeout_sec == 2400


def test_load_module_without_inactivity_timeout_sec_defaults_to_none(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(VALID_YAML, encoding="utf-8")
    module = load_module(yaml_path)
    assert module.actions[0].inactivity_timeout_sec is None


def test_load_module_without_undo_command_defaults_to_none(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(
        "module_id: m_test\n"
        "actions:\n"
        "  - id: a1\n"
        "    label_sk: \"Akcia 1\"\n"
        "    label_en: \"Action 1\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'hi'\"\n",
        encoding="utf-8",
    )
    module = load_module(yaml_path)
    assert module.actions[0].undo_command is None
