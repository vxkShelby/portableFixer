# F3a: Oprava integrity systému (M04) + undo.ps1 infra — Implementačný plán

> **Pre agentických pracovníkov:** POVINNÁ PODZRUČNOSŤ: Použiť superpowers:subagent-driven-development (odporúčané) alebo superpowers:executing-plans na implementáciu tohto plánu úloha po úlohe. Kroky používajú checkbox (`- [ ]`) syntax na sledovanie.

**Cieľ:** Pridať kategórie modulov (`ModuleCategory`), katalóg M04 (kontrola integrity systému — DISM/SFC/AppX/WMI), generický `undo.ps1` mechanizmus a rozšíriť Restore Point/undo spúšťač o kategóriu REPAIR/SECURITY. Opraviť GUI zoznam kategórií, aby zobrazoval reálne kategórie namiesto duplicitného plochého zoznamu.

**Architektúra:** `ModuleCategory` je nový `str`-`Enum` v `models.py`, pridaný ako voliteľné pole `ModuleDef.category` (default `DIAGNOSTICS`, spätne kompatibilné). `module_engine.load_module` parsuje voliteľný `category:` YAML kľúč na úrovni modulu. `MainWindow._run_next` rozširuje existujúcu DESTRUCTIVE-risk podmienku o kategóriu modulu. Nový `portablefix/undo.py` modul zapisuje `Backups/<run_id>/undo.ps1` (zatiaľ len hlavička, žiadne reálne kroky — pripravené pre M05/M06). GUI zoskupuje akcie pod nadpisy kategórií namiesto plochého zoznamu.

**Tech Stack:** Python 3.12, PySide6, PyYAML, pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-08-31-f3a-integrity-repair-design.md`

## Globálne obmedzenia

- Súbory sa vytvárajú/upravujú výhradne cez nástroj Write/Edit, nikdy cez PowerShell presmerovanie (Out-File/Set-Content/`>`) — spôsobuje UTF-8 BOM kontamináciu, ktorá rozbíja `tomllib`/YAML parsovanie. Toto je trvalé pravidlo z F1.
- Testy, ktoré reálne spúšťajú `powershell.exe` (GUI testy s `ActionRunner`/`RestorePointRunner`), sa musia overovať súborovo (per-file `pytest tests/test_X.py -v`), nikdy naraz cez `pytest tests/` — známy tranzientný natívny crash (`STATUS_STACK_BUFFER_OVERRUN`) pri veľkom počte súbežných reálnych PowerShell procesov v jednom behu. Toto NIE JE chyba kódu — pri výskyte jednoducho zopakovať beh daného súboru.
- Nové YAML kľúče/polia sú vždy voliteľné so spätne kompatibilným defaultom — existujúce katalógy (M01/M02) sa upravujú len pridaním jedného riadka (`category:`), nič iné sa nemení.
- Poradie akcií v M04 YAML katalógu = poradie spustenia (front sa stavia iteráciou v poradí YAML, filtrovanou na zaškrtnuté položky) — poradie v tabuľke nižšie je záväzné.
- Žiadna z M04 akcií nemá `preview_command` — SFC/DISM nemajú zmysluplný read-only náhľad, dry-run zostáva pri F1 správaní (`[DRY-RUN] <príkaz>` bez spustenia).
- Aktuálny stav pred touto fázou: 70 testov (`pytest tests/ --collect-only -q` potvrdené). Očakávaný počet po každej úlohe je uvedený v danej úlohe — počítaný voči tomuto základu.

---

### Task 1: `ModuleCategory` enum + `ModuleDef.category` pole + parsovanie v `module_engine`

**Súbory:**
- Modify: `portablefix/models.py:1-34` (celý súbor, pridáva sa enum a pole)
- Modify: `portablefix/module_engine.py:1-46` (celý súbor, pridáva sa parsovanie)
- Test: `tests/test_module_engine.py`

**Rozhrania:**
- Produkuje: `ModuleCategory(str, Enum)` s hodnotami `DIAGNOSTICS`, `CLEANUP`, `REPAIR`, `SECURITY`; `ModuleDef.category: ModuleCategory = ModuleCategory.DIAGNOSTICS`; `module_engine.load_module` nastaví `category` z voliteľného YAML kľúča `category:` (chýba → `DIAGNOSTICS`, neznáma hodnota → `ModuleLoadError`).
- Spotrebúva: nič (základná úloha).

- [ ] **Krok 1: Napísať zlyhávajúce testy**

```python
# pridať do tests/test_module_engine.py (za existujúce testy)
from portablefix.models import ModuleCategory


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
```

- [ ] **Krok 2: Spustiť testy a overiť zlyhanie**

Run: `pytest tests/test_module_engine.py -v`
Expected: nové 3 testy FAIL (`AttributeError: 'ModuleDef' object has no attribute 'category'` alebo `ImportError: cannot import name 'ModuleCategory'`)

- [ ] **Krok 3: Implementovať `ModuleCategory` a pole `category` v `models.py`**

Celý nový obsah `portablefix/models.py`:

```python
from dataclasses import dataclass
from enum import Enum


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    MODERATE = "MODERATE"
    DESTRUCTIVE = "DESTRUCTIVE"
    REQUIRES_REBOOT = "REQUIRES_REBOOT"


