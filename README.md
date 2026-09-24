<p align="center">
  <img src="docs/logo.png" alt="PortableFix" width="120">
</p>

<h1 align="center">PortableFix</h1>
<p align="center"><i><a href="README.en.md">English version</a></i></p>

<p align="center">
<a href="https://github.com/vxkShelby/portableFixer/actions/workflows/tests.yml"><img src="https://github.com/vxkShelby/portableFixer/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
<a href="https://github.com/vxkShelby/portableFixer/releases/latest"><img src="https://img.shields.io/github/v/release/vxkShelby/portableFixer" alt="Latest release"></a>
<a href="https://github.com/vxkShelby/portableFixer/releases"><img src="https://img.shields.io/github/downloads/vxkShelby/portableFixer/total" alt="Downloads"></a>
<a href="https://github.com/vxkShelby/portableFixer"><img src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6" alt="Platform"></a>
</p>

Prenosný diagnostický a opravný nástroj pre Windows 10/11, určený na beh
z USB kľúča. Python 3.12 + PySide6 GUI, akcie vykonáva cez PowerShell.

## Náhľad

![PortableFix screenshot](docs/screenshot.png)

## Rýchly štart

1. Skopíruj celý priečinok na USB kľúč (alebo spusti priamo z disku).
2. Spusti `PortableFix.cmd` (alebo `python main.py` pri vývoji).
3. Bez admin práv beží aplikácia v režime len-diagnostika; tlačidlom
   **Reštartovať ako administrátor** získaš plný prístup.
4. Zaškrtni akcie, over v **DRY-RUN** režime (predvolene zapnutý), potom
   DRY-RUN vypni a spusti naostro.

## Moduly

| Modul | Kategória | Obsah |
|---|---|---|
| M01 | Diagnostika | Systémové informácie (OS, HW, disky, procesy...) |
| M02 | Čistenie | Temp súbory, cache, kôš, Windows Update cache... |
| M03 | Oprava | Disk: SMART, NTFS scan/SpotFix, TRIM, chkdsk pri reštarte |
| M04 | Oprava | Integrita systému: DISM, SFC, AppX, WMI |
| M05 | Oprava | Windows Update: reset služieb a cache, DLL, detekcia |
| M06 | Oprava | Sieť: DNS, hosts, DHCP, Winsock, TCP/IP |
| M07 | Diagnostika | Autostart: registry Run, Startup, úlohy, služby, WMI, IFEO backdoor, služby bez úvodzoviek |
| M08 | Zabezpečenie | Defender, firewall, UAC audit + rýchly sken, WPBT disable |
| M09 | Oprava | Tuning: plán napájania, vizuálne efekty, End Task, Sticky Keys, klasické menu |
| M10 | Diagnostika | Drivery: problémové zariadenia (+ reštart), ovládače tretích strán, sieť/GPU, záloha/obnova |
| M11 | — | Reporting (HTML report po každej dávke, nie katalóg) |
| M12 | Diagnostika | Online: test pripojenia po vrstvách, DNS, proxy |
| M13 | Čistenie | Debloat: telemetria, naplánované úlohy, Fast Startup, reklamy v Exploreri, Recall/Click to Do |
| M14 | Oprava | Tlač: tlačiarne, ovládače, offline/ghost tlačiarne, reset spooleru |
| M15 | Oprava | Zavádzanie/platforma: BCD, TPM, Secure Boot, BitLocker, Bezpečný režim, F8 recovery |
| M16 | Oprava | Office: verzia/kanál, doplnky Outlooku, OST/PST, rýchla/úplná oprava |
| M17 | Oprava | Prehliadače: rozšírenia, policy, únos domovskej stránky, reset profilu |
| M18 | Oprava | Záloha používateľských priečinkov (Desktop/Documents/Pictures/Favorites) |
| M19 | Oprava | Voliteľné funkcie Windows: prehľad, .NET 3.5, PowerShell v2, Sandbox |
| M20 | Oprava | Aktualizácia softvéru cez winget: zoznam, zastaraný softvér, update all |
| M21 | Oprava | Hardvérové senzory: PawnIO stav/inštalácia (CPU teplota/hodinky cez LibreHardwareMonitor) |
| M22 | Čistenie | Hlbšie čistenie: osamotené uninstall položky, duplicitné súbory, nefunkčné odkazy (.lnk), bezpečné prepísanie voľného miesta |

## Funkcie pre technika

