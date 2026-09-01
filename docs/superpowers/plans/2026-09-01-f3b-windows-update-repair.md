# F3b: Oprava Windows Update (M05) + reálne undo.ps1 kroky — Implementačný plán

> **Pre agentických pracovníkov:** POVINNÁ PODZRUČNOSŤ: Použiť superpowers:subagent-driven-development (odporúčané) alebo superpowers:executing-plans na implementáciu tohto plánu úloha po úlohe. Kroky používajú checkbox (`- [ ]`) syntax na sledovanie.

**Cieľ:** Pridať `ActionDef.undo_command` pole (voliteľný YAML kľúč, rovnaký štýl ako `preview_command`), katalóg M05 (oprava Windows Update — 6 akcií, kategória REPAIR) a mechanizmus progresívneho zapisovania reálnych krokov do `undo.ps1` počas behu dávky — prvý reálny konzument `undo.py` infraštruktúry z F3a.

**Architektúra:** `undo_command: str | None = None` je nové voliteľné pole na `ActionDef` (F3a's `undo.create_undo_script` už má správnu signatúru, nemení sa). `MainWindow` si počas dávky drží `self._undo_steps: list[str]` — po úspešnom dokončení akcie (`exit_code == 0`, nie dry-run) s vyplneným `undo_command` sa krok pridá do zoznamu a `undo.create_undo_script` sa zavolá znova s aktuálnym kumulatívnym zoznamom (prepíše ten istý súbor). Kategória REPAIR na M05 automaticky spúšťa existujúci F3a Restore Point/undo trigger — žiadna nová trigger logika.

**Tech Stack:** Python 3.12, PySide6, PyYAML, pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-09-01-f3b-windows-update-repair-design.md`

## Globálne obmedzenia

- Súbory sa vytvárajú/upravujú výhradne cez nástroj Write/Edit, nikdy cez PowerShell presmerovanie (Out-File/Set-Content/`>`) — spôsobuje UTF-8 BOM kontamináciu.
- Testy, ktoré reálne spúšťajú `powershell.exe` (GUI testy), sa musia overovať súborovo/po dávkach (`pytest tests/test_X.py -v`, prípadne po jednotlivých test ID pri crashi), nikdy naraz cez `pytest tests/` — známy tranzientný natívny crash (`STATUS_STACK_BUFFER_OVERRUN`) pri veľkom počte súbežných reálnych PowerShell procesov v jednom behu. Toto NIE JE chyba kódu — pri výskyte jednoducho zopakovať beh, prípadne rozdeliť na menšie dávky/jednotlivé testy.
- YAML príkazy obsahujúce vnorené dvojité úvodzovky a spätné lomky (PowerShell reťazcová interpolácia typu `"$env:WINDIR\..."`) sa v YAML zapisujú ako dvojito-úvodzovkovaný reťazec s `\"` a `\\` escapovaním — rovnaký overený vzor ako `appx_reregister` v M04 katalógu (F3a), potvrdený ako platný `yaml.safe_load` výstup.
- Nové YAML polia sú vždy voliteľné so spätne kompatibilným defaultom (`undo_command` chýba → `None`).
- Poradie akcií v M05 YAML katalógu = poradie spustenia — `wu_stop_services` musí byť pred `wu_reset_cache`, `wu_reset_cache` pred `wu_restart_services` (poradie v tabuľke nižšie je záväzné).
- Aktuálny stav pred touto fázou: 90 testov (`pytest tests/ --collect-only -q` potvrdené na `main` po zmergovaní F3a). Očakávaný počet po každej úlohe je uvedený v danej úlohe.

---

### Task 1: `ActionDef.undo_command` pole + parsovanie v `module_engine`

**Súbory:**
- Modify: `portablefix/models.py:19-28` (pridáva sa pole `undo_command`)
- Modify: `portablefix/module_engine.py:35-46` (pridáva sa parsovanie)
- Test: `tests/test_module_engine.py`

**Rozhrania:**
- Produkuje: `ActionDef.undo_command: str | None = None`; `module_engine.load_module` nastaví `undo_command` z voliteľného YAML kľúča `undo_command:` na úrovni akcie (chýba → `None`).
- Spotrebúva: nič (základná úloha).

- [ ] **Krok 1: Napísať zlyhávajúce testy**

```python
# pridať do tests/test_module_engine.py
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
```

- [ ] **Krok 2: Spustiť testy a overiť zlyhanie**

Run: `pytest tests/test_module_engine.py -v`
Expected: nové 2 testy FAIL (`AttributeError: 'ActionDef' object has no attribute 'undo_command'`)

- [ ] **Krok 3: Pridať pole `undo_command` do `ActionDef` v `portablefix/models.py`**

Modify (za `preview_command: str | None = None`):
```python
@dataclass
class ActionDef:
    id: str
    label_sk: str
    label_en: str
    risk: RiskLevel
    command: str
    description_sk: str = ""
    description_en: str = ""
    preview_command: str | None = None
    undo_command: str | None = None

    def label(self, language: str) -> str:
        return self.label_en if language == "en" else self.label_sk

    def description(self, language: str) -> str:
        return self.description_en if language == "en" else self.description_sk
```

- [ ] **Krok 4: Pridať parsovanie `undo_command:` v `module_engine.py`**

Modify (v `load_module`, vnútri `for raw in data.get("actions", []):` cyklu):
```python
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
```

- [ ] **Krok 5: Spustiť testy a overiť úspech**

Run: `pytest tests/test_module_engine.py -v`
Expected: PASS, celkovo 92 testov v projekte.

- [ ] **Krok 6: Commit**

```bash
git add portablefix/models.py portablefix/module_engine.py tests/test_module_engine.py
git commit -m "feat: add undo_command field to ActionDef"
```

---

### Task 2: M05 katalóg — oprava Windows Update (6 akcií)

**Súbory:**
- Create: `Modules/m05_windows_update/actions.yaml`
- Test: `tests/test_m05_catalog.py`

**Rozhrania:**
- Konzumuje: `ModuleCategory`, `RiskLevel`, `load_module` (existujúce), `ActionDef.undo_command` z Task 1.
- Produkuje: katalóg `m05_windows_update` s kategóriou `REPAIR`, 6 akciami v presnom poradí nižšie — Task 3 (GUI) sa spolieha na to, že `wu_stop_services` a `wu_reset_cache` majú neprázdny `undo_command`, ostatné 4 akcie ho nemajú.

- [ ] **Krok 1: Napísať zlyhávajúce testy**

```python
# tests/test_m05_catalog.py
from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m05_windows_update" / "actions.yaml"


def test_m05_catalog_loads_6_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m05_windows_update"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 6


def test_m05_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 3
    assert len(by_risk[RiskLevel.MODERATE]) == 3
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m05_catalog_stop_services_before_reset_cache_before_restart_services():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert ids.index("wu_stop_services") < ids.index("wu_reset_cache")
    assert ids.index("wu_reset_cache") < ids.index("wu_restart_services")


def test_m05_catalog_undo_commands_present_only_on_stop_services_and_reset_cache():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["wu_stop_services"].undo_command is not None
    assert by_id["wu_reset_cache"].undo_command is not None
    assert by_id["wu_check_services"].undo_command is None
    assert by_id["wu_restart_services"].undo_command is None
    assert by_id["wu_reregister_dlls"].undo_command is None
    assert by_id["wu_trigger_detection"].undo_command is None
```

- [ ] **Krok 2: Spustiť testy a overiť zlyhanie**

Run: `pytest tests/test_m05_catalog.py -v`
Expected: FAIL (súbor `Modules/m05_windows_update/actions.yaml` neexistuje)

- [ ] **Krok 3: Vytvoriť `Modules/m05_windows_update/actions.yaml`**

```yaml
module_id: m05_windows_update
category: REPAIR
actions:
  - id: wu_check_services
    label_sk: "Kontrola stavu sluzieb Windows Update"
    label_en: "Check Windows Update service status"
    risk: SAFE
    command: "Get-Service -Name wuauserv,bits,cryptsvc,msiserver | Select-Object Name,Status,StartType | Format-Table -AutoSize"
    description_sk: "Zobrazi aktualny stav sluzieb potrebnych pre Windows Update, nic nemeni."
    description_en: "Shows the current status of services required by Windows Update, changes nothing."
  - id: wu_stop_services
    label_sk: "Zastavenie sluzieb Windows Update"
    label_en: "Stop Windows Update services"
    risk: MODERATE
    command: "Stop-Service -Name wuauserv,bits,cryptsvc,msiserver -Force -ErrorAction SilentlyContinue"
    undo_command: "Start-Service -Name wuauserv,bits,cryptsvc,msiserver -ErrorAction SilentlyContinue"
    description_sk: "Docasne zastavi sluzby Windows Update pred resetom cache. Potrebne pred dalsim krokom."
    description_en: "Temporarily stops Windows Update services before resetting the cache. Required before the next step."
  - id: wu_reset_cache
    label_sk: "Reset cache Windows Update"
    label_en: "Reset Windows Update cache"
    risk: MODERATE
    command: "if (Test-Path \"$env:WINDIR\\SoftwareDistribution\") { Rename-Item -Path \"$env:WINDIR\\SoftwareDistribution\" -NewName \"SoftwareDistribution.bak\" -Force -ErrorAction SilentlyContinue }; if (Test-Path \"$env:WINDIR\\System32\\catroot2\") { Rename-Item -Path \"$env:WINDIR\\System32\\catroot2\" -NewName \"catroot2.bak\" -Force -ErrorAction SilentlyContinue }"
    undo_command: "if ((Test-Path \"$env:WINDIR\\SoftwareDistribution.bak\") -and -not (Test-Path \"$env:WINDIR\\SoftwareDistribution\")) { Rename-Item -Path \"$env:WINDIR\\SoftwareDistribution.bak\" -NewName \"SoftwareDistribution\" -Force }; if ((Test-Path \"$env:WINDIR\\System32\\catroot2.bak\") -and -not (Test-Path \"$env:WINDIR\\System32\\catroot2\")) { Rename-Item -Path \"$env:WINDIR\\System32\\catroot2.bak\" -NewName \"catroot2\" -Force }"
    description_sk: "Premenuje priecinky SoftwareDistribution a catroot2 na .bak, Windows si vytvori nove pri dalsom starte sluzieb. Undo funguje spolahlivo len ak sa medzitym nespustil krok Restart sluzieb Windows Update."
    description_en: "Renames the SoftwareDistribution and catroot2 folders to .bak; Windows recreates them fresh when services restart. Undo only works reliably if the Restart Windows Update services step hasn't run yet."
  - id: wu_restart_services
    label_sk: "Restart sluzieb Windows Update"
    label_en: "Restart Windows Update services"
    risk: SAFE
    command: "Start-Service -Name wuauserv,bits,cryptsvc,msiserver -ErrorAction SilentlyContinue"
    description_sk: "Nastartuje sluzby Windows Update naspat po resete cache."
    description_en: "Starts Windows Update services back up after the cache reset."
  - id: wu_reregister_dlls
    label_sk: "Znovu-registracia DLL kniznic Windows Update"
    label_en: "Re-register Windows Update DLL libraries"
    risk: MODERATE
    command: "$dlls = @('atl.dll','urlmon.dll','mshtml.dll','shdocvw.dll','browseui.dll','jscript.dll','vbscript.dll','scrrun.dll','msxml.dll','msxml3.dll','msxml6.dll','actxprxy.dll','softpub.dll','wintrust.dll','dssenh.dll','rsaenh.dll','gpkcsp.dll','sccbase.dll','slbcsp.dll','cryptdlg.dll','oleaut32.dll','ole32.dll','shell32.dll','initpki.dll','wuapi.dll','wuaueng.dll','wucltui.dll','wups.dll','wups2.dll','wuwebv.dll'); foreach ($dll in $dlls) { regsvr32.exe /s \"$env:WINDIR\\System32\\$dll\" }"
    description_sk: "Znovu zaregistruje systemove DLL suvisiace s Windows Update. Casto nie je potrebne na modernom Windows, pomaha pri starsich/poskodenych instalaciach."
    description_en: "Re-registers system DLLs related to Windows Update. Often unnecessary on modern Windows, helps with older/corrupted installations."
  - id: wu_trigger_detection
    label_sk: "Spustenie detekcie aktualizacii"
    label_en: "Trigger update detection"
    risk: SAFE
    command: "UsoClient.exe StartScan"
    description_sk: "Spusti kontrolu dostupnych aktualizacii na pozadi."
    description_en: "Starts a background scan for available updates."
```

- [ ] **Krok 4: Spustiť testy a overiť úspech**

Run: `pytest tests/test_m05_catalog.py -v`
Expected: PASS, celkovo 96 testov v projekte.

- [ ] **Krok 5: Commit**

```bash
git add Modules/m05_windows_update/actions.yaml tests/test_m05_catalog.py
git commit -m "feat: add M05 Windows Update repair catalog"
```

---

### Task 3: Progresívne zapisovanie undo krokov v `MainWindow`

**Súbory:**
- Modify: `portablefix/gui/main_window.py` (`__init__` — pridáva sa `self._undo_steps`; `run_selected_actions` — reset `_undo_steps`; `_on_action_finished` — akumulácia a prepisovanie `undo.ps1`)
- Test: `tests/test_gui_main_window.py`

**Rozhrania:**
- Konzumuje: `ActionDef.undo_command` z Task 1, `undo.create_undo_script(base_dir, run_id, steps=None)` (nezmenené z F3a), M05 katalóg z Task 2 (nepriamo, cez testovacie fixtúry s vlastným YAML).
- Produkuje: `MainWindow._undo_steps: list[str]` — inštančný atribút, resetovaný na začiatku každej dávky, akumuluje `undo_command` úspešných akcií v poradí ich dokončenia.

- [ ] **Krok 1: Napísať zlyhávajúce testy**

```python
# pridať do tests/test_gui_main_window.py
def test_successful_actions_with_undo_command_accumulate_in_undo_script(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: True)

    module_dir = tmp_path / "Modules" / "m05_windows_update"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m05_windows_update\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: step_one\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'one'\"\n"
        "    undo_command: \"Write-Output 'undo-one'\"\n"
        "  - id: step_two\n"
        "    label_sk: \"Y\"\n"
        "    label_en: \"Y\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'two'\"\n"
        "    undo_command: \"Write-Output 'undo-two'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_undo_accum")
    qtbot.addWidget(window)
    window._action_checkboxes["step_one"].setChecked(True)
    window._action_checkboxes["step_two"].setChecked(True)

    window.run_selected_actions()

    reports_dir = tmp_path / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists(), timeout=10000)
    undo_content = (tmp_path / "Backups" / "run_undo_accum" / "undo.ps1").read_text(encoding="utf-8")
    assert "Write-Output 'undo-one'" in undo_content
    assert "Write-Output 'undo-two'" in undo_content
    assert undo_content.index("Write-Output 'undo-one'") < undo_content.index("Write-Output 'undo-two'")


def test_failed_action_with_undo_command_not_added_to_undo_script(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: True)

    module_dir = tmp_path / "Modules" / "m05_windows_update"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m05_windows_update\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: failing_step\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"exit 1\"\n"
        "    undo_command: \"Write-Output 'should-not-appear'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_undo_fail")
    qtbot.addWidget(window)
    window._action_checkboxes["failing_step"].setChecked(True)

    window.run_selected_actions()

    reports_dir = tmp_path / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists(), timeout=10000)
    undo_content = (tmp_path / "Backups" / "run_undo_fail" / "undo.ps1").read_text(encoding="utf-8")
    assert "should-not-appear" not in undo_content


def test_dry_run_action_with_undo_command_never_creates_backups_dir(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    def fail_if_called(description):
        raise AssertionError("create_restore_point must not be called in dry-run")

    monkeypatch.setattr(restore_point, "create_restore_point", fail_if_called)

    module_dir = tmp_path / "Modules" / "m05_windows_update"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m05_windows_update\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: step_one\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'one'\"\n"
        "    undo_command: \"Write-Output 'undo-one'\"\n"
        "    preview_command: \"Write-Output 'preview'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_undo_dry")
    qtbot.addWidget(window)
    window._action_checkboxes["step_one"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: "preview" in window.console.toPlainText(), timeout=10000)
    assert not (tmp_path / "Backups").exists()


def test_undo_steps_reset_between_batches(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: True)

    module_dir = tmp_path / "Modules" / "m05_windows_update"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m05_windows_update\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: step_one\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'one'\"\n"
        "    undo_command: \"Write-Output 'undo-one'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_undo_reset")
    qtbot.addWidget(window)
    window._action_checkboxes["step_one"].setChecked(True)
    window.run_selected_actions()
    reports_dir = tmp_path / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists(), timeout=10000)

    assert window._undo_steps == ["Write-Output 'undo-one'"]
    window._action_checkboxes["step_one"].setChecked(False)
    window.run_selected_actions()
    assert window._undo_steps == []
```

- [ ] **Krok 2: Spustiť testy a overiť zlyhanie**

Run: `pytest tests/test_gui_main_window.py -v`
Expected: nové 4 testy FAIL (`AttributeError: 'MainWindow' object has no attribute '_undo_steps'`)

- [ ] **Krok 3: Pridať `self._undo_steps` do `__init__`**

Modify (za `self._snapshot_before: dict = {}`):
```python
        self._snapshot_before: dict = {}
        self._undo_steps: list[str] = []
```

- [ ] **Krok 4: Resetovať `_undo_steps` v `run_selected_actions`**

Modify:
```python
    def run_selected_actions(self) -> None:
        self._queue = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
        self._restore_point_attempted = False
        self._undo_steps = []
        if self._queue:
            self._batch_active = True
            self._snapshot_before = self._take_snapshot()
            self.run_button.setEnabled(False)
        self._run_next()
```

- [ ] **Krok 5: Akumulovať a prepisovať `undo.ps1` v `_on_action_finished`**

Modify:
```python
    def _on_action_finished(
        self, module_id: str, action_id: str, command: str, exit_code: int, runner: ActionRunner
    ) -> None:
        output = "\n".join(runner.captured_output)
        entry = make_entry(module_id, action_id, command, exit_code, output, self.settings.dry_run)
        append_entry(self.state_dir, self.run_id, entry)
        if not self.settings.dry_run and exit_code == 0:
            _, action = self._find_action(action_id)
            if action.undo_command:
                self._undo_steps.append(action.undo_command)
                undo.create_undo_script(self.state_dir, self.run_id, steps=self._undo_steps)
        self._run_next()
```

- [ ] **Krok 6: Spustiť testy a overiť úspech**

Run: `pytest tests/test_gui_main_window.py -v`
Expected: PASS, celkovo 100 testov v projekte. Pri natívnom crashi (`STATUS_STACK_BUFFER_OVERRUN`) zopakovať beh, prípadne rozdeliť na menšie dávky/jednotlivé testy — známy environmentálny jav, nie chyba kódu.

- [ ] **Krok 7: Commit**

```bash
git add portablefix/gui/main_window.py tests/test_gui_main_window.py
git commit -m "feat: progressively record real undo steps into undo.ps1 during a batch"
```

---

## Štruktúra súborov po dokončení F3b

```
portablefix/
  models.py            (+ ActionDef.undo_command)
  module_engine.py      (+ parsovanie undo_command:)
  gui/main_window.py    (+ self._undo_steps; reset v run_selected_actions; akumulácia v _on_action_finished)
Modules/
  m05_windows_update/actions.yaml  (nový, 6 akcií, category: REPAIR)
tests/
  test_module_engine.py  (+ 2 testy: undo_command parsovanie)
  test_m05_catalog.py     (nový, 4 testy)
  test_gui_main_window.py (+ 4 testy: akumulácia, zlyhanie nezapíše krok, dry-run nezapíše nič, reset medzi dávkami)
```

Celkový počet testov po F3b: 90 (základ) + 2 (Task 1) + 4 (Task 2) + 4 (Task 3) = **100**.

## Self-Review

**1. Pokrytie spec:**
- `ActionDef.undo_command` pole + voliteľné YAML parsovanie → Task 1. ✅
- M05 katalóg — 6 akcií, presné poradie, risk úrovne, `undo_command` len na `wu_stop_services`/`wu_reset_cache` → Task 2. ✅
- Progresívne zapisovanie undo krokov (nie raz na konci dávky, ale po každej úspešnej akcii s `undo_command`) → Task 3. ✅
- Dry-run nikdy nezapisuje reálne kroky ani nevytvára `Backups/` → Task 3 (test `test_dry_run_action_with_undo_command_never_creates_backups_dir`, opiera sa o existujúcu F3a podmienku `not self.settings.dry_run` v `_run_next`, ktorá zostáva nezmenená). ✅
- Zlyhaná akcia (nenulový exit_code) sa do `undo.ps1` nezapíše → Task 3 (test `test_failed_action_with_undo_command_not_added_to_undo_script`). ✅
- Kategória REPAIR na M05 automaticky spúšťa Restore Point/undo trigger → zabezpečené existujúcou F3a logikou v `_run_next` (nemenená), bez potreby novej úlohy. ✅
- Reset `_undo_steps` medzi dávkami → Task 3 (test `test_undo_steps_reset_between_batches`). ✅
- Mimo rozsahu (M03, M06, zmeny `report.py`) — nemajú tasky, čo je správne. ✅

**2. Kontrola placeholderov:** Žiadne "TBD"/"implementovať neskôr" nájdené — všetky kroky obsahujú plný kód alebo presné YAML.

**3. Konzistencia typov/rozhraní:**
- `ActionDef.undo_command: str | None = None` — rovnaký typ v Task 1 (definícia) a Task 3 (`if action.undo_command:` — `None`/falsy string obe fungujú ako "nič nerobiť"). ✅
- `undo.create_undo_script(base_dir, run_id, steps=None)` — signatúra nezmenená z F3a, Task 3 ju volá s `steps=self._undo_steps` (vždy list, aj prázdny na začiatku), čo zodpovedá existujúcej implementácii (`steps = steps or []`). ✅
- Testovacie YAML fixtúry v Task 3 používajú `category: REPAIR`, čo je jediná cesta, ako v teste spustiť F3a-ov Restore Point trigger bez potreby skutočného M05 katalógu zo súborového systému — konzistentné s F3a-ovým testovacím vzorom (`_write_module`-štýl inline YAML). ✅
- `wu_reset_cache`'s `undo_command` v M05 katalógu (Task 2) je nezávislý od `MainWindow`'s progresívneho mechanizmu (Task 3) — Task 2 je testovateľná samostatne (`load_module` parsuje `undo_command` správne), Task 3 testuje mechanizmus na vlastných minimálnych fixtúrach, nie na reálnom M05 katalógu — žiadna krížová závislosť medzi úlohami okrem toho, že Task 3 potrebuje `ActionDef.undo_command` z Task 1 (existovať musí, obsah nie). ✅

Žiadne medzery nájdené, plán je pripravený na implementáciu.