class ModuleCategory(str, Enum):
    DIAGNOSTICS = "DIAGNOSTICS"
    CLEANUP = "CLEANUP"
    REPAIR = "REPAIR"
    SECURITY = "SECURITY"


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

    def label(self, language: str) -> str:
        return self.label_en if language == "en" else self.label_sk

    def description(self, language: str) -> str:
        return self.description_en if language == "en" else self.description_sk


@dataclass
class ModuleDef:
    module_id: str
    actions: list[ActionDef]
    category: ModuleCategory = ModuleCategory.DIAGNOSTICS
```

- [ ] **Krok 4: Implementovať parsovanie `category:` v `module_engine.py`**

Celý nový obsah `portablefix/module_engine.py`:

```python
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
            )
        )
    return ModuleDef(module_id=module_id, actions=actions, category=category)


def load_all_modules(modules_dir: Path) -> list[ModuleDef]:
    return [load_module(p) for p in sorted(modules_dir.glob("*/actions.yaml"))]
```

- [ ] **Krok 5: Spustiť testy a overiť úspech**

Run: `pytest tests/test_module_engine.py -v`
Expected: PASS, spolu 11 testov v tomto súbore (8 pôvodných + 3 nové), celkovo 73 testov v projekte.

- [ ] **Krok 6: Commit**

```bash
git add portablefix/models.py portablefix/module_engine.py tests/test_module_engine.py
git commit -m "feat: add ModuleCategory enum and category field to ModuleDef"
```

---

### Task 2: Pridať `category:` do existujúcich katalógov M01/M02

**Súbory:**
- Modify: `Modules/m01_diagnostics/actions.yaml` (pridať riadok `category: DIAGNOSTICS` hneď za `module_id:`)
- Modify: `Modules/m02_cleanup/actions.yaml` (pridať riadok `category: CLEANUP` hneď za `module_id:`)
- Test: `tests/test_module_engine.py`, `tests/test_m02_catalog.py`

**Rozhrania:**
- Konzumuje: `ModuleCategory` z Task 1, `load_module` z Task 1.
- Produkuje: nič nové (dopĺňa existujúce katalógy o metadáta).

- [ ] **Krok 1: Napísať zlyhávajúce testy**

```python
# pridať do tests/test_module_engine.py
from pathlib import Path


def test_m01_actions_yaml_has_diagnostics_category():
    module = load_module(Path(__file__).resolve().parent.parent / "Modules" / "m01_diagnostics" / "actions.yaml")
    assert module.category == ModuleCategory.DIAGNOSTICS
```

```python
# pridať do tests/test_m02_catalog.py (rovnaký CATALOG_PATH ako existujúce testy v tomto súbore)
def test_m02_catalog_has_cleanup_category():
    module = load_module(CATALOG_PATH)
    assert module.category == ModuleCategory.CLEANUP
