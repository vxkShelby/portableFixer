# PortableFix — F3a Design: Oprava integrity systému (M04) + undo.ps1 infra

Rozsah: prvá pod-fáza F3 z `PortableFix_SPEC.md`. Pôvodná fáza F3
zväzuje M03 (disk/súborový systém), M04 (integrita systému), M05
(Windows Update oprava) a M06 (sieť) do jednej fázy — na jeden
implementačný cyklus je to príliš veľa. Táto pod-fáza rieši len M04 a
vybudovanie `undo.ps1` infraštruktúry, ktorú M05/M06 neskôr reálne
využijú. M03/M05/M06 dostanú vlastné spec+plan cykly.

## Rozhodnutia z brainstormingu

- **Prečo M04 prvé:** najpresnejšie špecifikované v master spec (presné
  poradie DISM RestoreHealth → sfc /scannow → reboot → sfc /verifyonly),
  priamo využíva Restore Point infraštruktúru z F2.
- **`undo.ps1` nemá pre M04 čo reálne zapísať** — SFC/DISM opravy,
  AppX re-registrácia a WMI salvage nie sú vratné akcie. Master spec
  viaže undo skript na registry/služby/sieťové zmeny, nič také M04
  nerobí. Postaví sa generický, znovupoužiteľný mechanizmus (per-run
  `Backups/<run-id>/undo.ps1` s registráciou krokov), pre M04 sa zapíše
  len hlavička "žiadne vratné zmeny v tomto behu" — mechanizmus sa tým
  overí end-to-end, reálny obsah príde s M05/M06.
- **Vylúčené z M04 katalógu:** `findstr`-extrakcia CBS logu do súboru
  (duplicitné s M11 reportom, ktorý už zachytáva plný výstup každej
  akcie), `bootrec`/`bcdboot` (master spec sám hovorí "len z WinRE /
  advanced" — z bežiaceho Windows, kde tento nástroj beží, nedáva
  zmysel).