- **Predvoľby:** vstavané (Rýchle čistenie, Plná diagnostika, Privacy
  debloat) aj **vlastné** - vyber akcie, klikni **+ Uložiť výber**, pomenuj.
  Vlastné predvoľby sa ukladajú do `Data/settings.json` hneď (nie až pri
  zatvorení), cestujú s USB kľúčom a mažú sa pravým tlačidlom myši.
- **Prehľad (dashboard):** skóre systému po analýze (zelené/oranžové/červené),
  počty nálezov podľa kategórií a **história posledných behov na tomto PC**
  s odkazom na report - pri opakovanej návšteve u toho istého klienta je
  hneď vidno, čo sa robilo minule.
- **HTML report:** v jazyku aplikácie, so súhrnom zlyhaných akcií (odkazy
  priamo na detail), prehľadom podľa modulov/kategórií, filtrom
  (všetky / len zlyhané / len zmeny) a vyhľadávaním. Tlačidlo **Tlačiť /
  uložiť ako PDF** dá čistý biely výtlačok pre klienta. Report je jeden
  samostatný súbor bez internetu.
- **Balík pre klienta:** tlačidlo **Uložiť balík pre klienta** (v súhrne
  dávky aj pri každom behu v histórii) uloží jeden ZIP
  `PortableFix_<PC>_<run-id>.zip` s HTML/JSON reportom, audit logom,
  `undo.ps1` (ak existuje) a krátkym README (SK+EN) o tom, ako undo
  bezpečne použiť - pripravené na e-mail alebo archív. Obsahuje len súbory
  toho jedného behu (nikdy `Data/settings.json` ani iné behy).
- **Zákazka** (tlačidlo v hornej lište, Ctrl+J): meno technika (zapamätá
  sa), klient / číslo zákazky a poznámka - zobrazia sa v hlavičke reportu.
- **Upozornenie na koniec dávky:** ak je okno v pozadí (napr. počas
  dlhého DISM/SFC), bliká na paneli úloh a zobrazí systémovú notifikáciu
  s počtom OK/zlyhaných akcií.
- **Klávesové skratky:** F5 spustiť, Ctrl+A vybrať všetko, Ctrl+F hľadať,
  Esc vymazať hľadanie, Ctrl+S uložiť predvoľbu, Ctrl+J zákazka, F1 prehľad.
- **Odinštalovanie programov a winget aktualizácie:** tieto panely
  rešpektujú DRY-RUN (len vypíšu príkazy), pred akoukoľvek zmenou sa
  opýtajú a každý program, balík aj vymazaný zvyšok v registri (ten sa
  pred vymazaním zálohuje do `Backups/<run-id>/*.reg`) sa zapíše do
  audit logu.

## Bezpečnostné mechanizmy

- **Úrovne rizika:** každá akcia je označená SAFE / MODERATE /
  DESTRUCTIVE / REQUIRES_REBOOT. MODERATE a vyššie vyžadujú potvrdenie,
  DESTRUCTIVE má osobitné varovanie o nevratnosti.
- **DRY-RUN:** predvolene zapnutý — akcie sa len vypíšu (alebo spustia
  read-only náhľad), nič sa nemení.
- **Bod obnovenia:** pred prvou DESTRUCTIVE akciou alebo akoukoľvek
  akciou z kategórie Oprava/Zabezpečenie sa raz za dávku vytvorí System
  Restore Point na systémovom disku (best-effort; pri zlyhaní sa
  aplikácia opýta, či pokračovať). Windowsový 24-hodinový limit na
  vytváranie bodov obnovenia sa na tento jeden bod dočasne zruší a
  pôvodné nastavenie sa hneď obnoví - predtým Windows bod potichu
  preskočil a dávka bežala bez neho.
- **undo.ps1:** akcie s vratným účinkom (napr. reset hosts súboru,
  zastavenie služieb, zmena plánu napájania) priebežne zapisujú svoje
  undo príkazy do `Backups/<run-id>/undo.ps1` — v opačnom (LIFO)
  poradí, takže skript sa dá spustiť ako celok. Zapisuje sa po každej
  úspešnej akcii, takže aj pri páde aplikácie súbor odráža reálny stav.
- **Audit log + report:** každá akcia sa zapisuje do
  `Logs/<run-id>/audit.jsonl` a po každej dávke sa generuje HTML report
  do `Reports/`.