```

- [ ] **Krok 2: Spustiť testy a overiť zlyhanie**

Run: `pytest tests/test_module_engine.py tests/test_m02_catalog.py -v`
Expected: nové 2 testy FAIL (`category == ModuleCategory.DIAGNOSTICS` (default) namiesto explicitne nastaveného — pozor, m01 bez zmeny YAML by prešiel náhodou vďaka defaultu; toto je dôvod, prečo krok 2 tu neoveruje FAIL na m01 testom priamo, ale na to, že YAML **nemá** explicitný riadok — over vizuálne pred krokom 3, že `category:` v súbore chýba)

- [ ] **Krok 3: Pridať `category: DIAGNOSTICS` do `Modules/m01_diagnostics/actions.yaml`**

Vlož riadok `category: DIAGNOSTICS` hneď po riadku `module_id: m01_diagnostics` (Edit s `old_string="module_id: m01_diagnostics\n"`, `new_string="module_id: m01_diagnostics\ncategory: DIAGNOSTICS\n"`).

- [ ] **Krok 4: Pridať `category: CLEANUP` do `Modules/m02_cleanup/actions.yaml`**

Vlož riadok `category: CLEANUP` hneď po riadku `module_id: m02_cleanup` (rovnaký Edit vzor).

- [ ] **Krok 5: Spustiť testy a overiť úspech**

Run: `pytest tests/test_module_engine.py tests/test_m02_catalog.py -v`
Expected: PASS, celkovo 75 testov v projekte.

- [ ] **Krok 6: Commit**

```bash
git add Modules/m01_diagnostics/actions.yaml Modules/m02_cleanup/actions.yaml tests/test_module_engine.py tests/test_m02_catalog.py
git commit -m "feat: tag M01/M02 catalogs with explicit module category"
```

---

### Task 3: `portablefix/undo.py` — generický undo.ps1 mechanizmus

**Súbory:**
- Create: `portablefix/undo.py`
- Test: `tests/test_undo.py`

**Rozhrania:**
- Produkuje: `create_undo_script(base_dir: Path, run_id: str, steps: list[str] | None = None) -> Path` — zapíše `<base_dir>/Backups/<run_id>/undo.ps1`, vráti cestu k súboru. Bez krokov zapíše len informačnú hlavičku.
- Konzumuje: nič (samostatný modul).

- [ ] **Krok 1: Napísať zlyhávajúce testy**

```python
# tests/test_undo.py
from pathlib import Path

from portablefix.undo import create_undo_script


def test_create_undo_script_with_no_steps_writes_header_only(tmp_path):
    path = create_undo_script(tmp_path, "run1")
    assert path == tmp_path / "Backups" / "run1" / "undo.ps1"
    content = path.read_text(encoding="utf-8")
    assert "run1" in content
    assert "No reversible changes" in content


def test_create_undo_script_with_steps_includes_them(tmp_path):
    path = create_undo_script(tmp_path, "run2", steps=["Set-ItemProperty -Path X -Name Y -Value Z"])
    content = path.read_text(encoding="utf-8")
    assert "Set-ItemProperty -Path X -Name Y -Value Z" in content


def test_create_undo_script_creates_backups_dir(tmp_path):
    create_undo_script(tmp_path, "run3")
    assert (tmp_path / "Backups" / "run3").is_dir()
```

- [ ] **Krok 2: Spustiť testy a overiť zlyhanie**

Run: `pytest tests/test_undo.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'portablefix.undo'`

- [ ] **Krok 3: Implementovať `portablefix/undo.py`**

```python
from datetime import datetime, timezone
from pathlib import Path


