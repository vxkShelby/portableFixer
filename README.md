# PortableFix

Prenosný diagnostický a opravný nástroj pre Windows 10/11, určený na beh
z USB kľúča. Python 3.12 + PySide6 GUI, akcie vykonáva cez PowerShell.

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
| M07 | Diagnostika | Autostart: registry Run, Startup, úlohy, služby |
| M08 | Zabezpečenie | Defender, firewall, UAC audit + rýchly sken |
| M09 | Oprava | Tuning: plán napájania, vizuálne efekty |
| M10 | Diagnostika | Drivery: problémové zariadenia, ovládače tretích strán |
| M11 | — | Reporting (HTML report po každej dávke, nie katalóg) |
| M12 | Diagnostika | Online: test pripojenia po vrstvách, DNS, proxy |

## Bezpečnostné mechanizmy

- **Úrovne rizika:** každá akcia je označená SAFE / MODERATE /
  DESTRUCTIVE / REQUIRES_REBOOT. MODERATE a vyššie vyžadujú potvrdenie,
  DESTRUCTIVE má osobitné varovanie o nevratnosti.
- **DRY-RUN:** predvolene zapnutý — akcie sa len vypíšu (alebo spustia
  read-only náhľad), nič sa nemení.
- **Bod obnovenia:** pred prvou DESTRUCTIVE akciou alebo akoukoľvek
  akciou z kategórie Oprava/Zabezpečenie sa raz za dávku vytvorí System
  Restore Point (best-effort; pri zlyhaní sa aplikácia opýta, či
  pokračovať).
- **undo.ps1:** akcie s vratným účinkom (napr. reset hosts súboru,
  zastavenie služieb, zmena plánu napájania) priebežne zapisujú svoje
  undo príkazy do `Backups/<run-id>/undo.ps1` — v opačnom (LIFO)
  poradí, takže skript sa dá spustiť ako celok. Zapisuje sa po každej
  úspešnej akcii, takže aj pri páde aplikácie súbor odráža reálny stav.
- **Audit log + report:** každá akcia sa zapisuje do
  `Logs/<run-id>/audit.jsonl` a po každej dávke sa generuje HTML report
  do `Reports/`.

## Štruktúra priečinkov

```
PortableFix/
  PortableFix.cmd        spúšťač
  main.py                vstupný bod
  portablefix/           aplikačný kód
  Modules/<id>/actions.yaml   deklaratívne katalógy akcií
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

Výstup: `App/` (PyInstaller onedir). Po buildе vygeneruj kontrolné
súčty: `python scripts/generate_sha256sums.py`.

## Manuálne kroky pred distribúciou

Tieto kroky vyžadujú zdroje mimo repozitára a robia sa ručne:

1. **Podpísanie kódu** — `App\PortableFix.exe` je podpísaný self-signed
   certifikátom (`CN=PortableFix Self-Signed`, verejná časť v
   `Data\PortableFix-SelfSigned.cer`). Na cieľovom počítači sa dá
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

## Vývoj

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ --ignore=tests/test_gui_main_window.py
python -m pytest tests/test_gui_main_window.py
```

GUI testy spúšťajú reálne PowerShell procesy; pri behu celého súboru
naraz sa môže objaviť prechodný natívny crash prostredia
(STATUS_STACK_BUFFER_OVERRUN) — nie je to chyba kódu, beh stačí
zopakovať alebo rozdeliť na menšie dávky.

## Známe obmedzenia

- Undo pokrýva len akcie so statickým vratným príkazom; DISM/SFC/chkdsk
  opravy sú z princípu nevratné (kryje ich bod obnovenia).
- Undo pri kombinovaných akciách (napr. zastavenie 4 služieb naraz) sa
  zapíše len pri plnom úspechu akcie.
- Bod obnovenia sa vytvára aj pri čisto diagnostických akciách z
  kategórií Oprava/Zabezpečenie (zámerné, konzervatívne správanie).
- `regsvr32`/`UsoClient` kroky v M05 hlásia úspech aj pri tichom
  zlyhaní (neblokujúce procesy).