- **Auto-update:** pri behu ako zbalený `.exe` appka pri štarte ticho
  skontroluje GitHub Releases (`vxkShelby/portableFixer`); ak existuje
  novšia verzia, zobrazí dismissovateľný banner s ponukou stiahnuť a
  aplikovať. Sťahovanie beží na pozadí a nahradí celý balík (`App/`,
  `Modules/`, `Vendor/`, `PortableFix.cmd`) - `Data/settings.json`
  (jazyk, dry-run) sa zachová. Pri zlyhaní (offline, timeout) je ticho
  — nič nevypíše.
- **Ochrana pred zmazaním vlastných súborov:** akcie čistiace `%TEMP%`
  a `%WINDIR%\Temp` (`user_temp`, `system_temp`) rozpoznajú, ak appka
  beží zvnútra tohto priečinka, a jej priečinok vynechajú - ak sa to
  nedá bezpečne určiť (appka JE `%TEMP%`, alebo je presmerovaný cez
  junction/symlink), akcia sa radšej vôbec nespustí a appka to oznámi.
  Appka pri štarte tiež zaloguje vlastné cesty, a počas behu dávky
  kontroluje, či jej priečinok medzičasom nezmizol - ak áno, dávku
  okamžite zastaví namiesto tichého pokračovania.

## Štruktúra priečinkov

```
PortableFix/
  PortableFix.cmd        spúšťač
  main.py                vstupný bod
  portablefix/           aplikačný kód
  Modules/<id>/actions.yaml   deklaratívne katalógy akcií
  Vendor/                 LibreHardwareMonitorLib (voliteľné HW senzory)
  Data/                  settings.json, SHA256SUMS (runtime)
  Logs/                  audit logy (runtime)
  Reports/               HTML reporty (runtime)
  Backups/               undo.ps1 skripty (runtime)
  scripts/build.ps1      PyInstaller build
```

Ak USB nie je zapisovateľné, runtime priečinky sa presunú do
`%TEMP%\PortableFix` (aplikácia to oznámi bannerom).

## Build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

Výstup: `App/PortableFix.exe` (PyInstaller onefile, jeden spustiteľný
súbor, žiadny `_internal` podpriečinok). Skript automaticky aj
vygeneruje kontrolné súčty, zbalí portable ZIP a skompiluje inštalátor
(ak je nájdený `ISCC.exe`) - žiadny manuálny krok navyše netreba.

## Manuálne kroky pred distribúciou

Tieto kroky vyžadujú zdroje mimo repozitára a robia sa ručne:

