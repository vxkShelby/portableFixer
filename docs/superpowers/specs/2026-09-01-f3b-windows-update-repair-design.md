# PortableFix — F3b Design: Oprava Windows Update (M05) + reálne undo kroky

Rozsah: druhá pod-fáza F3 z `PortableFix_SPEC.md`. Nadväzuje na F3a
(M04 integrita + generický `undo.ps1` mechanizmus). Táto pod-fáza rieši
M05 (Windows Update) a je prvým reálnym konzumentom `undo.ps1` —
doteraz sa doň zapisovala len prázdna hlavička. M03 a M06 dostanú
vlastné spec+plan cykly.

Poznámka k zdroju: master spec `PortableFix_SPEC.md` nebol v tejto
session k dispozícii (bol len vložený na začiatku, neuložený ako
súbor, stratený po kompresii kontextu). Užívateľ explicitne potvrdil
navrhnúť M05 z bežnej overenej praxe opravy Windows Update namiesto
presnej master spec sekcie — nasledujúci návrh teda vychádza z
klasických, široko zdokumentovaných krokov (service reset,
SoftwareDistribution/catroot2 rename, WU DLL re-registrácia, trigger
detekcie), nie z pôvodnej master spec formulácie.

## Rozhodnutia z brainstormingu

- **Granularita akcií:** rozložené kroky (rovnaký štýl ako M02/M04),
  nie jedna kombinovaná "reset Windows Update" akcia — užívateľ vidí a
  potvrdzuje každý krok samostatne, presné risk levels na akciu.
- **Undo mechanizmus:** statický `undo_command:` reťazec v YAML
  (rovnaký štýl ako `preview_command` z F2), nie dynamické zachytenie
  stavu za behu — cesty `SoftwareDistribution`/`catroot2` sú pevné
  systémové cesty, undo príkaz je teda vopred známy a nemení sa.
- **DLL re-registrácia:** zahrnutá ako samostatná voliteľná MODERATE
  akcia (pokrýva edge-case starších/poškodených inštalácií), aj keď na
  modernom Windowse často nie je potrebná.
- **Kedy sa undo.ps1 reálne zapisuje:** F3a volalo
  `create_undo_script` raz na začiatku dávky s prázdnym zoznamom
  krokov (žiadny konzument). F3b potrebuje reálne kroky, ktoré sú
  známe až progresívne, ako akcie počas dávky uspejú — pozri sekciu
  nižšie.

## Model: `ActionDef.undo_command`

`portablefix/models.py`: `ActionDef` získava nové voliteľné pole
`undo_command: str | None = None` (rovnaké miesto/štýl ako
`preview_command`, pridané za neho). `module_engine.load_module`
parsuje voliteľný YAML kľúč `undo_command:` rovnako ako
`preview_command` (`raw.get("undo_command")`).

## Progresívne zapisovanie undo krokov (nový mechanizmus)

Problém: `create_undo_script` sa v F3a volá raz na začiatku dávky,
pred behom akcií — vtedy ešte nevieme, ktoré kroky reálne uspejú.
Prázdny zoznam bol pre F3a v poriadku (žiadny konzument), pre F3b už
nie.

Riešenie: `MainWindow` si počas dávky drží `self._undo_steps: list[str]`
(inicializovaný na prázdny zoznam v `run_selected_actions`, vedľa
existujúceho resetu `_restore_point_attempted`). Volanie
`create_undo_script` na začiatku dávky (F3a, `_run_next`) zostáva
nezmenené — zapíše hlavičku s prázdnym zoznamom ako doteraz (fallback
pre prípad pádu appky pred prvým úspešným krokom).

V `_on_action_finished` (po zápise audit log záznamu): ak
`not self.settings.dry_run` **a** `exit_code == 0` **a**
`action.undo_command` je nastavený, pridá sa `action.undo_command` do
`self._undo_steps` a `undo.create_undo_script(self.state_dir,
self.run_id, steps=self._undo_steps)` sa zavolá znova — prepíše ten
istý súbor s aktuálnym, kumulatívnym stavom. `undo.py` (F3a) už je na
toto pripravené — jeho signatúra `create_undo_script(base_dir, run_id,
steps=None)` sa nemení, len sa teraz reálne volá s neprázdnym
zoznamom, a to opakovane (idempotentné prepisovanie).

Výhoda oproti alternatíve "zapísať raz na konci dávky": aj pri páde
appky uprostred dávky zostane na disku `undo.ps1` odrážajúci presne
to, čo sa reálne stihlo vykonať — nie prázdny placeholder a nie
strata všetkých krokov kvôli pádu tesne pred koncom.