def create_undo_script(base_dir: Path, run_id: str, steps: list[str] | None = None) -> Path:
    steps = steps or []
    lines = [
        "# PortableFix undo script",
        f"# run_id: {run_id}",
        f"# generated: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    if steps:
        lines.extend(steps)
    else:
        lines.append("# No reversible changes were made in this run.")

    path = base_dir / "Backups" / run_id / "undo.ps1"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
```

- [ ] **Krok 4: Spustiť testy a overiť úspech**

Run: `pytest tests/test_undo.py -v`
Expected: PASS, celkovo 78 testov v projekte.

- [ ] **Krok 5: Commit**

```bash
git add portablefix/undo.py tests/test_undo.py
git commit -m "feat: add generic undo.ps1 script generator"
```

---

### Task 4: M04 katalóg — kontrola integrity systému (8 akcií)

**Súbory:**
- Create: `Modules/m04_integrity/actions.yaml`
- Test: `tests/test_m04_catalog.py`

**Rozhrania:**
- Konzumuje: `ModuleCategory`, `RiskLevel`, `load_module` z Task 1.
- Produkuje: katalóg `m04_integrity` s kategóriou `REPAIR`, 8 akciami v presnom poradí nižšie — GUI úloha (Task 6) sa spolieha na `dism_restorehealth` a `sfc_scannow` id na overenie behovej podmienky Restore Point.

- [ ] **Krok 1: Napísať zlyhávajúce testy**

```python
# tests/test_m04_catalog.py
from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m04_integrity" / "actions.yaml"


def test_m04_catalog_loads_8_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m04_integrity"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 8


def test_m04_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 4
    assert len(by_risk[RiskLevel.MODERATE]) == 2
    assert len(by_risk[RiskLevel.DESTRUCTIVE]) == 1
    assert len(by_risk[RiskLevel.REQUIRES_REBOOT]) == 1


def test_m04_catalog_dism_restorehealth_before_sfc_scannow():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert ids.index("dism_restorehealth") < ids.index("sfc_scannow")
    assert ids.index("sfc_scannow") < ids.index("sfc_verifyonly")


def test_m04_catalog_no_preview_command_set():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.preview_command is None
```

- [ ] **Krok 2: Spustiť testy a overiť zlyhanie**

Run: `pytest tests/test_m04_catalog.py -v`
Expected: FAIL (súbor `Modules/m04_integrity/actions.yaml` neexistuje)

- [ ] **Krok 3: Vytvoriť `Modules/m04_integrity/actions.yaml`**

```yaml
module_id: m04_integrity
category: REPAIR
actions:
  - id: dism_checkhealth
    label_sk: "Rychla kontrola uloziska komponentov"
    label_en: "Quick component store check"
    risk: SAFE
    command: "DISM /Online /Cleanup-Image /CheckHealth"
    description_sk: "Rychlo overi, ci je uloziste komponentov oznacene ako poskodene."
    description_en: "Quickly checks whether the component store is flagged as corrupted."
  - id: dism_scanhealth
    label_sk: "Hlbkova kontrola uloziska komponentov"
    label_en: "Deep component store scan"
    risk: SAFE
    command: "DISM /Online /Cleanup-Image /ScanHealth"
    description_sk: "Dokladnejsie skenovanie uloziska komponentov (dlhsie trva)."
    description_en: "More thorough component store scan (takes longer)."
  - id: dism_restorehealth
    label_sk: "Oprava uloziska komponentov"
    label_en: "Repair component store"
    risk: REQUIRES_REBOOT
    command: "DISM /Online /Cleanup-Image /RestoreHealth"
    description_sk: "Opravi uloziste komponentov cez Windows Update. Po dokonceni sa odporuca restart pred spustenim sfc /scannow."
    description_en: "Repairs the component store via Windows Update. A restart is recommended before running sfc /scannow afterward."
  - id: sfc_scannow
    label_sk: "System File Checker (oprava)"
    label_en: "System File Checker (repair)"
    risk: MODERATE
    command: "sfc /scannow"
    description_sk: "Skontroluje a opravi chranene systemove subory."
    description_en: "Scans and repairs protected system files."
  - id: sfc_verifyonly
    label_sk: "System File Checker (len overenie)"
    label_en: "System File Checker (verify only)"
    risk: SAFE
    command: "sfc /verifyonly"
    description_sk: "Len overi integritu systemovych suborov, nic nemeni."
    description_en: "Only verifies system file integrity, changes nothing."
  - id: appx_reregister
    label_sk: "Re-registracia Windows Store aplikacii"
    label_en: "Re-register Windows Store apps"
    risk: MODERATE
    command: "Get-AppXPackage -AllUsers | Foreach {Add-AppxPackage -DisableDevelopmentMode -Register \"$($_.InstallLocation)\\AppXManifest.xml\"}"
    description_sk: "Znova zaregistruje vsetky nainstalovane UWP aplikacie pre vsetkych pouzivatelov."
    description_en: "Re-registers all installed UWP apps for all users."
  - id: wmi_verify
    label_sk: "Overenie WMI repozitara"
    label_en: "Verify WMI repository"
    risk: SAFE
    command: "winmgmt /verifyrepository"
    description_sk: "Skontroluje konzistenciu WMI repozitara bez zmien."
    description_en: "Checks WMI repository consistency without making changes."
  - id: wmi_salvage
    label_sk: "Zachranenie WMI repozitara"
    label_en: "Salvage WMI repository"
    risk: DESTRUCTIVE
    command: "winmgmt /salvagerepository"
    description_sk: "Pokusi sa opravit poskodeny WMI repozitar. Robit len po zalohe, moze stratit vlastne WMI rozsirenia."
    description_en: "Attempts to repair a corrupted WMI repository. Only after a backup - may lose custom WMI extensions."
```

- [ ] **Krok 4: Spustiť testy a overiť úspech**

Run: `pytest tests/test_m04_catalog.py -v`
Expected: PASS, celkovo 82 testov v projekte.

- [ ] **Krok 5: Commit**

```bash
git add Modules/m04_integrity/actions.yaml tests/test_m04_catalog.py
git commit -m "feat: add M04 integrity repair catalog (DISM/SFC/AppX/WMI)"
```

---

### Task 5: Oprava GUI zoznamu kategórií (`main_window.py` — `_build_ui`)

**Súbory:**
- Modify: `portablefix/i18n.py:1-30` (pridať `category_cleanup`, `category_repair`, `category_security` do oboch slovníkov `sk`/`en`)
- Modify: `portablefix/gui/main_window.py` (funkcia `_build_ui`, aktuálne riadky 88-102 — zoznam kategórií a stredný panel)
- Test: `tests/test_gui_main_window.py`

**Rozhrania:**
- Konzumuje: `ModuleDef.category` z Task 1, `Settings`, `MainWindow(assets_dir, state_dir, settings, is_admin, run_id)` konštruktor (nezmenený z F2).
- Produkuje: `window.category_list` (QListWidget) obsahuje jeden riadok na **odlišnú** kategóriu naprieč modulmi (nie na modul); stredný panel zoskupuje checkboxy akcií pod tučný `QLabel` nadpis kategórie.

- [ ] **Krok 1: Napísať zlyhávajúce testy**

```python
# pridať do tests/test_gui_main_window.py
from portablefix.settings import Settings
from portablefix.gui.main_window import MainWindow


def _write_module(base_dir, module_id, category, action_id):
    module_dir = base_dir / "Modules" / module_id
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        f"module_id: {module_id}\n"
        f"category: {category}\n"
        "actions:\n"
        f"  - id: {action_id}\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'x'\"\n",
        encoding="utf-8",
    )


