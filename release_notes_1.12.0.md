> **Dôležité - aktualizuj raz ručne.** Aktualizácia z appky vo verzii
> 1.11.4 a starších nemôže fungovať: appka spustila aktualizátor spôsobom,
> pri ktorom sa PowerShell ukončí skôr, než vykoná prvý riadok. Appka sa
> zavrie a nič sa nenainštaluje - bez chybového hlásenia. Opravené je to až
> v 1.12.0, a keďže aktualizáciu vykonáva vždy stará verzia, na 1.12.0 treba
> prejsť ručne: zavri appku a rozbaľ obsah priečinka `PortableFix` z
> `PortableFix-Portable.zip` do priečinka appky (súbory nahraď; zip
> neobsahuje `Data\settings.json`, takže nastavenia zostanú), alebo spusti
> `PortableFix-Setup.exe` do toho istého priečinka. Od 1.12.0 aktualizácia
> najprv overí, že sa naozaj spustila, a každú chybu ukáže na obrazovke.
> Záznamy o aktualizácii sú v `%TEMP%\PortableFixUpdate`.

> **Important - update once by hand.** The in-app update of 1.11.4 and
> older cannot work: the app started the updater in a way that makes
> PowerShell quit before it runs its first line. The app closes and
> nothing is installed - without any error. This is fixed in 1.12.0, and
> since an update is always carried out by the version being replaced,
> 1.12.0 has to be installed by hand: close the app and extract the
> contents of the `PortableFix` folder in `PortableFix-Portable.zip` into
> the app's folder (replace the files; the zip carries no
> `Data\settings.json`, so your settings stay), or run
> `PortableFix-Setup.exe` into the same folder. From 1.12.0 on, an update
> first confirms that it really started and shows every failure on
> screen. Update logs are in `%TEMP%\PortableFixUpdate`.

## Slovensky

### Aktualizácia

- **Príčina:** aktualizačný PowerShell sa spúšťal s príznakom
  `DETACHED_PROCESS`, pri ktorom nemá konzolu a ticho skončí. Teraz sa
  spúšťa rovnako ako každý iný PowerShell, ktorý appka používa.
- Stiahnutý balík sa ešte pred zatvorením appky rozbalí vedľa inštalácie
  (`_update_stage`) a overí: štruktúra, voľné miesto a každý súbor zo
  `SHA256SUMS`. Chybný balík skončí hlásením a inštalácie sa nedotkne.
- Appka sa zavrie až vtedy, keď aktualizátor potvrdí, že beží. Inak zostane
  otvorená a ukáže dôvod, kód ukončenia a výstup PowerShellu, priečinok so
  záznamami a odkaz na ručné stiahnutie; ďalší pokus použije už pripravený
  balík bez nového sťahovania.
- Aktualizačný skript je pevný text a všetky cesty číta zo samostatného
  súboru, takže cesty s `’`, `„`, `”`, `[ ]`, `$(` alebo diakritikou ho už
  nerozbijú.
- Aktualizátor čaká na oba procesy appky (až 10 minút), priečinky
  premenúva s opakovaním, pri zlyhaní vráti pôvodný stav a novú verziu
  spustí s čistým prostredím (už nie „Failed to load Python DLL“) z
  priečinka inštalácie.
- Kým beží dávka, report, test rýchlosti, winget alebo vytváranie bodu
  obnovenia, appka aktualizáciu odmietne a povie prečo.