Dry-run: akcie s `undo_command` sa v dry-run režime nikdy nezapíšu do
`_undo_steps` (nič reálne sa nevykonalo, exit_code z DRY-RUN vetvy
`executor.py` je vždy 0, ale to neznamená úspech reálnej akcie).

## M05 katalóg (6 akcií, poradie v YAML = poradie behu, kategória REPAIR)

Kategória REPAIR automaticky spúšťa Restore Point + undo.ps1 mechanizmus
podľa existujúcej F3a podmienky (`module.category in (REPAIR, SECURITY)`)
— žiadna nová trigger logika nie je potrebná.

| id | risk | príkaz | undo_command |
|---|---|---|---|
| wu_check_services | SAFE | `Get-Service -Name wuauserv,bits,cryptsvc,msiserver \| Select-Object Name,Status,StartType \| Format-Table -AutoSize` | — |
| wu_stop_services | MODERATE | `Stop-Service -Name wuauserv,bits,cryptsvc,msiserver -Force -ErrorAction SilentlyContinue` | `Start-Service -Name wuauserv,bits,cryptsvc,msiserver -ErrorAction SilentlyContinue` |
| wu_reset_cache | MODERATE | `if (Test-Path "$env:WINDIR\SoftwareDistribution") { Rename-Item -Path "$env:WINDIR\SoftwareDistribution" -NewName "SoftwareDistribution.bak" -Force -ErrorAction SilentlyContinue }; if (Test-Path "$env:WINDIR\System32\catroot2") { Rename-Item -Path "$env:WINDIR\System32\catroot2" -NewName "catroot2.bak" -Force -ErrorAction SilentlyContinue }` | `if ((Test-Path "$env:WINDIR\SoftwareDistribution.bak") -and -not (Test-Path "$env:WINDIR\SoftwareDistribution")) { Rename-Item -Path "$env:WINDIR\SoftwareDistribution.bak" -NewName "SoftwareDistribution" -Force }; if ((Test-Path "$env:WINDIR\System32\catroot2.bak") -and -not (Test-Path "$env:WINDIR\System32\catroot2")) { Rename-Item -Path "$env:WINDIR\System32\catroot2.bak" -NewName "catroot2" -Force }` |
| wu_restart_services | SAFE | `Start-Service -Name wuauserv,bits,cryptsvc,msiserver -ErrorAction SilentlyContinue` | — |
| wu_reregister_dlls | MODERATE | `$dlls = @('atl.dll','urlmon.dll','mshtml.dll','shdocvw.dll','browseui.dll','jscript.dll','vbscript.dll','scrrun.dll','msxml.dll','msxml3.dll','msxml6.dll','actxprxy.dll','softpub.dll','wintrust.dll','dssenh.dll','rsaenh.dll','gpkcsp.dll','sccbase.dll','slbcsp.dll','cryptdlg.dll','oleaut32.dll','ole32.dll','shell32.dll','initpki.dll','wuapi.dll','wuaueng.dll','wucltui.dll','wups.dll','wups2.dll','wuwebv.dll'); foreach ($dll in $dlls) { regsvr32.exe /s "$env:WINDIR\System32\$dll" }` | — |
| wu_trigger_detection | SAFE | `UsoClient.exe StartScan` | — |

Tabuľka vyššie je záväzná pre poradie, risk úrovne, presné príkazy a to,
ktoré akcie majú `undo_command`.

`wu_reset_cache`'s popis (`description_sk`/`description_en`) musí
explicitne uvedomiť užívateľa o obmedzení undo: funguje spoľahlivo len
ak sa medzitým nespustila `wu_restart_services` (tá si nechá Windows
vytvoriť nové priečinky, čím `.bak` osirie).

## Testovanie

Rovnaká disciplína ako F1-F3a: unit testy na `undo_command` parsovanie
(`module_engine`/`models`), na M05 katalóg (počet akcií, risk
distribúcia, poradie, ktoré akcie majú/nemajú `undo_command`). GUI
testy (`pytest-qt`) na progresívne zapisovanie: úspešná akcia s
`undo_command` sa objaví v `undo.ps1` po jej dokončení (nie až na konci
dávky); zlyhaná akcia (nenulový exit_code) sa do `undo.ps1` nezapíše;
dry-run nikdy nezapisuje reálne kroky.

## Mimo rozsahu F3b (výslovne odložené)

- M03 (disk/súborový systém), M06 (sieť) — vlastné spec+plan cykly.
- Žiadna zmena `report.py` — existujúci generický audit log/report
  mechanizmus pokrýva aj M05 bez úprav.
- Žiadna validácia/ošetrenie prípadu, že `wu_reset_cache`'s `.bak`
  priečinok už existuje z predchádzajúceho neúspešného behu (implicitne
  ošetrené cez `-Force`/`Test-Path` guardy v príkaze, netreba osobitnú
  GUI logiku).