def test_category_list_deduplicates_same_category_across_modules(qtbot, tmp_path):
    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "a1")
    _write_module(tmp_path, "m02_other", "DIAGNOSTICS", "a2")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_cat1")
    qtbot.addWidget(window)
    assert window.category_list.count() == 1
    assert window.category_list.item(0).text() == "Diagnostics"


def test_category_list_shows_distinct_entries_for_different_categories(qtbot, tmp_path):
    _write_module(tmp_path, "m01_diagnostics", "DIAGNOSTICS", "a1")
    _write_module(tmp_path, "m04_integrity", "REPAIR", "a2")
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_cat2")
    qtbot.addWidget(window)
    assert window.category_list.count() == 2
    labels = {window.category_list.item(i).text() for i in range(window.category_list.count())}
    assert labels == {"Diagnostics", "System repair"}
```

- [ ] **Krok 2: Spustiť testy a overiť zlyhanie**

Run: `pytest tests/test_gui_main_window.py -v`
Expected: nové 2 testy FAIL (`category_list.count() == 2` namiesto `1` v prvom teste — aktuálny kód pridáva jeden riadok na modul, nie na kategóriu; druhý test FAIL na `KeyError`/chýbajúci i18n kľúč `category_repair`)

- [ ] **Krok 3: Pridať i18n kľúče do `portablefix/i18n.py`**

V `_STRINGS["sk"]` za riadok `"category_diagnostics": "Diagnostika",` pridať:

```python
        "category_cleanup": "Cistenie",
        "category_repair": "Oprava systemu",
        "category_security": "Zabezpecenie",
```

V `_STRINGS["en"]` za riadok `"category_diagnostics": "Diagnostics",` pridať:

```python
        "category_cleanup": "Cleanup",
        "category_repair": "System repair",
        "category_security": "Security",
```

- [ ] **Krok 4: Upraviť `_build_ui` v `portablefix/gui/main_window.py`**

Pridať import `ModuleCategory` do existujúceho importu z `..models`:

Modify (riadok s importom `ActionDef, ModuleDef, RiskLevel`):
```python
from ..models import ActionDef, ModuleCategory, ModuleDef, RiskLevel
```

Nahradiť existujúci blok (aktuálne riadky 88-102):

```python
        body_layout = QHBoxLayout()
        self.category_list = QListWidget()
        for module in self.modules:
            self.category_list.addItem(QListWidgetItem(self._t("category_diagnostics")))
        body_layout.addWidget(self.category_list, 1)

        center_layout = QVBoxLayout()
        for module in self.modules:
            for action in module.actions:
                checkbox = QCheckBox(f"[{action.risk.value}] {action.label(self.settings.language)}")
                self._action_checkboxes[action.id] = checkbox
                center_layout.addWidget(checkbox)
        self.run_button = QPushButton(self._t("run_selected"))
        self.run_button.clicked.connect(self.run_selected_actions)
        center_layout.addWidget(self.run_button)
        body_layout.addLayout(center_layout, 2)