1. **Podpísanie kódu** — `App\PortableFix.exe` je podpísaný self-signed
   certifikátom (`CN=PortableFix Self-Signed`, verejná časť v
   `Data\PortableFix-SelfSigned.cer`). Dôveryhodnosť certifikátu je
   **presne ten istý krok, ktorý sa zneužíva pri phishingu** - importuj
   ho len ak `Data\PortableFix-SelfSigned.cer` pochádza z balíka stiahnutého
   z [oficiálnych GitHub Releases](https://github.com/vxkShelby/portableFixer/releases)
   tohto repa (skontroluj `Data/SHA256SUMS` oproti stiahnutému balíku),
   nikdy z e-mailu/odkazu od niekoho iného. Na cieľovom počítači sa dá
   podpis zdôveryhodniť importom (admin PowerShell):
   ```powershell
   Import-Certificate -FilePath Data\PortableFix-SelfSigned.cer -CertStoreLocation Cert:\LocalMachine\Root
   Import-Certificate -FilePath Data\PortableFix-SelfSigned.cer -CertStoreLocation Cert:\LocalMachine\TrustedPublisher
   ```
   Pre distribúciu bez varovaní na cudzích počítačoch je potrebný
   komerčný certifikát (OV/EV); potom prepodpíš:
   ```powershell
   signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 App\PortableFix.exe
   ```
   Po každom podpísaní znovu vygeneruj SHA256SUMS
   (`python scripts/generate_sha256sums.py .`).
2. **VM test** — otestuj na čistej inštalácii Windows 10 aj 11
   (bez admin práv aj s nimi): štart aplikácie, DRY-RUN dávka,
   ostrá SAFE dávka, kontrola vygenerovaného reportu a undo.ps1.

## Release proces (nová verzia s auto-update)

Ručný postup, nič z toho nie je automatizované:

1. Zvýš `APP_VERSION` v `portablefix/version.py` **a** `MyAppVersion`
   v `installer/PortableFix.iss` (musia sedieť).
2. `powershell -ExecutionPolicy Bypass -File scripts\build.ps1` →
   `App/PortableFix.exe` (onefile), `Output/PortableFix-Portable.zip`
   (+ `.sha256`), a ak máš nainštalovaný Inno Setup (ISCC.exe,
   [jrsoftware.org](https://jrsoftware.org/isinfo.php)) aj
   `Output/PortableFix-Setup.exe`.
3. Podpíš `App/PortableFix.exe` (`signtool sign ...`, viď vyššie) **pred**
   krokom 2 alebo re-spusti `scripts\build_release_zip.ps1` po podpise,
   nech je podpísaný exe aj v zipe.
4. `python scripts/generate_sha256sums.py .` — aktualizuje
   `Data/SHA256SUMS` (obsah zipu musí mať aktuálne SHA256SUMS, spusti pred
   krokom 2/3 podľa poradia vyššie).
5. Vytvor GitHub Release s tagom `v<verzia>` (napr. `v1.1.0`), nahraj
   **tri** súbory ako assety, presne s týmito menami (auto-update aj
   inštalátor ich vyhľadávajú podľa fixného mena, nie podľa verzie):
   - `PortableFix-Portable.zip` — toto sťahuje aj auto-update mechanizmus
   - `PortableFix-Portable.zip.sha256`
   - `PortableFix-Setup.exe` — inštalátor pre bežných používateľov

**Dôležité:** ak sa release vytvorí bez `.sha256` assetu, auto-update
sťahovanie odmietne (fail-closed, banner "Stiahnutie zlyhalo") namiesto
aplikovania neoverenej aktualizácie — no bez neho sa aktualizácia vôbec
nedostane k používateľom, takže krok 5 nikdy nevynechaj.

Auto-update od tejto verzie sťahuje **celý balík** (exe + Data + Modules),
nie len samotné `.exe` — takto sa k už nainštalovaným kópiám dostanú aj
nové/zmenené moduly, nielen zmeny v Python kóde. `Data/settings.json`
(jazyk, dry-run) sa pri update zachová, všetko ostatné v `App/`, `Modules/`
a `PortableFix.cmd` sa nahradí.

## Vývoj

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ --deselect tests/test_gui_main_window.py --deselect tests/test_executor.py
python -m pytest tests/test_gui_main_window.py
python -m pytest tests/test_executor.py
python -m pytest tests/test_updater.py
```

`tests/test_gui_main_window.py`, `tests/test_executor.py` a
`tests/test_updater.py` (jeho `UpdateCheckRunner`/`UpdateDownloadRunner`
testy) spúšťajú reálne PowerShell procesy cez rovnaký `QThread`
mechanizmus (`portablefix/executor.py`); pri behu viacerých takýchto
súborov naraz v jednej pytest session sa môže objaviť prechodný
natívny crash prostredia (STATUS_STACK_BUFFER_OVERRUN) — nie je to
chyba kódu. Ak sa to stane, spusti postihnuté testy jednotlivo
(`python -m pytest tests/test_gui_main_window.py::test_name`) s jedným
opakovaním pri zlyhaní, namiesto celého súboru naraz.

## Dev tooling: self-improve loop

Jednorazové nastavenie pre každý klon, na zapnutie lokálneho
self-improve git hooku (pozri
`docs/superpowers/specs/2026-09-13-self-improve-loop-design.md`):

```bash
git config core.hooksPath .githooks
```

Po každom commite toto skontroluje reálne signály (zlyhávajúce testy,
opakované fix commity, nové záznamy v crash.log) a — len ak nejaký
nájde — zavolá lokálny Archon CLI workflow, ktorý vec preskúma,
opraví a otvorí PR. Nikdy nič nemerguje a nikdy nebeží v CI. Vyžaduje
lokálne nainštalovaný Archon CLI (`archon doctor` by mal prejsť — pozri
`docs/superpowers/specs/2026-09-13-archon-cli-findings.md`).

## Známe obmedzenia

- Undo pokrýva len akcie so statickým vratným príkazom; DISM/SFC/chkdsk
  opravy sú z princípu nevratné (kryje ich bod obnovenia).
- Undo pri kombinovaných akciách (napr. zastavenie 4 služieb naraz) sa
  zapíše len pri plnom úspechu akcie.
- Bod obnovenia sa vytvára aj pri čisto diagnostických akciách z
  kategórií Oprava/Zabezpečenie (zámerné, konzervatívne správanie).
- `regsvr32`/`UsoClient` kroky v M05 hlásia úspech aj pri tichom
  zlyhaní (neblokujúce procesy).