- Z `Data\` sa inštalujú len `SHA256SUMS`, certifikát a `.gitkeep` -
  `settings.json` sa nikdy neprepíše.
- Appka spustená počas aktualizácie iba oznámi, že sa aktualizuje;
  prerušenú alebo nedokončenú aktualizáciu ohlási pri ďalšom štarte.
- Zatvorenie appky počas sťahovania ho čisto preruší. „Reštartovať ako
  administrátor“ počká, kým sa stará inštancia ukončí.
- Inštalátor: odkazy spúšťajú appku v priečinku inštalácie (nie v `App\`),
  už neobsahuje `settings.json` z počítača, na ktorom sa build robil, a
  preinštalovanie neprepíše tvoje nastavenia; odstráni aj zvyšky
  prerušených aktualizácií.
- Portable zip obsahuje ikonu okna. `PortableFix.cmd` odovzdá appke
  parametre a po nahradení počas aktualizácie sa už znova nečíta.
- Záznamy v `%TEMP%\PortableFixUpdate` čistenie temp súborov nemaže, appka
  odstráni tie staršie ako 14 dní; staré stiahnuté balíky upratuje.

### Ďalšie zlepšenia

- Spoľahlivejšie body obnovenia: 24-hodinový limit Windows sa pre ne
  dočasne zruší a potom vráti, zaznamená sa číslo bodu obnovenia a
  zatvorenie appky počká na bod, ktorý sa práve vytvára.
- Reporty: lokalizovaný HTML report, prehľad zlyhaných akcií a modulov,
  filtre, tlač/PDF, podrobnosti akcií a bohatší obraz systému pred a po;
  report sa zapisuje mimo GUI vlákna.
- Balík pre klienta jedným klikom (ZIP jedného behu), vlastné predvoľby,
  história behov na dashboarde, oznámenie o dokončení dávky, klávesové
  skratky.
- Deväť nových akcií v katalógu; akcie pri zlyhaní vracajú nenulový kód,
  undo pri sprísnení zabezpečenia vráti pôvodný stav, selektory nezávisia
  od jazyka Windows.
- Odinštalovanie programov a winget rešpektujú DRY-RUN a pýtajú si
  potvrdenie.
- Slovenská diakritika v celom rozhraní a katalógu, korektúra textov,
  podpora vysokého kontrastu a prístupnosti.

## English

### Updates

- **Root cause:** the update PowerShell was started with the
  `DETACHED_PROCESS` flag, under which it has no console and quietly
  exits. It is now started like every other PowerShell the app runs.
- The downloaded package is unpacked next to the install
  (`_update_stage`) and verified before the app closes: layout, free space
  and every file in `SHA256SUMS`. A bad package ends with a message and
  leaves the install untouched.
- The app closes only once the updater confirms it is running. Otherwise
  it stays open and shows the reason, PowerShell's exit code and output,
  the log folder and the manual download link; a retry reuses the prepared
  package without downloading again.
- The update script is fixed text that reads every path from a separate
  file, so paths with `’`, `„`, `”`, `[ ]`, `$(` or diacritics can no
  longer break it.
- The updater waits for both of the app's processes (up to 10 minutes),
  renames folders with retries, restores the old state on failure and
  starts the new version with a clean environment (no more "Failed to load
  Python DLL") from the install folder.
- While a batch, report, speed test, winget task or restore point is
  running, the app refuses to update and says why.
- From `Data\` only `SHA256SUMS`, the certificate and `.gitkeep` are
  installed - `settings.json` is never replaced.
- An app started during the update only says that it is updating; an
  interrupted or unfinished update is reported at the next start.
- Closing the app during a download stops it cleanly. "Restart as
  Administrator" waits for the old instance to exit.
- Installer: the shortcuts start the app in the install folder (not in
  `App\`), it no longer ships the build machine's `settings.json`, and a
  reinstall keeps your settings; it also removes leftovers of interrupted
  updates.
- The portable zip includes the window icon. `PortableFix.cmd` passes its
  arguments to the app and is never read again after an update replaced
  it.
- The logs in `%TEMP%\PortableFixUpdate` survive the temp cleanup; the app
  removes ones older than 14 days and cleans up old downloads.

### Other improvements

- More reliable restore points: Windows' 24-hour limit is lifted for them
  and put back afterwards, the restore point's sequence number is
  recorded, and closing the app waits for one being created.
- Reports: a localized HTML report, failed-action and per-module
  summaries, filters, print/PDF, action details and a richer before/after
  system snapshot; the report is written off the GUI thread.
- A one-click client handoff package (a ZIP of one run), custom presets,
  run history on the dashboard, a batch-finished notice, keyboard
  shortcuts.
- Nine new catalog actions; failing actions exit non-zero, hardening
  undos restore the previous state, selectors no longer depend on the
  Windows display language.
- The uninstaller and winget honour DRY-RUN and ask for confirmation.
- Slovak diacritics across the UI and the catalog, proofread texts, High
  Contrast and accessibility support.

Full history since v1.11.4: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.11.4...v1.12.0).