```

za:

```python
        category_i18n_keys = {
            ModuleCategory.DIAGNOSTICS: "category_diagnostics",
            ModuleCategory.CLEANUP: "category_cleanup",
            ModuleCategory.REPAIR: "category_repair",
            ModuleCategory.SECURITY: "category_security",
        }
        categories_seen: list[ModuleCategory] = []
        for module in self.modules:
            if module.category not in categories_seen:
                categories_seen.append(module.category)

        body_layout = QHBoxLayout()
        self.category_list = QListWidget()
        for category in categories_seen:
            self.category_list.addItem(QListWidgetItem(self._t(category_i18n_keys[category])))
        body_layout.addWidget(self.category_list, 1)

        center_layout = QVBoxLayout()
        for category in categories_seen:
            category_label = QLabel(self._t(category_i18n_keys[category]))
            category_label.setStyleSheet("font-weight: bold;")
            center_layout.addWidget(category_label)
            for module in self.modules:
                if module.category != category:
                    continue
                for action in module.actions:
                    checkbox = QCheckBox(f"[{action.risk.value}] {action.label(self.settings.language)}")
                    self._action_checkboxes[action.id] = checkbox
                    center_layout.addWidget(checkbox)
        self.run_button = QPushButton(self._t("run_selected"))
        self.run_button.clicked.connect(self.run_selected_actions)
        center_layout.addWidget(self.run_button)
        body_layout.addLayout(center_layout, 2)
```

Ak `QLabel` ešte nie je importovaný z `PySide6.QtWidgets` v hlavičke súboru, pridať ho do existujúceho importného riadku PySide6 widgetov.

- [ ] **Krok 5: Spustiť testy a overiť úspech**

Run: `pytest tests/test_gui_main_window.py -v`
Expected: PASS, celkovo 84 testov v projekte. (Ak dôjde k natívnemu crashu `STATUS_STACK_BUFFER_OVERRUN` počas behu tohto súboru, zopakovať beh — nie je to chyba kódu.)

- [ ] **Krok 6: Commit**

```bash
git add portablefix/i18n.py portablefix/gui/main_window.py tests/test_gui_main_window.py
git commit -m "fix: group GUI actions by real module category instead of flat per-module list"
```

---

### Task 6: Rozšírenie Restore Point/undo spúšťača o kategóriu REPAIR/SECURITY

**Súbory:**
- Modify: `portablefix/gui/main_window.py` (import `undo`; metóda `_run_next` — aktuálny riadok 180 s podmienkou `action.risk == RiskLevel.DESTRUCTIVE`; metóda `_skip_destructive_actions_in_queue` — premenovaná a rozšírená; volanie v `_on_restore_point_checked`; empty-queue vetva v `_run_next`, kde sa dnes volá `report.generate_report`)
- Test: `tests/test_gui_main_window.py`

**Rozhrania:**
- Konzumuje: `undo.create_undo_script(base_dir, run_id)` z Task 3, `ModuleCategory` z Task 1, M04 katalóg (`dism_restorehealth`, `sfc_scannow` id) z Task 4 pre testovacie fixtúry.
- Produkuje: `_run_next` spúšťa Restore Point + `undo.create_undo_script` aj pre nedeštruktívnu akciu z modulu kategórie REPAIR/SECURITY; premenovaná metóda `_skip_high_risk_actions_in_queue` (nahrádza `_skip_destructive_actions_in_queue`) odstráni z frontu aj zvyšné akcie z REPAIR/SECURITY modulov, nielen DESTRUCTIVE-risk akcie.

- [ ] **Krok 1: Napísať zlyhávajúce testy**

```python
# pridať do tests/test_gui_main_window.py
def test_repair_category_safe_action_triggers_restore_point_and_undo_script(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    captured = {}

    def fake_create_restore_point(description):
        captured["called"] = True
        return True

    monkeypatch.setattr(restore_point, "create_restore_point", fake_create_restore_point)
    _write_module(tmp_path, "m04_integrity", "REPAIR", "safe_repair_action")

    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_repair")
    qtbot.addWidget(window)
    window._action_checkboxes["safe_repair_action"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: captured.get("called") is True, timeout=10000)
    assert (tmp_path / "Backups" / "run_repair" / "undo.ps1").exists()


def test_dry_run_repair_action_never_creates_restore_point_or_undo_script(qtbot, tmp_path, monkeypatch):
    from portablefix import restore_point

    def fail_if_called(description):
        raise AssertionError("create_restore_point must not be called in dry-run")

    monkeypatch.setattr(restore_point, "create_restore_point", fail_if_called)
    module_dir = tmp_path / "Modules" / "m04_integrity"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m04_integrity\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: safe_repair_action\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'repaired'\"\n"
        "    preview_command: \"Write-Output 'preview'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_repair_dry")
    qtbot.addWidget(window)
    window._action_checkboxes["safe_repair_action"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: "preview" in window.console.toPlainText(), timeout=10000)
    assert not (tmp_path / "Backups").exists()