- **Kategórie modulov (nová vec):** master spec viaže Restore Point na
  kategóriu akcie ("pred akoukoľvek akciou kategórie Repair alebo
  Security"), nie na risk úroveň jednotlivej akcie ako to F2 urobilo pre
  M02 (tam bol spúšťač `risk == DESTRUCTIVE`, čisto moje rozhodnutie pri
  F2 brainstormingu, nie priamo spec). Teraz treba oba mechanizmy popri
  sebe: M02 zostáva spúšťané cez DESTRUCTIVE risk (nemení sa), M04 sa
  spúšťa cez kategóriu REPAIR (nová vec, nezávislá od risk levelu
  jednotlivej akcie v module).
- **Oprava GUI kategórií teraz, nie neskôr:** F2 finálny review označil
  duplicitný/neusporiadaný zoznam kategórií ako "nemal by čakať na
  štvrtý modul" — M04 je tretí modul, presne ten bod. Rieši sa teraz:
  reálne odlíšené kategórie v ľavom paneli, akcie zoskupené pod
  nadpismi kategórie namiesto jedného plochého stĺpca. Žiadne
  klikni-a-filtruj — len správne zoskupenie a popisky.

## Model kategórie

`portablefix/models.py` získava:

```python
class ModuleCategory(str, Enum):
    DIAGNOSTICS = "DIAGNOSTICS"
    CLEANUP = "CLEANUP"
    REPAIR = "REPAIR"
    SECURITY = "SECURITY"
```

`ModuleDef` získava pole `category: ModuleCategory = ModuleCategory.DIAGNOSTICS`
(voliteľné, spätne kompatibilné). `module_engine.load_module` parsuje
voliteľný `category:` YAML kľúč. Existujúce katalógy `M01`/`M02`
dostanú explicitný riadok (`category: DIAGNOSTICS` / `category: CLEANUP`)
— malá, neškodná úprava dvoch existujúcich YAML súborov.

## Restore Point + undo trigger (rozšírenie F2 mechanizmu)

F2 postavilo: pred prvou `DESTRUCTIVE` akciou v dávke sa raz pokúsi
vytvoriť Restore Point (`restore_point.RestorePointRunner`), zlyhanie
= potvrdzovací dialóg s možnosťou pokračovať.

F3a rozširuje podmienku spúšťača v `MainWindow._run_next` z
`action.risk == RiskLevel.DESTRUCTIVE` na
`action.risk == RiskLevel.DESTRUCTIVE or module.category in (ModuleCategory.REPAIR, ModuleCategory.SECURITY)`
— stále raz za dávku (`self._restore_point_attempted` flag sa nemení).

Rovnaká rozšírená podmienka spúšťa aj vygenerovanie `undo.ps1` (nový
krok pridaný na rovnaké miesto v `_run_next`, kde sa dnes spúšťa
Restore Point): keď dávka obsahuje aspoň jednu DESTRUCTIVE akciu ALEBO
akciu z REPAIR/SECURITY
modulu, po úspešnom (alebo aj neúspešnom, best-effort) pokuse o Restore
Point sa zavolá `undo.create_undo_script(state_dir, run_id)`, ktorý
zapíše `Backups/<run-id>/undo.ps1` s hlavičkou (timestamp, run_id,
zoznam registrovaných krokov — pre F3a prázdny).

## `undo.py` — nový modul

```python
def create_undo_script(base_dir: Path, run_id: str, steps: list[str] | None = None) -> Path:
    """Zapíše Backups/<run_id>/undo.ps1. `steps` = zoznam PowerShell
    príkazov, ktoré vrátia zmeny späť. Prázdny/None zoznam => skript
    obsahuje len informačnú hlavičku."""
```

Volá sa raz za dávku (rovnaké miesto ako `report.generate_report`), nie
raz za akciu — F3a nemá žiadne kroky na registráciu, takže `steps` je
vždy `None`/prázdny zoznam. Mechanizmus na registráciu jednotlivých
krokov (napr. `MainWindow` by zbieral kroky z akcií, ktoré ich vedia
poskytnúť) nechá sa navrhnúť až pri M05/M06, keď bude reálny obsah na
registráciu — stavať ho teraz naslepo by bolo YAGNI porušenie.

## M04 katalóg (8 akcií, poradie v YAML = poradie behu)

| id | risk | príkaz (skrátene) |
|---|---|---|
| dism_checkhealth | SAFE | `DISM /Online /Cleanup-Image /CheckHealth` |
| dism_scanhealth | SAFE | `DISM /Online /Cleanup-Image /ScanHealth` |
| dism_restorehealth | REQUIRES_REBOOT | `DISM /Online /Cleanup-Image /RestoreHealth` |
| sfc_scannow | MODERATE | `sfc /scannow` |
| sfc_verifyonly | SAFE | `sfc /verifyonly` |
| appx_reregister | MODERATE | `Get-AppXPackage -AllUsers \| Foreach {Add-AppxPackage -DisableDevelopmentMode -Register "$($_.InstallLocation)\AppXManifest.xml"}` |
| wmi_verify | SAFE | `winmgmt /verifyrepository` |
| wmi_salvage | DESTRUCTIVE | `winmgmt /salvagerepository` |

Poradie v tabuľke = poradie v YAML, čo pri zaškrtnutí `dism_restorehealth`
aj `sfc_scannow` naraz zabezpečí správne poradie behu bez ďalšej logiky
(front sa stavia iteráciou cez `_action_checkboxes` v poradí YAML,
filtrovanú na zaškrtnuté).

`dism_restorehealth`'s `REQUIRES_REBOOT` konečne naplní M11 report
sekciu "Vyžaduje reštart", ktorá bola od F2 postavená, ale prázdna.

Žiadna z 8 akcií nemá zmysluplný `preview_command` (SFC/DISM nemajú
read-only "čo by sa opravilo" režim) — dry-run pre tieto akcie zostáva
pri F1 správaní (vypíše `[DRY-RUN] <príkaz>` bez spustenia), rovnako ako
M01's read-only diagnostika.

## GUI oprava kategórií

`portablefix/gui/main_window.py`:

- Ľavý panel (`category_list`): jeden riadok na **kategóriu** (nie na
  modul), preložený cez `i18n` (`category_diagnostics`,
  `category_cleanup` (nový kľúč), `category_repair` (nový kľúč)).
  Žiadna duplicita.
- Stredný panel: akcie zoskupené pod tučným nadpisom kategórie
  (`QLabel` s bold fontom nad každou skupinou checkboxov), moduly v
  rámci rovnakej kategórie idú pod seba bez ďalšieho nadpisu.

## Testovanie

Rovnaká disciplína ako F1/F2: unit testy na `undo.py` (reálny
file I/O), na rozšírenú `module_engine`/`models` kategóriu, na M04
katalóg (počet akcií, risk distribúcia, poradie DISM/sfc). GUI testy
(`pytest-qt`) na rozšírenú restore-point/undo trigger podmienku
(REPAIR-kategória modul spúšťa Restore Point aj bez DESTRUCTIVE akcie)
a na kategóriové zoskupenie v `_build_ui`.

## Mimo rozsahu F3a (výslovne odložené)

- M03 (disk/súborový systém), M05 (Windows Update), M06 (sieť) —
  vlastné spec+plan cykly.
- Registrácia reálnych undo krokov (žiadny konzument v F3a).
- `findstr` CBS log extrakcia, `bootrec`/`bcdboot` — vysvetlené vyššie.
- Klikni-a-filtruj interaktivita kategóriového zoznamu — len zoskupenie
  a popisky.