def test_restore_point_failure_declined_skips_remaining_repair_actions_too(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: False)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.No))

    module_dir = tmp_path / "Modules" / "m04_integrity"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(
        "module_id: m04_integrity\n"
        "category: REPAIR\n"
        "actions:\n"
        "  - id: repair_action\n"
        "    label_sk: \"X\"\n"
        "    label_en: \"X\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'repair-ran'\"\n"
        "  - id: other_repair_action\n"
        "    label_sk: \"Y\"\n"
        "    label_en: \"Y\"\n"
        "    risk: SAFE\n"
        "    command: \"Write-Output 'other-ran'\"\n",
        encoding="utf-8",
    )
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(assets_dir=tmp_path, state_dir=tmp_path, settings=settings, is_admin=True, run_id="run_rp_repair_fail")
    qtbot.addWidget(window)
    window._action_checkboxes["repair_action"].setChecked(True)
    window._action_checkboxes["other_repair_action"].setChecked(True)

    window.run_selected_actions()

    reports_dir = tmp_path / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists(), timeout=10000)
    assert "repair-ran" not in window.console.toPlainText()
    assert "other-ran" not in window.console.toPlainText()
```

- [ ] **Krok 2: Spustiť testy a overiť zlyhanie**

Run: `pytest tests/test_gui_main_window.py -v`
Expected: nové 3 testy FAIL (`create_restore_point` sa nezavolá pre SAFE-risk akciu z REPAIR modulu — aktuálna podmienka reaguje len na `RiskLevel.DESTRUCTIVE`)

- [ ] **Krok 3: Pridať import `undo` do `main_window.py`**

Modify (riadok s importom `from .. import elevation, i18n, report, restore_point`):
```python
from .. import elevation, i18n, report, restore_point, undo
```

- [ ] **Krok 4: Rozšíriť podmienku v `_run_next`**

Nahradiť (aktuálny riadok 180):
```python
        if action.risk == RiskLevel.DESTRUCTIVE and not self._restore_point_attempted and not self.settings.dry_run:
            self._restore_point_attempted = True
            rp_runner = restore_point.RestorePointRunner(f"PortableFix cleanup {self.run_id}", parent=self)
```

za:
```python
        needs_restore_point = action.risk == RiskLevel.DESTRUCTIVE or module.category in (
            ModuleCategory.REPAIR,
            ModuleCategory.SECURITY,
        )
        if needs_restore_point and not self._restore_point_attempted and not self.settings.dry_run:
            self._restore_point_attempted = True
            undo.create_undo_script(self.state_dir, self.run_id)
            rp_runner = restore_point.RestorePointRunner(f"PortableFix cleanup {self.run_id}", parent=self)
```

- [ ] **Krok 5: Premenovať a rozšíriť `_skip_destructive_actions_in_queue`**

Nájsť definíciu metódy `_skip_destructive_actions_in_queue` a nahradiť ju:

```python
    def _skip_high_risk_actions_in_queue(self) -> None:
        def _is_high_risk(action_id: str) -> bool:
            module, action = self._find_action(action_id)
            return action.risk == RiskLevel.DESTRUCTIVE or module.category in (
                ModuleCategory.REPAIR,
                ModuleCategory.SECURITY,
            )

        self._queue = [aid for aid in self._queue if not _is_high_risk(aid)]
```

Nájsť jediné volanie `self._skip_destructive_actions_in_queue()` (v `_on_restore_point_checked`) a nahradiť ho `self._skip_high_risk_actions_in_queue()`.

- [ ] **Krok 6: Spustiť testy a overiť úspech**

Run: `pytest tests/test_gui_main_window.py -v`
Expected: PASS, celkovo 87 testov v projekte. (Pri natívnom crashi zopakovať beh súboru.)

- [ ] **Krok 7: Commit**

```bash
git add portablefix/gui/main_window.py tests/test_gui_main_window.py
git commit -m "feat: trigger restore point and undo.ps1 for REPAIR/SECURITY category actions"
```

---

## Štruktúra súborov po dokončení F3a

```
portablefix/
  models.py            (+ ModuleCategory, ModuleDef.category)
  module_engine.py      (+ parsovanie category:)
  undo.py                (nový)
  gui/main_window.py    (+ import undo, ModuleCategory; zoskupené _build_ui; rozšírená _run_next; premenovaná _skip_high_risk_actions_in_queue)
  i18n.py                (+ category_cleanup, category_repair, category_security)
Modules/
  m01_diagnostics/actions.yaml  (+ category: DIAGNOSTICS)
  m02_cleanup/actions.yaml       (+ category: CLEANUP)
  m04_integrity/actions.yaml     (nový, 8 akcií, category: REPAIR)
tests/
  test_module_engine.py  (+ 5 testov: 3 kategória-parsovanie, 1 m01-kategória, pôvodné nezmenené)
  test_m02_catalog.py     (+ 1 test)
  test_undo.py             (nový, 3 testy)
  test_m04_catalog.py      (nový, 4 testy)
  test_gui_main_window.py  (+ 5 testov: 2 kategóriový zoznam, 3 restore-point/undo trigger)
```

Celkový počet testov po F3a: 70 (základ) + 3 (Task 1) + 2 (Task 2) + 3 (Task 3) + 4 (Task 4) + 2 (Task 5) + 3 (Task 6) = **87**.

## Self-Review

**1. Pokrytie spec:**
- Model kategórie (`ModuleCategory`, `ModuleDef.category`) → Task 1. ✅
- Parsovanie voliteľného `category:` kľúča → Task 1. ✅
- M01/M02 dostávajú explicitný riadok kategórie → Task 2. ✅
- Restore Point spúšťač rozšírený o kategóriu REPAIR/SECURITY, pôvodná DESTRUCTIVE podmienka zachovaná → Task 6. ✅
- `undo.py` generický mechanizmus, volaný raz za dávku na rovnakom mieste ako Restore Point → Task 3 (modul) + Task 6 (volanie v `_run_next`). ✅
- M04 katalóg — 8 akcií, presné poradie, risk úrovne, žiadny `preview_command` → Task 4. ✅
- `dism_restorehealth` konečne naplní "Vyžaduje reštart" v M11 reporte → zabezpečené existujúcou `report.py` logikou z F2 (nemenená), overené implicitne testom poradia v Task 4; explicitný GUI/report test nie je pridaný, keďže `report.py`'s spracovanie `REQUIRES_REBOOT` sekcie je F2 funkcionalita, ktorá sa touto fázou nemení — riziko nízke, vlastnosť je len teraz prvýkrát reálne "naplnená" dátami, nie novo postavená.
- Oprava GUI kategórií (odlíšené kategórie, zoskupenie, žiadna filtrácia klikom) → Task 5. ✅
- Vylúčenia (findstr CBS, bootrec/bcdboot, M03/M05/M06, registrácia reálnych undo krokov) → nemajú tasky, čo je správne, keďže sú explicitne mimo rozsahu. ✅

**2. Kontrola placeholderov:** Žiadne "TBD"/"implementovať neskôr" nájdené — všetky kroky obsahujú plný kód alebo presné YAML.

**3. Konzistencia typov/rozhraní:**
- `create_undo_script(base_dir: Path, run_id: str, steps: list[str] | None = None) -> Path` — rovnaký signature v Task 3 (definícia) aj Task 6 (volanie `undo.create_undo_script(self.state_dir, self.run_id)` — bez `steps`, čo je platné vďaka defaultu `None`). ✅
- `ModuleCategory` hodnoty (`DIAGNOSTICS`, `CLEANUP`, `REPAIR`, `SECURITY`) konzistentné naprieč Task 1 (definícia), Task 4 (YAML `category: REPAIR`), Task 5 (`category_i18n_keys` mapovanie všetkých 4 hodnôt), Task 6 (`in (ModuleCategory.REPAIR, ModuleCategory.SECURITY)`). ✅
- `_skip_high_risk_actions_in_queue` — nový názov použitý konzistentne v Task 6 krokoch 5-6 (definícia aj úprava volania). ✅
- Testovacia fixtúra `_write_module` (Task 5) je zdieľaná pomocná funkcia — Task 6 duplicitne inline-uje podobný YAML zápis namiesto volania `_write_module`, keďže potrebuje 2 akcie v jednom module (odlišná signatúra) — zámerné, nie chyba.

Žiadne medzery nájdené, plán je pripravený na implementáciu.
