# PortableFix – konkurenčná analýza 2026

*Stav k 24. 9. 2026. Nadväzuje na `docs/research/research-competitors.md`. Tá štúdia vznikla pri katalógu so 108 akciami. Neopakujem ju, idem hlbšie a zameriavam sa na novinky z rokov 2025–2026. Dnešný katalóg má 229 akcií v 22 moduloch.*

---

## Zhrnutie

- **Rozdiel oproti konkurencii už nie je v počte opráv, ale v tom, čo sa deje okolo nich.** 229 akcií pokrýva šírku, akú nemá žiadny bezplatný nástroj. Všetky vážne medzery (32 potvrdených) sú v kvalite verdiktu, v práci s jednotlivými položkami (parametre, výber položiek), v návrate po reštarte a v dôkazoch pre klienta. Nový „fix“ ako taký chýba len výnimočne.
- **Najnaliehavejšie: Secure Boot 2023 (G07).** Certifikát Windows Production PCA 2011 vyprší **19. 10. 2026**, teda o menej ako 4 týždne. KEK a UEFI CA 2011 expirovali v júni 2026. `boot_tpm_status` dnes vypíše „SecureBoot enabled: True“, takže zaseknutý stroj vyzerá zdravo. Je to malá SAFE akcia s vysokou hodnotou. Komunitné nástroje (CheckCA2023, Detect-SecureBootCertUpdateStatus) zobrazujú surové panely. Jednoznačný verdikt by bol **náskok PortableFix**, nie dobiehanie konkurencie.
- **Bezpečnostná chyba v čistení (G22).** BleachBit mal v roku 2026 CVE-2026-55567 (CVSS 7.8): zvýšené mazanie sa dalo cez junction alebo symlink presmerovať mimo temp. Čistiace akcie m02 v PortableFix majú rovnaký vzor (`Get-ChildItem | Remove-Item -Recurse` ako admin nad priečinkami, do ktorých môže používateľ zapisovať). Opraviť ako prvé.
- **Skóre na dashboarde je nečestné (G02).** Počíta sa ako `max(40, 100 − 10 × počet odporúčaní)`, takže po analýze vychádza prakticky 100 alebo 90. Konkurencia s dôveryhodnosťou (CrystalDiskInfo, NinjaOne) dáva **verdikt pre každú oblasť aj s dôvodom**. Scareware konkurencia (Fortect, IObit) robí presný opak a práve na tom môžeme stavať marketing.
- **Disk: chýba brána „najprv image“ (G13).** `chkdsk /f /r`, defrag, DISM a hlboké čistenia sa spúšťajú bez kontroly SMART. Na umierajúcom disku to môže zničiť dáta klienta. Manuál ddrescue aj Tron to riešia explicitne.
- **Sila, ktorú treba chrániť:** dry-run ako predvolený režim, 4 úrovne rizika, bod obnovy s obídením 24 h limitu, LIFO `undo.ps1`, JSONL audit a dvojjazyčný HTML/JSON report s handoff ZIP. Túto kombináciu **nemá žiadny** z viac ako 120 preskúmaných nástrojov. Tron má dry-run, WinUtil, Win11Debloat a Sophia majú undo. Nikto nemá všetko naraz a nikto nemá klientsky report zadarmo.
- **Pozor na nadhodnotené medzery.** Pôvodná hypotéza G01 („Uninstaller a winget obchádzajú dry-run, potvrdenie a audit“) sa pri overení v kóde **ukázala ako nepravdivá**. Tieto panely dry-run, potvrdenie aj audit majú, osirelé kľúče sa zálohujú do `.reg`. Reálne chýba len bod obnovy, zoznam chránených aplikácií a výzva na zatvorenie bežiacich programov. Podobne G30 (porovnanie návštev) už čiastočne existuje.
- **Top odporúčania na najbližší mesiac:** G07 Secure Boot verdikt, G22 bezpečné mazanie, G24 + G01 bod obnovy podľa efektu akcie a záloha hive, G11 pre-flight brána, G12 jedna revízna obrazovka namiesto 8 dialógov, G16 robustný winget (dnes tichý falošný „všetko OK“ na lokalizovanom Windows), G18 WinRE/QMR stav, G33 tichý režim (ping na 8.8.8.8 každé 4 s).
- **Architektonické investície na neskôr:** G05 (výber položiek a parametre), G10 (deklaratívne registry a služby s undo na pôvodnú hodnotu) a G02 (štruktúrované findings). Odomykajú polovicu ostatných medzier: Autoruns-disable, tlačiarne, drift, CLI.
- **Čo nekopírovať:** registry cleanery, nafúknuté skóre, databázy generických ovládačov, `irm | iex`, rezidentné agenty a telemetriu, bezpečnostné downgrady ako predvolenú cestu (populárny printer fixer vypína SMB signing a zapína guest prístup) ani hodinové „fix everything“ behy.

---

## Metodika

**Rozsah.** Preskúmali sme zhruba 120 unikátnych nástrojov (niektoré ako WinUtil, Tron, HWiNFO či AIDA64 sa objavili vo viacerých kategóriách) v 11 kategóriách:

1. repair suites (all-in-one opravné balíky),
2. debloat a tweak nástroje,
3. cleanery a optimizéry,
4. záchranné prostredia a launchery,
5. second-opinion skenery a nástroje na perzistenciu,
6. aktualizácie, odinštalácia a ovládače,
7. HW diagnostika,
8. reporting a RMM/PSA,
9. zálohy, imaging a migrácia,
10. Secure Boot, boot a obnova, ESU a tlač,
11. AI a MCP rozhrania.

**Zdroje.** Primárne GitHub repozitáre, README a changelogy, winget, Scoop a Chocolatey manifesty, releases.atom, oficiálne dokumenty Microsoftu (cez GitHub mirrory MicrosoftDocs) a advisories (GHSA). Niekoľko vendor webov (ninjaone.com, atera.com, syncromsp.com, 0patch.com, eset.com, clonezilla.org, macrium.com, windows-repair-toolbox.com) blokovala proxy výskumného prostredia. Údaje o nich sú preto z druhotných zdrojov alebo sú označené ako **neoverené**.

**Dvojité overenie každej medzery.** Každá kandidátna medzera prešla dvoma nezávislými kontrolami:

1. **missing_check**, teda naozaj to v PortableFix chýba? Prehľadal sa kód (`portablefix/*.py`, `portablefix/gui/main_window.py`, `Modules/*/actions.yaml`, README, release notes). Ak funkcia čiastočne existuje, uvádzam to v časti „Stav v PortableFix“.
2. **claim_check**, teda robí to konkurencia naozaj tak, ako tvrdíme? Tvrdenie sa overilo proti citovanému zdroju. Kde sa zdroj nepodarilo stiahnuť, je tvrdenie označené ako *neoverené* a opravené podľa najlepšieho vedomia.

Medzera je **CONFIRMED**, len ak prešli obe kontroly. Opravy z oboch kontrol sú zapracované priamo do textu, takže popis konkurencie je miestami skromnejší než pôvodná hypotéza. Jedna položka (G30) neprešla prvou kontrolou a je v Prílohe A. Tri tvrdenia som navyše osobne overil v kóde počas písania:

- `RestorePointRunner(` je v `main_window.py` len raz (riadok 2856),
- skóre `max(40, 100 - unique_recommended * 10)` je na riadku 2146,
- ping timer beží každé 4 s na 8.8.8.8 (`main_window.py` 3183–3194, `sysinfo.py` 348 a 477) a VPN kontrola každých 60 s.

V repozitári tiež nie je súbor `LICENSE`.

**Priorita.** `skóre = hodnota (1–5) ÷ náročnosť (S = 1, M = 2, L = 3)`. Pri rovnosti rozhoduje nižšie riziko a potom naliehavosť (termíny ako 19. 10. alebo 13. 10. 2026, bezpečnostné chyby). V roadmape sa poradie mierne upravuje podľa závislostí: G05 → G04, G02 → G13 a G28, G25 → G29 a G31.

---

## Prehľad konkurentov

Legenda stavu: **Aktívny** = release v roku 2026; **Útlm** = posledný release 2023–2025 alebo aktualizujú sa len definície; **Ukončený**; **?** = neoverené.

### 1. Repair suites

| Nástroj | Kategória | Licencia / cena | Portable | Stav | V čom vyniká |
|---|---|---|---|---|---|
| Tweaking.com Windows Repair | all-in-one oprava | zadarmo len osobne; Pro/Technician ročne (~$25–65) | áno (4.14.0) | Útlm (posledná verzia 06/2023, fórum zrušené, falošné „2026“ zrkadlá na GitHube) | lineárny sprievodca: najprv chkdsk, SFC a záloha, až potom opravy; 3 úrovne dôvery |
| FixWin 11 | jednoklikové opravy | freeware | áno | Útlm (11.2, 03/2025) | „?“ pri každej oprave; dvojklik skopíruje príkaz |
| Windows Repair Toolbox | launcher nástrojov | freeware | áno (~7 MB, sťahuje nástroje na požiadanie) | Aktívny (3.0.4.8; definície 09/2026) – *zdroje si odporujú* | panel systémových informácií stále na očiach; nástroje zoskupené podľa práce, nie podľa vendora |
| Tron | automatizovaný skript | MIT + nástroje tretích strán | áno | Útlm (engine v12.0.9 01/2025; zoznamy 09/2026) | „spusti a odíď“; pokračovanie po reštarte cez RunOnce; exit kódy; Stage 8 vlastné skripty |
| d7x / dSupportSuite | platforma pre dielne | predplatné na technika | USB/share (?) | ? (posledné verejné poznámky 2023) | klikateľné alerty otvoria nástroj na opravu; profily seniora spustí aj junior |
| MS Get Help / GetHelpCmd (ex-SaRA) | oficiálna diagnostika | zadarmo | build platí len 90 dní | Aktívny; SaRA CLI odstránené 03/2026, MSDT odstránené | najprv súhlas, potom zmena; CLI pre RMM |
| UVK (Carifred) | oprava a malware | free + jednorazové licencie, branding od ~$50 | áno | Aktívny (11.10.28, 09/2026) | log → fix skript; fronta sa dá upravovať počas behu |
| Fortect | spotrebiteľská „oprava“ | freemium, ~$34–59/rok, obnova drahšia | nie | Aktívny | lievik „scan → počet problémov → zaplať“ (anti-vzor) |
| BAIOS 4.x | orchestrátor pre technikov | GPL-3 | áno (.NET 8) | Aktívny | katalóg second-opinion nástrojov so stavom (aktuálny / opustený) |

### 2. Debloat a tweak

| Nástroj | Kategória | Licencia / cena | Portable | Stav | V čom vyniká |
|---|---|---|---|---|---|
| Chris Titus WinUtil | inštalácie + tweaky + opravy | MIT (platený .NET variant) | bez inštalácie, ale `irm \| iex` | Aktívny (26.08.19); Win10 vypustené | „Get Installed Tweaks“ zaškrtne už aplikované; JSON kľúč v tooltipe; dokumentácia pre každý tweak |
| Win11Debloat | debloat pre nasadenie | MIT | áno (ZIP) | Aktívny (2026.08.24) | záverečná karta „Review Changes“; detekcia aplikovaných tweakov; záloha registrov pred zmenou (od 2026.05.10); `-User`, `-Sysprep` |
| Sophia Script | PS modul, 150+ funkcií | MIT | áno | Aktívny (7.3.0, 09/2026) | preflight: odhalí AtlasOS, Tron, RemoveWindowsAI a pozastavený BitLocker |
| O&O ShutUp10++ | privacy prepínače | free + Premium od 05/2026 ($19.90) | Free áno | Aktívny (3.5.1130) | semafor odporúčaní; „Undo all“ |
| Ultimate Windows Tweaker 5 | GUI tweaker | freeware | áno | Útlm | „Restore Defaults“ pre každú stránku |
| Winaero Tweaker | hlboká customizácia | freeware | voliteľne | Aktívny (1.65, 02/2026) | pri každom tweaku odkaz „More info“ na článok |
| Optimizer → OptimizerNXT | tweaker → podpísané YAML | GPL-3 | áno | Optimizer ukončený; NXT 1.0.1 (01/2026), odvtedy ticho | spúšťa iba YAML s platným `.sig` |
| privacy.sexy | generátor skriptov | AGPL-3 | web áno | Útlm (0.13.8, 03/2025) | živý panel s kódom; úrovne Standard/Strict |
| Winhance | debloat GUI | PolyForm Shield | áno | Aktívny (26.06.12) | aktuálna, odporúčaná a predvolená hodnota; revízia „N of M reviewed“; banner pri inom elevovanom účte |
| Flyoobe | post-install sprievodca | MIT | ZIP | Aktívny (3.02.72, 09/2026) | preflight akcie; zobrazí len to, čo si žiada pozornosť |
| RemoveWindowsAI | odstránenie AI komponentov | MIT | skript | Aktívny | granulárne voľby (Sophia ho označuje za „harmful“) |

### 3. Cleanery a optimizéry

| Nástroj | Kategória | Licencia / cena | Portable | Stav | V čom vyniká |
|---|---|---|---|---|---|
| CCleaner 7 / Technician Ed. | komerčný cleaner | freemium; Pro ~$45/rok; Technician na technika | 7.x **nie** (≈7 služieb); portable len 6.x | Aktívny (7.11, 6.41) | Health Check vs Custom Clean; zoznam cookies, ktoré sa zachovajú |
| BleachBit | open-source cleaner | GPL-3 | áno | Aktívny (6.0.4, 09/2026) | náhľad = presne to, čo sa zmaže; od 6.0.2 všetky profily Chrome/Edge; CVE-2026-55567 opravené |
| Wise Care 365 | komerčný cleaner | freemium ~$30/rok | áno („Make Portable“) | Aktívny (8.0.6) | Activity Timeline (boot, USB, aplikácie) |
| Glary Utilities 6 | suite | free len nekomerčne; Pro $40/rok | áno | Aktívny (6.48) | „Delay“ pri položkách autoštartu; komunitné hodnotenia |
| Ashampoo WinOptimizer | suite | jednorazovo ~$30–50 | **nie**, opakovaná online kontrola licencie | Aktívny | pohľad podľa úlohy aj podľa modulu |
| IObit Advanced SystemCare 19 | „booster“ | predplatné ~$30/rok | nie | Aktívny | Rescue Center s detailom zmien (inak anti-vzor) |
| Microsoft PC Manager | first-party cleaner | zadarmo | nie (Store, regionálne obmedzený; SK pravdepodobne nie) | Aktívny | Deep Cleanup: jeden zoznam na zaškrtnutie |
| Windows Storage Sense | vstavané čistenie | súčasť Windows | – | Aktívny | nastavíš raz a čistí sám, bez agenta |
| PrivaZer | stopy a súkromie | zadarmo aj komerčne | áno | Aktívny (4.0.126) | úvodná otázka „čo chceš urobiť?“ |

### 4. Záchranné prostredia a launchery

| Nástroj | Kategória | Licencia / cena | Portable | Stav | V čom vyniká |
|---|---|---|---|---|---|
| Hiren's BootCD PE | WinPE | zadarmo | bootovateľné ISO 3 GB | Útlm (v1.0.8, obsah ~2024) | známy Win11 desktop, automatické ovládače |
| Medicat USB | Ventoy multiboot | AGPL inštalátor, obsah šedá zóna | USB 30 GB+ | obsah 21.12 (2021); inštalátor aktívny | „Verify“ stick opraví len poškodené súbory |
| NirLauncher | launcher | freeware | áno | Aktívny (1.30.25) | jednotné list-view a HTML reporty naprieč 200 nástrojmi |
| Sysinternals Suite / Live | expertné nástroje | Sysinternals licencia (**zákaz redistribúcie**) | áno, aj `\\live.sysinternals.com` | Aktívny (09/2026; ProcExp už len Win11+) | vždy aktuálne nástroje z UNC cesty |
| PortableApps.com | platforma | OSS | áno | Aktívny (30.5) | bezpečné vysunutie USB |
| Ventoy | multiboot bootloader | GPL-3 | – | Aktívny (1.1.17; shim pre CA 2023) | drag & drop ISO |

### 5. Second-opinion skenery, perzistencia, procesy

| Nástroj | Kategória | Licencia / cena | Portable | Stav | V čom vyniká |
|---|---|---|---|---|---|
| Malwarebytes AdwCleaner | adware/PUP | freeware (komerčné použitie ?) | áno, `/path`, `/uninstall` | Aktívny (8.8.1) | `/scan` a `/clean` oddelene, karanténa |
| Emsisoft Emergency Kit | dual-engine skener | freeware | áno | ? (posledný winget 2025.7) | GUI + CLI v jednom priečinku |
| Kaspersky KVRT | disinfekcia | zadarmo; problém v USA | áno (~114 MB, „latest“ URL) | Aktívny | žiadny krok aktualizácie, každé stiahnutie je čerstvé |
| ESET Online Scanner | cloudový skener | zadarmo jednorazovo | čiastočne (stub) | Aktívny | voľba PUA hneď na začiatku |
| HitmanPro | behaviorálny skener | scan zadarmo, odstránenie platené | áno | ? | rýchly cloudový sken |
| RogueKiller | perzistencia, PUM | Free/Premium/Technician | Technician + CLI | Aktívny (?) | skenuje aj „následky“ (politiky, proxy, hosts) |
| Sysinternals Autoruns | autoštart | Sysinternals licencia | áno | Aktívny (14.3, 06/2026) | ~17–20 kategórií; „Hide Microsoft entries“; VT stĺpec; reverzibilný checkbox; `.arn` porovnanie |
| Process Explorer | procesy | Sysinternals licencia | áno | Aktívny (17.14; **už len Win11+**) | farebné riadky, suspend pred kill |
| Microsoft Safety Scanner | Defender engine | zadarmo | áno (expiruje po 10 dňoch) | Aktívny | `/N` len detekcia, potom `/F:Y` |
| FRST | forenzný log + fixlist | freeware | áno | Aktívny | scan → vložený fixlist → Fixlog (parametrizácia) |
| System Informer | procesy/siete | MIT | áno (nastavenia na USB) | Aktívny | open source, auditovateľný |
| Malwarebytes Toolset | technický toolkit | platený | áno | Aktívny (?) | orientácia na účtovanie dielne |
| ESET SysRescue Live | bootovací AV | zadarmo | ISO | **Ukončený** (EOL 09/2023, ?) | – |
| Kaspersky Rescue Disk 18 | bootovací AV | zadarmo (problém v USA) | ISO | Útlm (build ~2022) | offline second opinion |

### 6. Aktualizácie, odinštalácia, ovládače

| Nástroj | Kategória | Licencia / cena | Portable | Stav | V čom vyniká |
|---|---|---|---|---|---|
| UniGetUI (Devolutions) | GUI pre správcov balíkov | MIT | áno (marker súbor) | Aktívny (v2026.3.0) | jeden zoznam zo všetkých správcov; história operácií; CLI s exit kódmi; skip version/ignore (*nie všetko overené*) |
| Patch My PC Home Updater | aktualizácie z katalógu | free, closed | 5.x MSI (+ portable ?) | Aktívny (5.4.5) | bod obnovy pred aktualizáciou; farebný stav |
| Ninite / Ninite Pro | hromadná inštalácia | free doma; Pro od ~$35/mes. | generovaný exe | ? | úplne bez klikania |
| Revo Uninstaller | odinštalácia + zvyšky | Free / Pro / Pro Portable | Free áno | Aktívny (Pro 5.5.2) | Hunter mode; bod obnovy + záloha registrov |
| Bulk Crap Uninstaller | hromadná odinštalácia | Apache-2.0 | áno | Aktívny (v6.3, 09/2026) | tiché príkazy pre MSI, NSIS a Inno; hodnotené zvyšky; `.reg` záloha; kôš; BCU-console |
| Snappy Driver Installer Origin | offline driver packy | GPL-3 | áno | Aktívny (2.0.4) | 3 jasné voľby na úvod (inak anti-vzor: repackované packy) |
| Driver Store Explorer (RAPR) | driver store | GPL-2, podpis cez SignPath | áno | Aktívny (1.0.26) | jeden klik vyberie staré ovládače, pred mazaním revízia |
| Display Driver Uninstaller | čisté odstránenie GPU/audio | free | áno | Aktívny (18.1.6) | 3 veľké tlačidlá, odporúčané označené |
| Winget-AutoUpdate | agent na aktualizácie | MIT | nie (MSI + SYSTEM tasky) | Aktívny (2.12, 3.0 pre) | preskakuje „Unknown“ verzie; block/allow listy |

### 7. HW diagnostika

| Nástroj | Kategória | Licencia / cena | Portable | Stav | V čom vyniká |
|---|---|---|---|---|---|
| HWiNFO | info + senzory | free nekomerčne; Pro | áno | Aktívny (8.52) | Min/Max/Avg + throttling; WHEA počítadlá |
| CrystalDiskInfo | SMART | MIT | áno | Aktívny (9.9.2) | Good/Caution/Bad/Unknown podľa auditovateľných pravidiel (05, C5, C6, NVMe); „Hide Serial“ |
| CrystalDiskMark | benchmark disku | MIT | áno | Aktívny (9.0.3) | uloženie výsledku ako obrázok |
| AIDA64 Extreme/Engineer | audit, stres | platené; Engineer pre technikov | áno | Aktívny (8.40) | report wizard s profilmi; licencia putuje na USB |
| Speccy | jednoduché info | freeware | cez Scoop | Útlm | najčitateľnejší súhrn pre klienta |
| OCCT | stres test | free osobne | áno | Aktívny (17.1.5) | automatické zastavenie pri teplote alebo chybe |
| BlueScreenView | minidumpy | freeware | áno | Útlm (2015) | nulová konfigurácia |
| WhoCrashed | analýza pádov | Home free osobne; Pro | nie | Útlm (7.10) | vysvetlenie v bežnej reči a označenie vinného ovládača |
| Reliability Monitor + `perfmon /report` | vstavané | Windows | – | Aktívny | denný index stability 1–10 s časovou osou |
| Windows Memory Diagnostic | vstavaný RAM test | Windows | – | Aktívny | žiadne bootovacie médium |
| MemTest86 (PassMark) | bootovací RAM test | Free/Pro/Site | USB | ? | report na stick |
| Memtest86+ | bootovací RAM test | GPL-2 | USB | Aktívny (8.10) | legálne na technikovom USB |
| BatteryInfoView | batéria | freeware | áno | Aktívny (1.27) | návrhová vs plná kapacita, cykly |
| LibreHardwareMonitor | senzory (knižnica) | MPL-2 | áno | Aktívny (0.9.6) | **PortableFix ho už používa** |
| Hard Disk Sentinel | zdravie disku | shareware | ? | Aktívny (6.40) | percento zdravia + zrozumiteľný odsek |
| smartmontools | SMART engine | GPL-2 | smartctl.exe | Aktívny (7.5) | strojovo čitateľný výstup (JSON) |

### 8. Reporting, RMM/PSA, dielne

| Nástroj | Kategória | Licencia / cena | Portable | Stav | V čom vyniká |
|---|---|---|---|---|---|
| NinjaOne | RMM | SaaS za zariadenie | nie (agent) | Aktívny | HEALTHY/NEEDS_ATTENTION/UNHEALTHY + počítadlá |
| Atera | RMM/PSA + AI Copilot | SaaS za technika (~$129+) | nie | Aktívny (?) | cena za technika; AI súhrn relácie |
| Syncro | PSA/RMM | SaaS za technika (?) | nie | Aktívny (?) | tiket = checklist, timer, prílohy aj faktúra |
| RepairShopr / RepairDesk | tiketing dielne | SaaS | nie | Aktívny (?) | intake/outtake s podpisom klienta |
| Tactical RMM | RMM | source-available; reporting od $80/mes. | nie | Aktívny | výsledok skriptu → vlastné pole → šablóna reportu; logo |
| ConnectWise Sidekick / ScreenConnect | RMM + AI | SaaS | nie | Aktívny | AI generované skripty |
| TeamViewer / LogMeIn Resolve / Datto (AI) | AI v relácii | SaaS doplnky | nie | Aktívny | jeden záznam → interná poznámka + súhrn pre klienta |
| Belarc Advisor | audit PC | free len osobne | nie | Aktívny (13.1) | bezpečnostný súhrn hore v reporte |
| powercfg reporty | vstavané | Windows | – | Aktívny | `/batteryreport`, `/sleepstudy`, `/XML` |
| MSInfo32 / systeminfo | vstavaný inventár | Windows | – | Aktívny | `.nfo` otvorí každý technik |

### 9. Zálohy, imaging, záchrana dát, migrácia

| Nástroj | Kategória | Licencia / cena | Portable | Stav | V čom vyniká |
|---|---|---|---|---|---|
| Rescuezilla | imaging GUI | GPL-3 | bootovacie ISO | Aktívny (2.6.2) | sprievodca v štýle Macrium |
| Clonezilla live | imaging TUI | GPL | ISO | Aktívny (3.3.3-37) | voliteľné checksumy image aj súborov; Info-*.txt v každom image |
| Disk2vhd | P2V za behu (VSS) | Sysinternals | áno | Útlm (2.02, 2021) | bez reštartu; nezvláda BitLocker |
| Macrium Reflect X | imaging | len predplatné; **Free ukončený** | nie | Aktívny | – |
| Veeam Agent FREE | záloha | freeware | nie | Aktívny (13.1.1) | enterprise spoľahlivosť zadarmo |
| Hasleo Backup Suite Free | záloha/klon | freeware | nie | ? (5.9.2, 2025) | náhrada za Macrium Free |
| wbadmin | vstavaný image | Windows | – | Legacy | nič netreba nosiť |
| GNU ddrescue (+ ddrescueview) | záchrana dát z chybného disku | GPL | live CD | Aktívny (1.30) | mapfile; „nikdy neopravuj FS na disku s I/O chybami“ |
| HDDSuperClone | pokročilý klon | GPL-2 | live CD | Komunitný (08/2026) | head-skipping |
| TestDisk / PhotoRec | partície, carving | GPL | áno | Aktívny (7.3-WIP) | `/log` ako dôkaz |
| DMDE | obnova dát | free do 4000 súborov | áno | Aktívny (4.4.4) | – |
| ForensiT Transwiz | migrácia profilu | freeware | extrahovateľné | ? | 2-krokový sprievodca |
| Microsoft USMT | migrácia stavu | súčasť ADK | z USB (?) | Aktívny | odmietne C:\ ako úložisko, `/uel`, `/vsc` |
| Laplink PCmover | migrácia aj aplikácií | ~$60 | nie | Aktívny | presun aplikácií |
| Windows Backup for Organizations | cloudová záloha nastavení | Windows + MDM | – | GA 08/2025 | obnova pri prvom prihlásení |
| Windows Backup (PC-to-PC) | spotrebiteľská migrácia | Windows | – | nové (2025) | v OOBE |
| MigrationMerlin | USMT wrapper | MIT | skripty | Aktívny (26.6) | dry-run s presným príkazom; verifikačný report |

### 10. Secure Boot, boot a obnova, ESU, tlač

| Nástroj | Kategória | Licencia / cena | Portable | Stav | V čom vyniká |
|---|---|---|---|---|---|
| CheckCA2023 | CA 2023 checker (WPF) | MIT | áno (.ps1) | Aktívny (1.6.0, 05/2026) | dekóduje 13 bitov AvailableUpdates, db/KEK, udalosti TPM-WMI, spustí task |
| Check-UEFISecureBootVariables | audit premenných | **bez licencie** | áno | Aktívny (09/2026) | kontrola externých boot médií |
| Detect-SecureBootCertUpdateStatus (MS) | oficiálny status | MIT; na nových buildoch priamo vo Windows | áno | Aktívny (v1.5) | PASS / ACTION NEEDED + príkaz ako ďalší krok |
| Fleet secureboot extension | osquery klasifikátor | ? | nie | Aktívny (05/2026) | ~15 signálov → jedno slovo + ďalší krok |
| WindowsPrinterSharingFix (khairudinfahmi) | printer/SMB fixer | GPL-3 | čiastočne | Aktívny (2.4.1) | stavový banner; PrintBRM; **anti-vzor: security downgrady** |
| Windows-Printer-Sharing-Fix v4 (man612) | diagnostika tlače | MIT | áno | Aktívny (4.2.0) | „START HERE: Diagnose“; V3/V4/IPP triedy; WPP readiness; vrstvený test UNC |
| ConsumerESU (abbodi1406) | ESU stav/enrollment | public domain | áno | Aktívny (01/2026) | dekódovanie ESUEligibility |
| 0patch | micropatche pre Win10 | Free/Pro/Ent (?) | nie (agent) | ? | patch bez reštartu |
| WinRE + offline oprava | vstavané | Windows | – | Aktívny | SrtTrail.txt |
| Quick Machine Recovery | cloudová obnova | Win11 24H2+ | – | Aktívny | na Pro/Enterprise predvolene vypnuté |
| Defender Offline (`Start-MpWDOScan`) | boot-time sken | Windows | – | Aktívny | jeden cmdlet |
| MS CrowdStrike Recovery Tool | incidentné WinPE | zadarmo | ISO/USB | jednorazový (2024) | bootuje rovno do opravy |
| Microsoft LiveRE | živý OS na USB | zadarmo (?) | USB 8 GB | ? (doc 02/2026) | PowerShell + PSSession offline |
| DaRT 10 | offline sada | len MDOP/SA | WinRE image | Legacy | Solution Wizard (otázky → nástroj) |
| PITR / Cloud Rebuild | rollback celého stroja | Win11 (+Intune) | – | PITR GA 06/2026 (podľa jedného zdroja) | undo celého stroja |
| Miracle Boot | komunitná boot oprava | bez licencie | áno | 01/2026 | jeden nástroj pre live Windows aj WinRE |
| Komunitné WinRE/QMR nástroje | detekcia WinRE/QMR | OSS | skripty | Aktívny | zelené/červené zhrnutie + Fix All |

### 11. AI a MCP

| Nástroj | Kategória | Licencia / cena | Portable | Stav | V čom vyniká |
|---|---|---|---|---|---|
| Agent in Settings (Win11) | NL → nastavenie | súčasť OS | – | Aktívny, len Copilot+ PC, bez SK (?) | Apply + Undo priamo v Settings |
| Windows ODR (MCP registry) | OS plumbing | súčasť OS | – | preview | jeden register nástrojov, Intune kontrola |
| Windows-MCP | LLM ↔ Windows | MIT | čiastočne | Aktívny (0.8.5) | **anti-vzor:** surový shell bez náhľadu a undo |
| mcp-windbg | AI nad CDB/KD | MIT | nie | Aktívny (1.3.0) | „ktorý ovládač spôsobil bugcheck?“ |
| PowerShell.MCP | PS MCP server | MIT | nie | Aktívny | človek vidí príkazy živo |
| MSP Skills (Servosity) | MCP pre MSP nástroje | OSS | nie | Aktívny | politika read/write/destructive pre každý konektor |
| MCP OS Doctor a podobné | malé diag MCP | OSS | – | malé projekty | `get_capabilities` hlási elevation |

---

## Čo majú oni a my nie

Zoradené podľa priority (skóre = hodnota ÷ náročnosť; pri rovnosti nižšie riziko a naliehavosť). **Hodnota** je 1–5, **náročnosť** S/M/L, **riziko** 1–3 (riziko implementácie a toho, že zmena niečo pokazí u klienta). Každá položka prešla oboma kontrolami. Kde kontrola zúžila tvrdenie, je to uvedené v riadku *Oprava po overení*.

### 1. G07 – Stav a verdikt pre Secure Boot certifikáty 2023 · skóre 5,0
- **Čo robia a kto:** CheckCA2023 dekóduje všetkých 13 bitov `AvailableUpdates`, `UEFICA2023Status` a `WindowsUEFICA2023Capable`. Číta db, KEK a ich Default varianty z firmvéru, ukazuje udalosti TPM-WMI a vie spustiť a sledovať task Secure-Boot-Update. Microsoft Detect-SecureBootCertUpdateStatus (na novších buildoch priamo vo Windows) končí riadkom PASS / ACTION NEEDED. Fleet extension skladá ~15 signálov do jedného slova.
- **Oprava po overení:** CheckCA2023 **nedáva** jeden súhrnný verdikt, ukazuje viac panelov. Päťstavová taxonómia (Updated, RebootPending, BlockedOEMMissingKEK, ServicingBroken, WaitingOnRollout) je náš návrh, nie existujúca funkcia konkurencie. Jednoznačný verdikt je teda príležitosť na **náskok**.
- **Stav v PortableFix:** `boot_tpm_status` v m15 spúšťa len `Get-Tpm` a `Confirm-SecureBootUEFI`. V repozitári nie je žiadna zmienka o UEFICA2023, AvailableUpdates, KEK, udalostiach 1795–1808 ani o tasku.
- **Prečo to technikovi pomôže:** KEK a UEFI CA 2011 expirovali v júni 2026 a Windows Production PCA 2011 expiruje 19. 10. 2026. Stroj, ktorý sa zasekol (OEM chýba KEK, servicing je rozbitý), dnes vyzerá zdravo. Klient sa to dozvie až pri probléme s bootom po revokácii.
- **Návrh:** SAFE `boot_secureboot_ca2023_status` číta `HKLM\SYSTEM\CurrentControlSet\Control\SecureBoot\AvailableUpdates` a `Servicing\*`. Hľadá „Windows UEFI CA 2023“ v `Get-SecureBootUEFI db/KEK`, číta SetupMode, poslednú udalosť TPM-WMI, stav tasku a OEM, model a dátum BIOS-u. Výsledok je jeden verdikt podľa rebríčka „prvá zhoda vyhráva“ (1803 = blokujúce) a odporúčaný ďalší krok. Ak existuje `C:\Windows\SecureBoot\Scripts\Detect-SecureBootCertUpdateStatus.ps1`, pribudne druhá akcia, ktorá ho spustí. Voliteľne MODERATE apply akcia (`0x5944` + spustenie tasku), vylúčená zo select-all:
  - odmietne bežať bez ochrany BitLocker recovery-password,
  - uloží predchádzajúcu hodnotu,
  - **nikdy** nenastaví revokačné bity 0x80, 0x200 ani 0x400.
- **Náročnosť:** S · **Riziko:** 1 (status); apply akcia 2 · **Hodnota:** 5
- **Zdroje:** github.com/claude-boucher/CheckCA2023 · github.com/Azure/azure-support-scripts/…/SecureBootCertCheck/readme.md · github.com/allenhouchins/fleet-extensions/…/secureboot_cert_update/README.md · github.com/fleetdm/fleet/…/microsoft-is-rotating-every-windows-pcs-secure-boot-keys.md

### 2. G22 – Odolnosť zvýšeného mazania voči junction a symlink presmerovaniu · skóre 4,0 · bezpečnosť
- **Stav implementácie: hotovo.** Všetky katalógové akcie, ktoré mažú strom priečinkov (10 v m02, `wu_reset_cache` v m05, `print_reset_print_system` v m14), používajú vložený pomocník `Remove-PfSafe`: strom prechádza sám, reparse point zmaže len ako odkaz a nikdy doň nevojde. `tests/test_safe_delete.py` bráni návratu surového `Remove-Item -Recurse` do katalógu a spúšťa skutočné príkazy nad stromom s podstrčeným symlinkom (na Windows aj junction). Zostáva: pretek medzi kontrolou a zmazaním (vyžaduje mazanie cez handle), `takeown /R`/`icacls /T` vo vnútri priečinka, ktorý nie je odkazom, a `Remove-WithRetry -Recurse` v skripte aktualizácie (`portablefix/update_swap_script.py`).
- **Čo robia a kto:** BleachBit 6.0.1 opravil CVE-2026-55567 (CVSS 7.8). Štandardný používateľ mohol cez podstrčený junction alebo symlink presmerovať elevované mazanie na ľubovoľný súbor a v kombinácii s Windows Installer eskalovať na SYSTEM.
- **Oprava po overení:** Advisory potvrdzuje, že príčinou bolo chýbajúce zamykanie počas mazania. Presný spôsob opravy („zamkne a overí rodičovský priečinok“) z advisory priamo nevyplýva.
- **Stav v PortableFix:** `system_temp`, `user_temp` a `browser_cache_sweep` v m02 posielajú deti priečinkov `$env:TEMP` a `$env:WINDIR\Temp` rovno do `Remove-Item -Recurse -Force` bez kontroly ReparsePoint. `paths.compute_temp_protected_child` rieši len to, keď je samotný %TEMP% junction (ochrana pred zmazaním seba), nie položky vnútri stromu.
- **Prečo to technikovi pomôže:** PortableFix beží ako admin na cudzích, často infikovaných PC. Nástroj pre technikov nesmie byť vektorom eskalácie. Je to aj reputačné riziko.
- **Návrh:** zdieľaný PS prelude (ako `$__pfProtect`) s funkciou `Remove-PfSafe`:
  - enumeruje bez nasledovania ReparsePoint,
  - reparse point odstráni len ako samotný link (`[IO.Directory]::Delete` na link),
  - nikdy cez neho nerekurzuje,
  - odmietne pokračovať, ak koreň rezolvuje inam.
  
  K tomu CI test na `windows-latest`, ktorý podstrčí junction a symlink mimo temp a overí, že cieľ prežije.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 4
- **Zdroje:** github.com/bleachbit/bleachbit/security/advisories/GHSA-vcjw-px28-5w94 · osv.dev/vulnerability/CVE-2026-55567

### 3. G24 – Bod obnovy podľa efektu akcie a plná záloha registry hive · skóre 4,0
- **Čo robia a kto:** Tron robí v kroku 1 bod obnovy a v kroku 12 zálohu registrov cez ERUNT, pretože body obnovy nefungujú vždy (servery, Safe Mode). UVK a Tweaking.com (krok 5) robia podobne (*neoverené*).
- **Oprava po overení:** Wise Care 365 do tejto skupiny nepatrí. Tron README spája limity bodov obnovy so server edíciami a Safe Mode, nie so všeobecnou nespoľahlivosťou.
- **Stav v PortableFix:** Bod obnovy sa robí raz za batch, s obídením 24 h throttlingu a otázkou pri zlyhaní. **Spúšťa ho však kategória** (DESTRUCTIVE alebo REPAIR, SECURITY, DRIVER_UPDATES, WINGET). 15 MODERATE debloat akcií z m13 (vrátane odstránenia OneDrive bez undo) preto beží **bez** bodu obnovy, kým read-only SAFE kontroly v REPAIR ho vytvárajú. `reg save` hive sa nikde nerobí.
- **Prečo to technikovi pomôže:** Druhá záchranná sieť pre prípad, že System Protection je vypnutá alebo bod obnovy zlyhá. Zároveň odpadne zbytočné vytváranie bodu obnovy pre čisto diagnostické batche.
- **Návrh:** Spúšťač bodu obnovy = akákoľvek akcia vo fronte s rizikom iným ako SAFE (alebo nové pole `changes_state: true`), bez ohľadu na kategóriu. Pri zlyhaní alebo vypnutej ochrane ponúknuť `reg save` HKLM\SOFTWARE, HKLM\SYSTEM a NTUSER prihláseného používateľa do `Backups/<run_id>/hives` (~200 MB). V náhľade ukázať veľkosť a upozorniť, že sa obnovuje offline.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 4
- **Zdroje:** raw.githubusercontent.com/bmrf/tron/master/README.md · carifred.com/uvk/help/system_repair.php

### 4. G11 – Pre-flight brána: blokery a varovania pred batchom · skóre 4,0
- **Čo robia a kto:** Sophia `InitialActions.ps1` (**overené**) odhalí Windows upravený nástrojmi AtlasOS, Revision, Tron, RemoveWindowsAI, Ghost Toolbox, Win 10 Tweaker, BoosterX a WinClean. Upozorní aj na BitLocker, ktorý je zašifrovaný, ale s vypnutou ochranou. Flyoobe setup-preflight podľa zdroja blokuje pri chýbajúcich admin právach, čakajúcom reštarte alebo pod 5 GB voľného miesta a varuje pri batérii alebo bez internetu (*neoverené*). Tron pri čakajúcom reštarte možno len varuje, nie odmieta (*neoverené*).
- **Stav v PortableFix:** Existujú len manuálne diagnostiky (m01 `pending_reboot`, `bitlocker_status`, batéria, voľné miesto). Nič sa nespúšťa automaticky pred batchom. Banner „read-only bez admin práv“ nie je nijako vynútený.
- **Prečo to technikovi pomôže:** Vysvetlí rozbitý Store alebo Update ešte predtým, než technik niečo urobí („toto už niekto zdebloatoval“). Zabráni DISM na PC s čakajúcim reštartom alebo s 2 GB voľného miesta.
- **Návrh:** `requires_admin` v ActionDef (alebo odvodené od rizika iného ako SAFE) a blokovanie bez elevácie, čím sa banner stane pravdivým. Pred prvou akciou s vedľajším efektom jeden SAFE preflight skript s JSON výstupom:
  - blokery: čakajúci reštart s dôvodom (CBS, WU, PendingFileRenameOperations), menej ako 5 GB voľného miesta, SMART Bad pred akciami, ktoré zaťažujú disk;
  - varovania: batéria, žiadna sieť, pozastavený BitLocker, stopy debloaterov.
  
  Výsledok sa zobrazí raz a zapíše do sekcie Safety v reporte. Override je možný, ale zaloguje sa.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 4
- **Zdroje:** github.com/farag2/Sophia-Script-for-Windows/…/InitialActions.ps1 · github.com/builtbybel/Flyoobe/tree/main/Actions/setup-preflight

### 5. G12 – Jedna revízna obrazovka namiesto dialógu pri každej akcii · skóre 4,0
- **Čo robia a kto:** Winhance (**overené**): „Current: X → Config: Y“ a počítadlo „N of M reviewed (K will be applied)“. Win11Debloat končí kartou Review Changes a sleduje, čo vyžaduje reštart (*karta neoverená, reboot flagy v Features.json áno*). BleachBit 6.0 Expert Mode (*neoverené*).
- **Stav v PortableFix:** `_dispatch_action` (`main_window.py` ~2926–2958) ukazuje jeden QMessageBox pri každej akcii. Preset `privacy_debloat` sa tak opýta 8-krát, a to aj v DRY-RUN. Uninstaller a winget už majú jeden súhrnný dialóg, takže vzor v kóde existuje.
- **Prečo to technikovi pomôže:** Opakované dialógy sa prestanú čítať, čím sa bezpečnostné brány oslabujú. Jedna obrazovka zároveň ukáže, čo vyžaduje reštart a čo nemá undo, ešte pred behom. Dnes sa to dozvie až z reportu.
- **Návrh:** Pred batchom jeden dialóg s akciami zoskupenými podľa rizika:
  - plný text varovania pre MODERATE a vyššie,
  - checkbox „rozumiem, je to nevratné“ pri každej DESTRUCTIVE akcii,
  - zoznam akcií bez undo a akcií s reštartom,
  - informácia, či vznikne bod obnovy.
  
  Jeden súhlas pokryje celý batch. Audit zachová `risk_accepted` pri každej akcii s citáciou textu. V DRY-RUN sa obrazovka zobrazí bez súhlasu.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 4
- **Zdroje:** github.com/memstechtips/Winhance/…/Localization/en.json · github.com/Raphire/Win11Debloat/blob/master/Config/Features.json · ubuntuhandbook.org/index.php/2026/04/bleachbit-6-0-0-cookie-manager-expert-mode/

### 6. G16 – Robustný winget: stav „winget chýba“, parsovanie nezávislé od jazyka, pravidlá pre balík · skóre 4,0
- **Čo robia a kto:** Winget-AutoUpdate preskakuje balíky s verziou „Unknown“ (**overené**). UniGetUI 2026.3.0 opravil detekciu App Installer mimo PATH (**overené**). Skip version, ignore, pauza na batérii alebo meranej sieti a zatvorenie aplikácie pred aktualizáciou sú dlhodobé funkcie UniGetUI (*neoverené v tomto behu*).
- **Oprava po overení:** Fallback „Pinget“ v UniGetUI nemá zdroj a z porovnania je vyradený.
- **Stav v PortableFix:** `list_outdated_packages` vráti `[]` pri OSError, takže chýbajúci winget sa zobrazí ako „Žiadne aktualizácie“. `parse_winget_upgrade_table` hľadá anglické hlavičky („Name“, „Id“, „Available“), takže na slovenskom alebo nemeckom Windows vráti `[]`. Existuje len trvalý ignore list.
- **Prečo to technikovi pomôže:** Dnes môže vzniknúť **falošné „všetko aktuálne“ v klientskom reporte**. Na slovenskom trhu je to typický prípad, nie okrajový.
- **Návrh:** Tri stavy (ok / unavailable / error) a samostatný stav „winget nie je dostupný“ s ponukou `winget_source_reset` alebo preregistrácie App Installera. Ak je dostupný modul Microsoft.WinGet.Client, použiť `Get-WinGetPackage | ? IsUpdateAvailable`, inak textový fallback. Balíky s „Unknown“ zobraziť zvlášť. Pridať „preskočiť túto verziu“, „zatvoriť aplikáciu pred aktualizáciou“ a „nie na batérii“.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 4
- **Zdroje:** github.com/Devolutions/UniGetUI/releases/tag/v2026.3.0 · github.com/Romanitho/Winget-AutoUpdate

### 7. G18 – Pripravenosť WinRE a Quick Machine Recovery, SrtTrail · skóre 4,0
- **Čo robia a kto:** OSD `Get-ReAgentXml` číta `%WINDIR%\System32\Recovery\ReAgent.xml`, čo funguje v každom jazyku Windows. Rebuild-WinREPartition parsuje text `reagentc /info` a opätovne zapne WinRE. Microsoft dokumentuje, že QMR cloud a auto remediation sú na Pro/Enterprise predvolene vypnuté.
- **Oprava po overení:** Nerobí to jeden nástroj, funkcie sú rozdelené medzi viaceré. Detail „verzia 0.0.0.0 / prázdna lokácia“ je *neoverený*.
- **Stav v PortableFix:** Nič. `docs/research/research-repair-additions.md` dokonca reagentc explicitne vyradil zo scope.
- **Prečo to technikovi pomôže:** Bez funkčného WinRE nefunguje „Reset this PC“, Startup Repair, QMR ani Defender Offline (G08). Technik to zistí až vtedy, keď to potrebuje.
- **Návrh:** SAFE `boot_winre_status` (ReAgent.xml, veľkosť recovery partície, prítomnosť winre.wim). MODERATE `boot_winre_enable` iba ak winre.wim existuje; undo ho znova vypne len vtedy, ak bol vypnutý predtým. SAFE `boot_qmr_status` cez presmerovanie do súboru (pipe padá s chybou 57), s odstránením Wi-Fi hesiel z výstupu. MODERATE `boot_qmr_enable_cloud` s uložením predošlého XML. SAFE `boot_srttrail_report`. **Nerobiť** rebuild recovery partície.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 4
- **Zdroje:** github.com/OSDeploy/OSD/blob/master/Public/Functions/WinRE.ps1 · github.com/5a9awneh/Rebuild-WinREPartition · github.com/SystechConsulting/systech-itpros-microsoft/…/quick-machine-recovery/index.md · github.com/agadiffe/WindowsMize/…/Get-QuickMachineRecoverySetting.ps1 (*neoverené*)

### 8. G19 – Balík dôkazov: vstavané Windows reporty v handoff ZIP · skóre 4,0
- **Čo robia a kto:** Clonezilla ukladá do každého image Info-dmi, Info-lshw, Info-lspci a Info-smart (ak je smartctl). Tron vedie logy odstránených súborov a programov. Syncro `Upload-File` priloží súbor k záznamu zariadenia.
- **Stav v PortableFix:** `handoff.py` balí len report, audit, `undo.ps1` a README. Battery report končí v `%ProgramData%\PortableFix\BatteryReport_*` na klientskom PC. „Export diagnostics“ zbalí len vlastné logy PortableFix.
- **Prečo to technikovi pomôže:** Dôkaz o stave PC ostane u technika, nie na stroji klienta. Report zatiaľ nehovorí, čo sa zmazalo.
- **Návrh:** Konvencia `PFARTIFACT:<cesta>`: executor súbor skopíruje do `Reports/<run_id>/artifacts` a zapíše ho do auditu. `handoff.py` ho pribalí s limitom veľkosti. Nové SAFE akcie `msinfo_nfo_export` (`/nfo`, bez loadedmodules) a `power_sleep_report` (`/sleepstudy`, `/systempowerreport`, `/lastwake`, `/waketimers`, `/requests`). Čistiace akcie vypíšu top 20 zmazaných ciest s veľkosťami.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 4
- **Zdroje:** github.com/stevenshiau/clonezilla/…/ocs-functions · github.com/bmrf/tron/blob/master/README.md · MicrosoftDocs msinfo32.md

### 9. G26 – Trieda tlačových ovládačov, WPP readiness, záloha PrintBRM, SMB kompatibilita · skóre 4,0
- **Čo robia a kto:** man612 Windows-Printer-Sharing-Fix (**overené**) klasifikuje ovládače (v3/v4, Microsoft, tretia strana), hodnotí WPP readiness, testuje UNC po vrstvách (DNS → 445 → 135/RPC) a číta udalosti SMBClient 31017, 31998 a 31999. khairudinfahmi používa PrintBRM na zálohu a migráciu.
- **Oprava po overení:** PrintBRM používa **len khairudinfahmi**, nie man612.
- **Stav v PortableFix:** m14 má inventár tlačiarní a driver store, odstránenie ghost/offline tlačiarní (záloha do JSON) a reset spoolera. Chýba trieda ovládača, WPP, PrintBRM aj SMB diagnostika. `print_reset_print_system` a odstránenie osirelých ovládačov sú DESTRUCTIVE bez zálohy.
- **Prečo to technikovi pomôže:** Od júla 2026 Windows Update preferuje inbox IPP class driver a ovládače tretích strán dostávajú od júla 2027 už len bezpečnostné opravy. Windows 11 24H2 vyžaduje SMB signing a blokuje guest, čo rozbíja NAS. „Nejde tlačiareň / nejde NAS“ patrí medzi najčastejšie tikety malých firiem.
- **Návrh:** SAFE `print_driver_class_report` (MajorVersion, provider, port, WPP politika, verdikt „N tlačiarní závisí od ovládačov tretích strán“). SAFE `print_backup_printbrm` do priečinka jobu, automaticky ponúknutý pred DESTRUCTIVE akciami m14, s undo `printbrm -r`. SAFE `net_smb_compat_report` (`Get-SmbClientConfiguration`, dialekt, Signed/Encrypted, `Get-SmbMapping`, udalosti za 7 dní) s verdiktmi v bežnej reči. Akékoľvek uvoľnenie guest alebo signing len na strane klienta, HIGH riziko, mimo select-all, s undo na presný predošlý stav.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 4
- **Zdroje:** github.com/man612/Windows-Printer-Sharing-Fix (docs/WPP-READINESS.md, docs/MODERN-SMB-RPC.md) · github.com/khairudinfahmi/WindowsPrinterSharingFix · MicrosoftDocs end-of-servicing-plan-for-third-party-printer-drivers-on-windows.md

### 10. G08 – Hlbšie akcie Defendera: full scan, offline scan, história hrozieb, PUA · skóre 4,0 (riziko 2)
- **Čo robia a kto:** Defender sám vie full scan (`Start-MpScan -ScanType FullScan`), boot-time offline scan (`Start-MpWDOScan`) aj blokovanie PUA (`Set-MpPreference -PUAProtection`); to je **overené** v oficiálnej dokumentácii. Tron reťazí KVRT, MSS, Malwarebytes a Sophos.
- **Oprava po overení:** Nie všetky second-opinion skenery bežia najprv v režime len skenovania. Tron ich predvolene púšťa v režime odstraňovania. Jasný detect-only majú len MSS (`/n`) a AdwCleaner (`/scan`). Logy sú textové, nie štruktúrované.
- **Stav v PortableFix:** m23 má status, update, quick scan a zoznam a vymazanie výnimiek. Chýba FullScan, WDOScan, PUA aj Get-MpThreat*.
- **Prečo to technikovi pomôže:** Technik uvidí, čo Defender už našiel. Rootkity rieši offline scan bez stiahnutia čohokoľvek.
- **Návrh:**
  - SAFE `sec_defender_threat_history` (`Get-MpThreatDetection`, `Get-MpThreat`) a zoznam karantény.
  - MODERATE full scan a cielený scan (%TEMP%, Downloads, %APPDATA%, %ProgramData%) s `-AsJob` a výpisom priebehu, aby ich nezabil inactivity watchdog.
  - MODERATE `hard_defender_pua_enable` s undo na predošlú hodnotu.
  - REQUIRES_REBOOT `sec_defender_offline_scan` mimo select-all: nie na ARM64, Defender musí byť aktívny AV, najprv update signatúr, ukázať ID BitLocker kľúča, spustiť ako posledný po zapísaní reportu.
- **Náročnosť:** S · **Riziko:** 2 · **Hodnota:** 4
- **Zdroje:** MicrosoftDocs windows-powershell-docs …/Defender/Start-MpWDOScan.md · microsoft-defender-offline.md (mirror adamblack1111/M365D) · github.com/dcorrada/POWERSHELL/…/SafetyScan.ps1

### 11. G01 – Bod obnovy, chránené aplikácie a „zatvor bežiace programy“ pre panely Uninstaller, winget a osirelé kľúče · skóre 3,0 (po korekcii)
- **Čo robia a kto:** BCU pred úlohou ponúkne bod obnovy, tichý režim a zoznam programov, ktoré treba zavrieť. Pri výbere chránených alebo systémových položiek varuje. Pred mazaním zvyškov zálohuje kľúče do `.reg`. Patch My PC robí bod obnovy pred aktualizáciou. Revo robí bod obnovy aj zálohu registrov.
- **Oprava po overení:** Pôvodná hypotéza, že tieto panely obchádzajú DRY-RUN, potvrdenie aj audit, bola **nesprávna**. Winget panel (`main_window.py` ~1780–1830) aj Uninstaller (~2290–2460) majú dry-run, potvrdenie s predvolenou odpoveďou Nie a `_log_panel_action` / `risk_declined`. Osirelé kľúče sa pred zmazaním exportujú (`uninstaller.backup_registry_key`). BCU sa na chránené aplikácie nepýta pred každou úlohou, len varuje. Limit 1000 operácií v histórii UniGetUI je *neoverený*.
- **Stav v PortableFix:** `RestorePointRunner` sa volá **len na jednom mieste** (katalógová fronta, riadok 2856). Uninstaller, winget update ani orphan cleanup bod obnovy nerobia. Chýba zoznam chránených aplikácií aj výzva na zatvorenie procesov.
- **Prečo to technikovi pomôže:** Odinštalovanie a aktualizácia sú zmeny, ktoré klient spozoruje najviac, a nemajú undo. Bod obnovy je jediná cesta späť.
- **Návrh:** Malý adaptér, ktorý pred prvou živou zmenou v paneli použije rovnaké pravidlo bodu obnovy ako batch (a G24). Plus zoznam chránených aplikácií (antivírus, ovládače, runtime, RMM agent klienta) a pred spustením zoznam procesov, ktorých image leží v `InstallLocation`, s ponukou ich zatvoriť.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 3
- **Zdroje:** github.com/BCUninstaller/Bulk-Crap-Uninstaller/blob/master/doc/BCU_manual.html · github.com/Devolutions/UniGetUI/releases · winget-pkgs PatchMyPC

### 12. G33 – Tichý alebo offline režim bez sieťového pollingu na pozadí · skóre 3,0
- **Čo robia a kto:** BleachBit, PrivaZer, Sysinternals a NirLauncher neinštalujú služby a nemajú telemetriu. Používatelia kritizujú CCleaner 7 a IObit za služby na pozadí a pingy.
- **Oprava po overení:** Aj tieto nástroje robia drobné sieťové volania (update check, VirusTotal opt-in, symboly), takže tvrdenie „nikdy nevolajú sieť“ je prehnané. Kontrola missing_check tvrdila, že PortableFix nemá polling na pozadí. **To nesedí:** v kóde som overil ping na 8.8.8.8 každé 4 s (`main_window.py` 3183–3186, `sysinfo.py` 348 a 477), VPN kontrolu cez powershell.exe každých 60 s, senzory každých 2,5 s, sysinfo každé 2 s a update check na GitHub pri štarte bez možnosti vypnutia.
- **Prečo to technikovi pomôže:** Na firemnej sieti klienta môže ICMP na 8.8.8.8 každé 4 s a opakované spúšťanie powershell.exe spustiť EDR alerty a nechať stopy.
- **Návrh:** Prepínač „Tichý/offline režim“ v nastaveniach a indikátor na lište. Vypne update check, ping, VPN polling a automatický winget scan. Cieľ pingu bude konfigurovateľný (predvolene brána, potom 1.1.1.1). Timery sa zastavia pri minimalizovanom okne. Režim sa zapíše do reportu.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 3
- **Zdroje:** elevenforum.com/t/ccleaner-7-warning.40596/ · vlastné overenie v kóde

### 13. G29 – Stav Windows 10 ESU · skóre 3,0 · časovo citlivé
- **Čo robia a kto:** ConsumerESU (abbodi1406, **overené**, vetva `master`) dekóduje `ESUEligibility` a `ESUEligibilityResult` (napr. 3 = DeviceEnrolled, 4 = ReEnrollReq) z `HKCU\…\ConsumerESU` a volá `GetESUEligibilityStatusV1`.
- **Oprava po overení:** Pre Fleet sa funkcia nepotvrdila. Citovaná URL mala zlú vetvu (`main` namiesto `master`).
- **Stav v PortableFix:** Vek záplat už existuje (m08, posledné úspešné WU hľadanie a inštalácia). m01 vypisuje build, UBR a pevnú vetu „overte ESU“. Chýba len dekódovanie stavu enrollmentu.
- **Prečo to technikovi pomôže:** Consumer ESU podľa dostupných informácií končí 13. 10. 2026. Klient potrebuje jasnú vetu o svojom stave a o možnostiach: upgrade, komerčné ESU alebo nové PC.
- **Návrh:** SAFE `os_win10_esu_status` číta hodnoty z hive interaktívneho používateľa (pozri G25). Pridá vek poslednej CU (viac ako 45 dní = nezáplatované), prítomnosť 0patch agenta a Win11 readiness z existujúcich akcií a vydá verdikt.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 3
- **Zdroje:** github.com/abbodi1406/ConsumerESU/blob/master/Consumer_ESU_Enrollment.ps1

### 14. G23 – Čistenie prehliadačov vo všetkých profiloch a Chromium prehliadačoch s kontrolou bežiaceho prehliadača · skóre 3,0
- **Čo robia a kto:** BleachBit 6.0.2 pridal podporu viacerých profilov Chrome a Edge (**overené**). Zoznam cookies, ktoré sa zachovajú, majú BleachBit aj CCleaner (*neoverené, známe*).
- **Oprava po overení:** Tvrdenie „BleachBit zastaví čistenie cookies, keď nevie prečítať keep-list“ nemá zdroj a je vyradené.
- **Stav v PortableFix:** `browser_cache_sweep` má napevno `User Data\Default` (Firefox rieši všetky profily). Brave, Vivaldi a Opera chýbajú, kontrola bežiaceho prehliadača tiež. m17 sa tiež pozerá len na Default a pri resete profilu prehliadač natvrdo zabije.
- **Návrh:** Glob `User Data\*\{Cache,Code Cache,GPUCache,Service Worker\CacheStorage}` pre Chrome, Edge, Brave, Vivaldi a Operu. Bežiaci prehliadač preskočiť a nahlásiť. Mazať len cache, nikdy cookies ani loginy. Rovnakú enumeráciu profilov použiť aj v `browser_extensions_report` a hijack reportoch m17.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 3
- **Zdroje:** github.com/bleachbit/bleachbit/releases/tag/v6.0.2 · support.ccleaner.com (retain cookies)

### 15. G31 – Storage Sense ako nastavenie, ktoré zostane po odchode technika, namiesto agenta · skóre 3,0
- **Čo robia a kto:** Konkurencia udržiava PC čisté agentom alebo taskom (CCleaner Pro, WAU). Storage Sense je súčasť Windows, dá sa nakonfigurovať pre každého používateľa v registroch (`StoragePolicy`: 01 zapnuté, 2048 kadencia…) a spúšťa sa pri nedostatku miesta alebo podľa plánu.
- **Oprava po overení:** Storage Sense „nenecháva službu“, je to funkcia OS. Plánované čistenie v CCleaneri je len v Pro.
- **Stav v PortableFix:** Nič. `research-cleanup-additions.md` Storage Sense vyradil (0 MB pri behu, nedokumentovaná schéma).
- **Prečo to technikovi pomôže:** PC sa po odchode technika opäť zaplní. Storage Sense to rieši bez porušenia princípu „nenechávame agenta“ a v reporte sa to dá dobre komunikovať.
- **Návrh:** SAFE `storage_sense_report` (dekódovanie hodnôt) a MODERATE `storage_sense_enable`: mesačne, dočasné súbory, kôš starší ako 30 dní, Downloads vypnuté. Zapisuje do hive klienta (G25) a undo vráti presné predošlé hodnoty. Schéma je nedokumentovaná, preto treba test na 23H2, 24H2 a 25H2.
- **Náročnosť:** S · **Riziko:** 1 · **Hodnota:** 3
- **Zdroje:** cyberdrain.com/monitoring-with-powershell-monitoring-storage-sense-settings/ · github.com/kaspersmjohansen/StorageSense

### 16. G06 – Triáž pádov: dekódovanie bugchecku, WHEA, Reliability história, zachovanie dumpov · skóre 2,5
- **Čo robia a kto:** WhoCrashed vysvetľuje pády v bežnej reči a menuje podozrivý ovládač. HWiNFO zobrazuje WHEA počítadlá. Reliability Monitor má denný index 1–10 na časovej osi. PDQ System Stability scanner číta `Win32_ReliabilityStabilityMetrics`.
- **Oprava po overení:** mcp-windbg **nespúšťa** `!analyze -v` automaticky na každý dump ani sám nezoskupuje pády. Robí to AI model na požiadanie a dokumentácia varuje, že na veľkom priečinku je to pomalé.
- **Stav v PortableFix:** `bsod_summary` vypíše súbory v Minidump a surové udalosti 1001 a 41. `crash_dumps` v m02 je opt-in a varuje „najprv spusti bsod_summary“, ale dumpy nearchivuje.
- **Prečo to technikovi pomôže:** Rozlíši hardvér od ovládača a „odkedy sa to deje“. Zachová dôkazy.
- **Návrh:** SAFE `bsod_analyze` parsuje 1001 (kód a 4 parametre) a Kernel-Power 41 (BugcheckCode 0 = výpadok napájania), mapuje ~25 bežných kódov na ďalšie kroky v SK a EN a koreluje pády s inštaláciami ovládačov. Ak je `cdb.exe` dostupný (Windows Kits alebo WinDbg Store), spustí `cdb -z dump -c ".lastevent;!analyze -v;q"` s timeoutom. Ďalej SAFE `whea_errors_30d` a `reliability_history` (min, priemer a posledná hodnota za 28 dní, najčastejšie padajúci ProductName, kontrola RAC tasku). `crash_dumps` pred zmazaním skopíruje dumpy do handoff (G19).
- **Náročnosť:** M · **Riziko:** 1 · **Hodnota:** 5
- **Zdroje:** winget-pkgs Resplendence.WhoCrashed 7.10 · github.com/pdqcom/PowerShell-Scanners/…/System Stability.ps1 · github.com/svnscha/mcp-windbg/…/triage.md · microsoft/wmi Win32_ReliabilityStabilityMetrics

### 17. G13 – Verdikt zdravia disku a brána „najprv image“ pred akciami, ktoré zaťažujú disk · skóre 2,5 · ochrana dát
- **Čo robia a kto:** CrystalDiskInfo (`AtaSmart.cpp`) má explicitné pravidlá pre 05, C5 a C6 a pre NVMe critical warning a spare. Manuál ddrescue: *„Never try to repair a file system on a drive with I/O errors.“* Clonezilla predvolene nespúšťa fsck na zdroji. Tron preskočí defrag pri SMART chybách a na SSD.
- **Stav v PortableFix:** `disk_smart_status` (HealthStatus), `disk_reliability_counters` a `sysinfo.disk_health_summary` len zobrazujú dáta. Chýba prah, verdikt aj blokovanie pred `Repair-Volume`, defragom a `chkdsk /f /r`.
- **Prečo to technikovi pomôže:** `chkdsk /r` na umierajúcom disku môže zničiť dáta klienta. To je najdrahšia chyba, akú môže technik urobiť.
- **Návrh:** SAFE `disk_health_verdict` (`Get-PhysicalDisk`, `MSStorageDriver_FailurePredictStatus`, `Get-StorageReliabilityCounter`, udalosti disk, ntfs a storport, voliteľne už pribalený LibreHardwareMonitor) vráti Good, Caution, Bad alebo Unknown pre každý disk aj s atribútom, ktorý rozhodol. Nové YAML pole `disk_stress: heavy` pre chkdsk, defrag, DISM, SFC a `wipe_free_space`. Pri Caution alebo Bad ich engine zablokuje (override sa loguje) a ukáže kartu „najprv image“ v SK a EN: pripojiť disk priamo, klonovať (HDDSuperClone alebo ddrescue), opravovať klon. Varovanie pri `chkdsk /r` a wipe na SSD.
- **Náročnosť:** M · **Riziko:** 1 · **Hodnota:** 5
- **Zdroje:** github.com/hiyohiyo/CrystalDiskInfo/blob/master/AtaSmart.cpp · gnu.org ddrescue manual (mirror mruffalo/ddrescue) · clonezilla ocs-functions · github.com/bmrf/tron

### 18. G03 – Pokračovanie batchu po reštarte a udržanie PC hore · skóre 2,5 (riziko 2)
- **Čo robia a kto:** Tron zapíše HKCU RunOnce `*tron_resume` (funguje aj v Safe Mode) a po páde alebo vynútenom reštarte pokračuje od poslednej **začatej** fázy. Počas behu drží PC hore cez caffeine.exe a na konci obnoví nastavenia napájania.
- **Oprava po overení:** Tron pokračuje od posledného *začatého* kroku, nie dokončeného. „Rovnaké nastavenia“ README neuvádza. Detekcia prerušeného reťazca vo Windows Repair Toolbox je *neoverená*.
- **Stav v PortableFix:** Nič. `updater.recover_interrupted_swap` rieši len self-update. `SetThreadExecutionState` nikde. m09 len prepne na High Performance.
- **Prečo to technikovi pomôže:** Chkdsk pri boote, WU reset → DISM → SFC, ovládače a Defender Offline vyžadujú reštart uprostred. Dnes sa beh rozdelí na dva reporty a dlhý DISM môže zabiť uspanie notebooku.
- **Návrh:**
  - Keep-awake: `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)` cez ctypes na celý batch. To je triviálne a stojí za to hneď.
  - Resume: pri akcii REQUIRES_REBOOT zapísať `Data/pending_batch.json` (run_id, zostávajúce id, dry_run, job) a ponúknuť tlačidlo „Reštartovať a pokračovať“, ktoré zapíše HKCU RunOnce na `PortableFix.exe --resume <run_id>`. Pri štarte použiť rovnaký run_id, audit aj `undo.ps1`. Zapísať audit udalosť `resumed_after_reboot`. Pri zmene písmena USB sa opýtať.
- **Náročnosť:** M (keep-awake S) · **Riziko:** 2 · **Hodnota:** 5
- **Zdroje:** raw.githubusercontent.com/bmrf/tron/master/README.md · malwaretips.com Windows Repair Toolbox vlákno (*neoverené*)

### 19. G14 – Záloha používateľských dát na iný disk s hash manifestom a overením · skóre 2,5 (riziko 2)
- **Čo robia a kto:** USMT ScanState odmieta C:\ ako úložisko, predvolene berie všetky profily, `/uel:<dni>` filtruje podľa posledného prihlásenia a `/vsc` kopíruje zamknuté súbory cez VSS (len pri nekomprimovanom alebo hard-link úložisku). Clonezilla má voliteľné checksumy image aj súborov a overenie po obnove (`-gm/-gmf`, `-cm/-cmf`). MigrationMerlin robí verifikačný report (*neoverené*).
- **Stav v PortableFix:** `backup_user_folders` robocopy-uje 4 priečinky aktuálneho používateľa do `%ProgramData%` na **tom istom disku** (popis to priznáva). Kontroluje len exit kód robocopy.
- **Prečo to technikovi pomôže:** Keď zomrie disk, zomrie aj záloha. Súhlas klienta so zálohou je aj právna ochrana technika (pozri G20).
- **Návrh:** MODERATE `backup_user_data_external`:
  - cieľ len na zväzkoch s iným DiskNumber ako systémový disk, alebo na UNC ceste;
  - všetky profily použité za N dní, vrátane `*.pst`, podpisov Outlooku, Sticky Notes a profilov prehliadačov (len pri zatvorenom prehliadači);
  - VSS snapshot (`Win32_ShadowCopy.Create`) + `robocopy /B /XJ /UNILOG`, s preskočením OneDrive placeholderov;
  - SHA-256 manifest, zoznam preskočených súborov a opätovné zahashovanie cieľa;
  - manifest a log idú do handoff.
  
  V reporte: kde je záloha a hash manifestu, alebo „klient zálohu odmietol“.
- **Náročnosť:** M · **Riziko:** 2 · **Hodnota:** 5
- **Zdroje:** USMT scanstate syntax (mirror SystechConsulting) · clonezilla ocs-functions · github.com/supermarsx/migration-merlin/…/post-migration-verify.ps1

### 20. G09 – Kontrola stavu „už aplikované / netreba“ pred spustením · skóre 2,0
- **Čo robia a kto:** WinUtil „Get Installed Tweaks“ zaškrtne tweaky, ktoré už sú aplikované (release 26.06.23 pridal aj detekciu vrátených tweakov). Win11Debloat 2026.06.11 „detect previously applied tweaks“ a „Show & Undo“ (podľa release notes). Winhance zobrazuje aktuálny stav.
- **Oprava po overení:** Winhance side-by-side „aktuálne, odporúčané, predvolené“ a O&O Premium re-apply pri drifte sú *neoverené*.
- **Stav v PortableFix:** ActionDef nemá pole pre kontrolu stavu. `preview_command` (26 akcií) beží len v dry-run a je zameraný na čistenie. Reportové akcie (`tune_power_plan_report`) nie sú prepojené s tweakmi.
- **Prečo to technikovi pomôže:** Report povie „bolo už v poriadku“. Na ďalšej návšteve ukáže, čo Windows vrátil (telemetriu, Copilot).
- **Návrh:** Voliteľný `check_command` (SAFE, vypíše APPLIED, NOT_APPLIED alebo UNKNOWN). Pri otvorení kategórie beží na pozadí a zobrazí čip so stavom. Batch preskočí akcie so stavom APPLIED a zapíše to do auditu. `snapshot.py` uloží výsledky pre drift. Začať s m13, m09 a m08.
- **Náročnosť:** M · **Riziko:** 1 · **Hodnota:** 4
- **Zdroje:** github.com/ChrisTitusTech/winutil/releases · github.com/Raphire/Win11Debloat/releases · github.com/memstechtips/Winhance/blob/main/README.md

### 21. G20 – Intake a outtake formulár, čas práce, branding, redakcia citlivých údajov · skóre 2,0
- **Čo robia a kto:** RepairShopr má intake a outtake formuláre s podpisom a podmienkami. Syncro premieňa timery na položky faktúry. Tactical RMM (EE reporting) a d7x dávajú do reportu logo dielne.
- **Oprava po overení:** Worksheety v Syncro sú checklisty a samé sa na položky faktúry nemenia. Branding UVK nie je potvrdený. Anonymizácia v TeamViewer pred uploadom je **nepotvrdená**, a zdroj „ScreenTail market scan“ sú plánovacie poznámky iného projektu.
- **Stav v PortableFix:** `report.py` 279–307 obsahuje len technika, klienta a poznámku. Formuláre, čas, logo ani redakcia nie sú.
- **Prečo to technikovi pomôže:** Sólo technik potrebuje dôkaz o stave pri prevzatí („škrabanec tam bol“), súhlas so stratou dát, funkčný test pri odovzdaní, fakturovateľný čas a vlastnú značku. Report a ZIP dnes obsahujú sériové čísla, MAC a IP adresy aj mená používateľov bez možnosti ich skryť, čo je z pohľadu GDPR citlivé.
- **Návrh:** Karta Intake (problém, fyzický stav, príslušenstvo, súhlas so zálohou alebo waiver, ako sa narábalo s heslom) a karta Outtake (Wi-Fi, zvuk, kamera, klávesnica, USB, nabíjanie, displej: Pass, Fail alebo N/A, meno klienta a čas). Obe ako `_system` audit udalosti. Trvanie batchu a voliteľný timer. Branding v nastaveniach (firma, IČO, kontakt, logo v base64). Prepínač „Redigovať pre klienta“, ktorý zamaskuje používateľské mená v cestách, IP, MAC, sériové čísla, fragmenty kľúčov a SSID.
- **Náročnosť:** M · **Riziko:** 1 · **Hodnota:** 4
- **Zdroje:** github.com/omiinaya/mcp-repairshopr/…/ticket.md · github.com/watchmanmonitoring/wm-syncromsp-swagger-client/…/WorksheetResultApi.md · github.com/amidaware/trmm-docs/…/reporting_basics.md

### 22. G10 – Deklaratívne operácie s registrami a službami s automatickým undo na pôvodnú hodnotu · skóre 2,0 (riziko 2)
- **Čo robia a kto:** WinUtil `tweaks.json` má `OriginalValue` a `OriginalType`. Win11Debloat od 2026.05.10 zálohuje registre pred zmenou a pred obnovou ukáže súhrn zmien na potvrdenie.
- **Oprava po overení:** WinUtil `OriginalValue` sú **napevno zapísané predvolené hodnoty Windows**, nie snapshot stroja. Formát zálohy vo Win11Debloat je pravdepodobne `.reg`, nie JSON (*neoverené*). Win11Debloat neobnovuje odstránené aplikácie.
- **Stav v PortableFix:** Undo je ručne napísaný pevný `undo_command`. README priznáva: „Undo only covers actions with a static reversible command.“ 52 zo 105 akcií s rizikom iným ako SAFE nemá undo. Existujúce undo zapisujú pevné predvolené hodnoty (napr. `net_set_public_dns` undo prepne DNS na DHCP, aj keď klient mal statickú DNS).
- **Prečo to technikovi pomôže:** Undo, ktoré vráti PC tak, ako bolo, a neprepíše vlastné nastavenia klienta. Tu môže PortableFix **predbehnúť** WinUtil (pozri „Kde môžeme byť lepší“).
- **Návrh:** YAML `ops:` (`reg_set`, `reg_delete`, `service_start_type`, `task_state`). Engine pred každou operáciou prečíta živú hodnotu (alebo zaznamená, že neexistovala) do `Backups/<run_id>/state.json` a vygeneruje presné inverzné riadky pre `undo.ps1`, vrátane zmazania hodnôt, ktoré predtým neexistovali. Náhľad je potom zadarmo. Migrovať najprv m13 a m09.
- **Náročnosť:** M · **Riziko:** 2 · **Hodnota:** 4
- **Zdroje:** github.com/ChrisTitusTech/winutil/blob/main/config/tweaks.json · github.com/Raphire/Win11Debloat/wiki/Reverting-Changes

### 23. G15 – Tichá odinštalácia MSI, NSIS a Inno a hodnotený sken zvyškov · skóre 2,0 (riziko 2)
- **Čo robia a kto:** BCU (**len BCU**, ostatní nie) si skladá tiché príkazy (`msiexec /x {GUID} /qn`, NSIS `/S`, Inno `/VERYSILENT`). Tiché odinštalácie púšťa paralelne do nastaveného limitu a interaktívne po jednej. Zvyšky hodnotí stupnicou Very good / Good / Questionable / Bad, predvolene vyberie Good a lepšie, kľúče zálohuje do `.reg` a súbory posiela do koša. Revo má režimy Safe, Moderate a Advanced.
- **Stav v PortableFix:** Použije `QuietUninstallString` od výrobcu, inak `UninstallString`. `MsiExec /I{GUID}` otvorí maintenance dialóg a 300 s timeout ho označí ako zlyhanie. Zvyšky sa hľadajú len ako osirelé Uninstall kľúče.
- **Návrh:** Detekcia typu inštalátora (`WindowsInstaller=1` → MSI `/x … /qn /norestart /l*v`, `unins000.exe` → Inno, `Uninstall.exe` → NSIS `/S`). Dve fronty: tichá a interaktívna (sériovo, bez timeoutu). Úzky sken zvyškov (InstallLocation, ProgramData a AppData podľa vendora alebo produktu, `HK*\Software\<Publisher>\<Product>`, služby a tasky ukazujúce na zmazané cesty) s hodnotením istoty. Predvolene vybrať len položky s vysokou istotou, `reg export` a kôš, audit aj undo.
- **Náročnosť:** M · **Riziko:** 2 · **Hodnota:** 4
- **Zdroje:** BCU_manual.html · github.com/BCUninstaller/Bulk-Crap-Uninstaller/issues/734

### 24. G17 – Karta zdravia HW: opotrebenie batérie, teploty pod záťažou, RAM test · skóre 2,0 (riziko 2)
- **Čo robia a kto:** HWiNFO: Min, Max a Avg a príznaky throttlingu. LibreHardwareMonitor: Min a Max (bez Avg). BatteryInfoView: návrhová kapacita vs plná kapacita a počet cyklov. OCCT a AIDA64 zastavia stres test pri teplote alebo chybe.
- **Oprava po overení:** Tvrdenie „Belarc ukazuje zdravie batérie na prvej strane“ je *pravdepodobne nepravdivé* a je vyradené.
- **Stav v PortableFix:** `battery_health_report` len vypíše cestu k HTML na klientskom PC. Živé teploty sú v paneli sysinfo, ale bez záznamu. RAM test ani stres test nie sú.
- **Prečo to technikovi pomôže:** Tieto čísla vedú k predaju: nová batéria, SSD, RAM, pasta.
- **Návrh:**
  - (a) `battery_wear` cez `powercfg /batteryreport /XML`: Good nad 80 %, Caution 60–80 %, Replace pod 60 %;
  - (b) záznam senzorov počas batchu (min, max, priemer CPU a GPU, príznak pri 95 °C a viac);
  - (c) `mem_test_schedule` (mdsched, REQUIRES_REBOOT) a SAFE `mem_test_result` (MemoryDiagnostics-Results);
  - (d) voliteľný MODERATE `load_check_60s` s automatickým prerušením, mimo presetov.
- **Náročnosť:** M · **Riziko:** 2 · **Hodnota:** 4
- **Zdroje:** winget-pkgs NirSoft.BatteryInfoView 1.27 · LibreHardwareMonitor MainForm.Designer.cs · powercfg-command-line-options.md

### 25. G25 – Správny cieľový používateľ: „over-the-shoulder“ elevácia a hive prihláseného používateľa · skóre 2,0 (riziko 2)
- **Čo robia a kto:** Winhance (**overené**): *„This app was elevated with a different account's credentials. Settings will still be applied to the logged-in user.“* Win11Debloat má `-User <meno>` a `-Sysprep` (Default profil).
- **Oprava po overení:** Samostatný „SYSTEM mód“ vo Win11Debloat nie je zdokumentovaný. Consumer ESU tool túto funkciu nemá.
- **Stav v PortableFix:** Porovnanie prihláseného a elevovaného používateľa neexistuje. HKCU tweaky (m13, m09, reset proxy) idú do hive technika, nie klienta, a hlásia úspech.
- **Prečo to technikovi pomôže:** Technik v doméne sa bežne eleviuje vlastným admin účtom. Dnes by „úspešný“ debloat klientovi nič nezmenil. Je to tichá chyba.
- **Návrh:** Pri štarte porovnať vlastníka explorer.exe (SID interaktívnej relácie) s tokenom procesu. Pri rozdiele zobraziť banner a exportovať `$__pfUserHive = 'Registry::HKEY_USERS\<SID>'` do preludu. HKCU akcie prepísať na `$__pfUserHive` a ak profil nie je načítaný, použiť `reg load` NTUSER.DAT. Audit zapíše cieľový SID. Voliteľne „všetci používatelia + Default“ pre nové PC.
- **Náročnosť:** M · **Riziko:** 2 · **Hodnota:** 4
- **Zdroje:** Winhance en.json (InfoBar_OtsElevation) · github.com/Raphire/Win11Debloat/wiki/Advanced-Features

### 26. G02 – Štruktúrované findings a vysvetlený verdikt zdravia namiesto skóre 100 − 10x · skóre 1,67 (riziko 2)
- **Čo robia a kto:** NinjaOne API v2 má stav HEALTHY / NEEDS_ATTENTION / UNHEALTHY s počítadlami (chýbajúce patche, aktívne hrozby…). CrystalDiskInfo má Good, Caution, Bad a Unknown podľa auditovateľných pravidiel. Belarc má bezpečnostný súhrn hore v reporte.
- **Oprava po overení:** Voľnotextové pole s dôvodom v NinjaOne *nie je potvrdené* (dôvod môže vyplývať len z počítadiel). „Každý alert v d7x otvorí nástroj na opravu“ je *neoverené* absolútne tvrdenie. Hard Disk Sentinel a Fleet sú spomenuté bez konkrétnej funkcie.
- **Stav v PortableFix:** `main_window.py:2146` `score = max(40, 100 - unique_recommended*10)` a `_score_state` (80 a viac good, 60 a viac warn). Päť `problem_keywords` pravidiel je založených na hľadaní podreťazca, čo sa rozbije na lokalizovanom Windows. Findings nemajú závažnosť ani dôvod.
- **Prečo to technikovi pomôže:** Klient chce jeden verdikt pre každú oblasť s dôkazom. Technik chce kliknúť a predvybrať opravu. A predovšetkým: **nesmie to byť Fortect.**
- **Návrh:** Riadok `PFJSON:{"findings":[{"id","severity","area","msg_sk","msg_en","fix":[…]}]}` vo výstupe akcie. `executor.py` ho oddelí od konzoly a `findings[]` ide do auditu aj report JSON. Dashboard dostane dlaždice pre oblasti (Disk, Pády, Bezpečnosť, Aktualizácie, Batéria, Boot) so stavmi OK, Pozor, Kritické a Neznáme, s dôvodmi a klikom na opravu. Číselné skóre zrušiť alebo zobraziť aj so vzorcom. 5 keyword pravidiel migrovať. Na vrch report.html dať blok „Súhrn pre klienta“ (Nájdené / Opravené / Odporúčané).
- **Náročnosť:** L · **Riziko:** 2 · **Hodnota:** 5
- **Zdroje:** github.com/jstott/postman-ninjarmm/…/NinjaRMM-API-v2.yaml · CrystalDiskInfo AtaSmart.cpp · d7xtech.com/d7x/manual/d7x-system-info/ · Fleet secureboot README

### 27. G05 – Výber jednotlivých položiek a parametre (model „fixlist“) · skóre 1,67 (riziko 2) · architektonický základ
- **Čo robia a kto:** FRST: riadky z FRST.txt sa vložia do fixlist.txt, po Fix vznikne Fixlog.txt a položky sa presunú do karantény so zálohou registrov. UVK: riadky z logu sa vložia do skriptu. Autoruns: checkbox pri každej položke.
- **Oprava po overení:** FRST **nerobí** bod obnovy automaticky, len s direktívou `CreateRestorePoint:`. DDU a SDIO sú spomenuté bez konkrétnej funkcie.
- **Stav v PortableFix:** Checkboxy existujú pri celých akciách, per-item výber len v paneloch winget a Uninstaller. Každý `command` je pevný string: nedá sa povedať „odinštaluj tento KB“, „vypni tento task“ ani „odstráň túto tlačiareň“.
- **Prečo to technikovi pomôže:** Odomyká G04, časti G26, G23 a G15 a tiež vlastné akcie.
- **Návrh:** `items_command` (SAFE, vráti JSON riadky `{id,label,detail,risk_hint}`) + šablóna `apply_command`, ktorá dostane vybrané id cez env premennú alebo dočasný JSON, **nikdy nie interpoláciou do reťazca**. Striktný regex na id proti injection. GUI zobrazí dialóg s checklistom. Audit zapíše presné id a undo vygeneruje riadok pre každú položku.
- **Náročnosť:** L · **Riziko:** 2 · **Hodnota:** 5
- **Zdroje:** github.com/rifteyy/frst-snippets · carifred.com/uvk/help/script_commands/ · BleepingComputer FRST tutorial

### 28. G04 – Triáž autoštartu s filtrom podpisu a reverzibilným vypnutím · skóre 1,67 (riziko 3)
- **Čo robia a kto:** Autoruns 14.3 pokrýva ~17–20 kategórií. Filter skryje položky podpísané Microsoftom, stĺpce ukazujú podpis, hash a VirusTotal. Vypnutie je reverzibilný checkbox a `.arn` snapshoty sa dajú porovnať (File > Compare). Glary má „Delay“ (*neoverené*).
- **Oprava po overení:** `-u` (len nepodpísané) patrí command-line verzii **autorunsc** a pri zapnutom VT ukáže aj neznáme alebo detegované položky.
- **Stav v PortableFix:** m07 má 7 read-only reportov (Run, Startup, tasky, služby, WMI, IFEO, unquoted paths), bez filtra podpisu, hashu či vypnutia. Autenticode kontrola existuje len pre bežiace procesy (m08).
- **Prečo to technikovi pomôže:** Upratanie autoštartu je jedna z najčastejších úloh v teréne a každý konkurent vie položky aj meniť.
- **Návrh:**
  - Krok 1 (SAFE, dá sa hneď, bez G05): `autoruns_thirdparty_signed_view`. Run a RunOnce, Startup, tasky, služby, Winlogon, AppInit, LSA, print monitors, Winsock, BootExecute, IFEO a Active Setup, so signerom, SHA256 a časom súboru. Bez položiek podpísaných Microsoftom, so stabilnými ID.
  - Krok 2 (s G05): `autoruns_disable_items` presunie Run hodnoty do kľúča „AutorunsDisabled“ patriaceho PortableFix, spustí `Disable-ScheduledTask` alebo uloží štartovací typ služby, každé s presnou inverziou v `undo.ps1`.
  - Inventár autoštartu do `snapshot.py`, aby report vedel ukázať „nové od minulej návštevy“.
- **Náročnosť:** L (krok 1: S) · **Riziko:** 3 · **Hodnota:** 5
- **Zdroje:** MicrosoftDocs sysinternals autoruns.md · glarysoft.com/kb/startup-manager/ · pupontech screenconnect-cleanup docs

### 29. G27 – Offline mapovanie symptómov (SK/EN) na preset, neskôr lokálne MCP rozhranie · skóre 1,5
- **Čo robia a kto:** DaRT Solution Wizard sa opýta niekoľko otázok a odporučí nástroj. Windows Agent in Settings mapuje prirodzený jazyk na nastavenie, pred zmenou sa pýta a potom ponúka Undo (*undo a chýbajúca slovenčina neoverené*). Vyžaduje Copilot+ PC.
- **Stav v PortableFix:** Vyhľadávanie je obyčajné hľadanie podreťazca. `problem_keywords` má 5 z 229 akcií a sú to väzby diagnostika → oprava, nie vstup symptómov.
- **Prečo to technikovi pomôže:** Technik začína od sťažnosti klienta („Outlook sa neotvára“, „nejde internet“), nie od zoznamu modulov. Pomôže to hlavne juniorom.
- **Návrh:** `Data/symptoms.yaml` (frázy SK a EN, SAFE diagnostiky, nadväzujúce opravy, vysvetlenie). Porovnávanie bez diakritiky s jednoduchým odstraňovaním slovenských koncoviek a tabuľkou synoným. Top 3 výsledky s riadkom „prečo“. Neskôr headless ActionService a read-only stdio MCP (`--mcp`): `list_actions`, `run_safe_action`, `preview_action`, `symptom_to_plan`. Plán s rizikom iným ako SAFE sa musí schváliť v GUI. **Nikdy** neposkytovať surový shell.
- **Náročnosť:** M · **Riziko:** 1 · **Hodnota:** 3
- **Zdroje:** MicrosoftDocs learn agent-in-settings 2-definition.md · DaRT 10 overview (mirror cscannell-inacloud)

### 30. G32 – Katalóg a aktualizácie odolné voči manipulácii, priečinok pre vlastné akcie · skóre 1,5
- **Čo robia a kto:** OptimizerNXT spúšťa len YAML s platným `.sig`, podpísaným PFX certifikátom maintainera. Tron podpisuje `checksums.txt` PGP kľúčom. Tron Stage 8 spustí vlastné `.bat` z `stage_8_custom_scripts`.
- **Oprava po overení:** OptimizerNXT používa PFX, nie PGP, a **nepovolí** dielni vlastné nepodpísané skripty. Súbor v Trone sa volá checksums.txt, nie SHA256SUMS. Windows Repair Toolbox a privacy.sexy sú *neoverené*.
- **Stav v PortableFix:** `integrity.py` porovnáva hashe s `Data/SHA256SUMS`, ale pri nezhode len varuje. Updater vyžaduje `.sha256` asset z toho istého release. Nič nie je kryptograficky podpísané. Vendor DLL sa nekontrolujú. Vlastná YAML v `Modules/` spustí varovanie o integrite a updater ju zmaže. Exe je podpísané len self-signed certifikátom.
- **Prečo to technikovi pomôže:** USB putuje cez desiatky cudzích PC. Kto vie upraviť YAML, vie aj pregenerovať SHA256SUMS.
- **Návrh:** ed25519 podpis SHA256SUMS a release `.sha256` (verejný kľúč zabudovaný, PyNaCl alebo minisign), overenie pred inštaláciou aj pri štarte. Vendor/ zahrnúť do kontroly. Blokovať moduly so zlým hashom (s explicitným overridom). `UserModules/` mimo priečinkov, ktoré updater nahrádza, s odznakom „custom“ v UI aj reporte.
- **Náročnosť:** M · **Riziko:** 1 · **Hodnota:** 3
- **Zdroje:** github.com/hellzerg/optimizerNXT/blob/main/README.md · raw.githubusercontent.com/bmrf/tron/master/README.md · privacy.sexy desktop-vs-web-features.md

### 31. G21 – Headless CLI s exit kódmi · skóre 1,5 (riziko 2)
- **Čo robia a kto:** Tron: 0 OK, 1 chyba, 2 varovanie, 3 nepodporovaný OS, 4 čaká sa na reštart, 5 spustené z temp (**overené**). UniGetUI má CLI s dokumentovanými exit kódmi (**overené**). BCU-console `/Q /U /J` a dry-run, WinUtil `-Config` a CCleaner `/AUTO` sú *neoverené*.
- **Stav v PortableFix:** `main.py` nemá argparse a `sys.argv` ide len do QApplication.
- **Prečo to technikovi pomôže:** Spustenie cez vzdialenú reláciu alebo RMM, opakovaná kontrola a presety bez dozoru.
- **Návrh:** Po vyčlenení ActionService z `main_window.py`: `PortableFix.exe --preset <meno|súbor.json> [--live] [--out dir] [--accept-risk MODERATE] --job-client X`. Predvolene dry-run, DESTRUCTIVE len s explicitným povolením, rovnaký audit, report aj undo. Exit kódy 0, 1, 2, 3, 4 podľa Tronu. Export a import presetov do JSON.
- **Náročnosť:** M (po refaktore) · **Riziko:** 2 · **Hodnota:** 3
- **Zdroje:** Tron README (Script exit codes) · github.com/Devolutions/UniGetUI/blob/main/docs/CLI.md · BCU releases v6.3

### 32. G28 – Orchestrátor second-opinion skenerov, sťahovaných na požiadanie a overených · skóre 1,5 (riziko 2)
- **Čo robia a kto:** COOLForge `Run-AllScanners.cmd` spustí MRT a KVRT (s `-d` log priečinkom) a exit kódy mapuje na PASS alebo ALERT. pupontech `Get-AVTools.ps1` sťahuje „latest“ KVRT a ESET Online Scanner. Tron (stage 3) má skenery pribalené.
- **Oprava po overení:** **Celý** pipeline nemá žiadny konkurent. Kontrola Authenticode podpisu, sťahovanie AdwCleaner a MSERT a výsledok REBOOT neboli preukázané. Je to skôr príležitosť urobiť to lepšie ako zdokumentovaná funkcia konkurencie.
- **Stav v PortableFix:** Nič (grep adwcleaner, kvrt, msert a eset vracia len výskumné dokumenty).
- **Návrh:** Modul m24_scanners. Každá akcia stiahne jeden nástroj do `Vendor/cache` (alebo použije kópiu dodanú technikom), overí `Get-AuthenticodeSignature` voči pripnutému signerovi a spustí ho v režime len skenovania: AdwCleaner `/eula /scan /path <job>`, MSERT `/N /Q`, KVRT `-accepteula -dontencrypt -d <job>`. Čistenie je samostatná HIGH akcia. Log sa parsuje do findings (G02) a surový log ide do ZIP. **Nie** TDSSKiller. Pri každom nástroji poznámky k licencii a regiónu (Kaspersky v USA).
- **Náročnosť:** M · **Riziko:** 2 · **Hodnota:** 3
- **Zdroje:** github.com/bmrf/tron/…/stage_3_disinfect.bat · github.com/coolnetworks/COOLForge/…/Run-AllScanners.cmd · github.com/pupontech/screenconnect-cleanup/…/Get-AVTools.ps1

---

## Kde sme už lepší

1. **Dry-run, úrovne rizika, `undo.ps1`, bod obnovy a audit v jednom nástroji.** Z preskúmaných nástrojov má skutočný dry-run len Tron. Undo pre jednotlivé zmeny majú len WinUtil, Win11Debloat a Sophia. Nikto nekombinuje 4 úrovne rizika, dry-run zapnutý v predvolenom stave, automatický bod obnovy s obídením 24 h throttlingu (vrátane obnovenia throttle nastavenia aj pri zabitom procese a zapísania sekvenčného čísla), LIFO `undo.ps1` so zoznamom toho, čo sa vrátiť nedá, a JSONL audit, ktorý cituje aj odmietnuté varovania. Tým sa vyhýbame zlyhaniam hláseným v BCU #734 a #755 a v SDIO.
2. **Klientsky dvojjazyčný HTML/JSON report s handoff ZIP.** Offline, s job detailmi, snapshotom pred a po, sekciou Safety, porovnaním s predošlou návštevou, tlačovými štýlmi a ZIP s `undo.ps1` a README v SK a EN. Bezplatná konkurencia ponúka textové logy (Tron, WinUtil, AdwCleaner, KVRT), surové HTML (Belarc) alebo nič (Hiren's, Medicat). d7x a RMM to majú len za predplatné. Reporting je naprieč kategóriami najslabšie miesto konkurencie.
3. **Žiadny agent, licenčná kontrola, služba ani telemetria.** CCleaner 7 inštaluje ~7 služieb a portable verziu opustil. Ashampoo opakovane overuje licenciu online. PC Manager je len v Store a regionálne obmedzený. Veeam, Revo Pro a Patch My PC 5.x sa inštalujú. Pre firemných klientov je to silný argument dôvery. (Aby platil úplne, treba G33.)
4. **Žiadny registry cleaner a žiadne scareware skóre.** Microsoft registry cleanery nepodporuje a FTC udelila Restoro/Reimage pokutu $26M za scareware. Treba to povedať v UI aj marketingu a zosúladiť s tým aj dashboard (G02). Dnešné skóre je v tomto ohľade slabé miesto.
5. **Deklaratívny YAML katalóg s transparentnými príkazmi.** Všetkých 229 akcií ukazuje presný PowerShell, preview aj undo ešte pred behom a vyhľadávanie ide aj cez text príkazov. To je viac ako „copy command“ vo FixWin, na úrovni WinUtil alebo privacy.sexy, a bez potreby internetu. UVK, Tweaking.com, Glary a IObit sú uzavreté a nepriehľadné.
6. **Windows 10 aj 11 a slovenčina.** WinUtil Win10 vypustil, Process Explorer 17.14 a Sigcheck už oficiálne cielia na Win11+ a Agent in Settings nevie po slovensky a vyžaduje Copilot+ PC. Plné SK/EN UI aj katalóg vynútené testami sú vzácne. UWT, Winaero, FixWin a Tron sú len anglicky.
7. **Ochrana seba a integrity.** SHA256 kontrola integrity pri štarte bez nasledovania junctionov, ochrana proti zmazaniu seba samého pri čistení temp, integrity guard počas batchu a pripnutý updater s hashom a rollbackom. WinUtil beží vždy na najnovšom vzdialenom kóde (`irm | iex`). Medicat overuje len MD5. (Priestor na zlepšenie: G32.)
8. **Moderná cesta k HW senzorom bez WinRing0.** LibreHardwareMonitorLib a PawnIO inštalované až po kontrole signera, s undo. Vyhýbame sa alertom Defendera `VulnerableDriver:WinNT/Winring0` na klientskych PC.
9. **Široké cielené opravy v minútach, nie hodinách.** 22 modulov: Office C2R, WMI, reset WU, sieťový stack, tlač, BCD a safe mode, ovládače cez WU, browser hijack, audit perzistencie (WMI subscriptions, IFEO, unquoted paths), debloat, to všetko v jednej fronte s presetmi. Tron beží 3–10 h, Windows Repair Toolbox je len launcher a FixWin robí jeden klik naraz.

---

## Kde môžeme byť lepší než konkurencia

| Príležitosť | Slabina konkurencie | Čo urobiť v PortableFix | Súvisiace gapy |
|---|---|---|---|
| **Undo, ktoré vráti stav tohto PC** | WinUtil zapisuje statickú `OriginalValue` (predvolenú hodnotu Windows), privacy.sexy `revertCode` tiež. Rollback v khairudinfahmi je `reg import`, ktorý nevie zmazať hodnoty, ktoré sám vytvoril. Reset v UWT a Winaero je neúplný. | Zachytiť živé hodnoty pred zmenou a generovať presné inverzie vrátane zmazania. Marketing: *„undo vráti váš PC tak, ako bol, nie do továrenského nastavenia.“* | G10 |
| **Poctivé, vysvetlené verdikty** | Fortect „opraví 505 položiek“ na čistej inštalácii. IObit AI Mode je nepriehľadný. PC Manager označí iné vyhľadávanie ako Bing za „treba opraviť“. CrystalDiskInfo má otvorené issues s falošnými verdiktmi (#312, #318). | Každý finding s dôkazom, pravidlom a stavom „Neznáme“, keď dáta chýbajú (USB mostík, VM). Žiadne súhrnné číslo bez vzorca. V reporte: „žiadne nálezy ≠ garancia čistého stroja“. | G02, G13, G07 |
| **Opravy tlače a NAS bez oslabenia bezpečnosti** | Najpopulárnejší fixer (khairudinfahmi) vypína SMB signing na klientovi aj serveri, zapína guest, anonymous a blank-password prístup a vypína RPC privacy, a to trvalo cez SYSTEM boot tasky, ktoré prežijú aj jeho vlastný rollback. | Najprv diagnóza, potom uvoľnenie len na strane klienta s undo na presný stav. Nová akcia `sec_downgrade_audit`, ktorá odhalí downgrady a perzistentné tasky po iných nástrojoch a ponúkne cielené znovusprísnenie. | G26 |
| **Odinštalácia a čistenie, ktoré nerozbije stroj** | BCU #734: hlboké čistenie zmazalo nesúvisiace kľúče a sľúbený bod obnovy neexistoval. RAPR #353: PC nenabootovalo po odstránení ovládača. „Run all repairs“ v Tweaking.com má hlásenia o nefunkčných strojoch. | Predvybrať len zvyšky s vysokou istotou, `reg export` a kôš, overiť sekvenčné číslo bodu obnovy pred deštruktívnym krokom a ukázať ho v potvrdení (overenie už máme). | G15, G01, G24 |
| **Zachovanie dôkazov** | Tron predvolene maže event logy. Cleanery mažú minidumpy a temp inštalátory pred diagnózou. pupontech upozorňuje, že čistenie pred dezinfekciou ničí dôkazy. | Poradie „malware triáž“ a „crash triáž“: read-only audity a snapshot → skeny → opravy → čistenie ako posledné. `crash_dumps` najprv archivuje do handoff. | G06, G19 |
| **Rozhodnúť o image ešte pred opravou** | Rescuezilla, Clonezilla a HDDSuperClone sú silné, ale offline a bez klientskeho výstupu. Disk2vhd nezvláda BitLocker. Macrium Free skončil. | SMART brána, karta „najprv image“, export BitLocker kľúča, manifest zálohy a zápis výsledku do reportu (vrátane % zachránených dát, ak sa importuje ddrescue mapfile). | G13, G14 |
| **Aktuálnosť bez rizika v dodávateľskom reťazci** | WinUtil a Win11Debloat používajú `irm \| iex`. Medicat hlási 31 AV enginov. Existujú falošné zrkadlá NirLauncher, Flyoobe a Winaero. BCU prestal podpisovať kód. | ed25519 podpísaný manifest, verejná stránka „len oficiálne zdroje“, zoznam komponentov tretích strán a skutočný certifikát na podpis kódu (napr. cez SignPath Foundation, ako RAPR) namiesto self-signed. | G32 |
| **Krátke, cielené behy s jasným koncom** | Tron beží 3–10 h s pokynom „nerušiť“ a Malwarebytes krok vyžaduje manuálny klik. | Presety na minúty, reboot-resume a keep-awake len tam, kde treba. Súhrn na konci presne povie, čo ešte čaká na reštart alebo ručný krok. | G03, G12 |
| **Použiteľné UI pre hustú technickú prácu** | Redizajn UniGetUI 2026 odradil používateľov, UVK je zastaraný, Sophia a cjee21 sú .ps1 alebo 13 samostatných .cmd súborov. | Zachovať husté Qt UI ovládateľné klávesnicou. Pridať filtračné čipy (kategória, riziko), jednu revíznu obrazovku a zbalenie „zobraz len to, čo treba riešiť“ v štýle Flyoobe. | G12, G09 |
| **Legálne komerčné použitie u klienta** | Belarc, HWiNFO Free, WhoCrashed Home, Glary Free a Tweaking.com Free sú len pre osobné alebo nekomerčné použitie. d7x je predplatné na technika. | **Pridať LICENSE** (repo ho dnes nemá) s explicitným povolením komerčného servisu. Pribaľovať len redistribuovateľné komponenty (CrystalDiskInfo MIT, LibreHardwareMonitor MPL, Memtest86+ GPL), ostatné sťahovať za behu z oficiálnych URL. | G28 |
| **Secure Boot verdikt, ktorý nikto nemá** | CheckCA2023 aj MS skript ukazujú surové panely alebo PASS/ACTION NEEDED, nie diagnózu príčiny. | Jeden stav s príčinou a ďalším krokom v SK a EN. Toto je jediné miesto, kde môžeme byť v roku 2026 **prví**. | G07 |

---

## Čo NEkopírovať

1. **Registry cleanery a defrag registrov.** Microsoft ich nepodporuje, majú vysoké riziko a nemerateľný prínos a sú hlavným dôvodom nedôvery voči CCleaneru, Wise, Glary, Ashampoo a IObit. Nerobiť ani plošné „Basic repair“ resety.
2. **Falošné alebo nafúknuté skóre a počty „problémov“.** „505 issues“ vo Fortecte a pokuta FTC $26M pre Restoro/Reimage. Každé číslo musí mať pravidlo. Dnešné `max(40, 100−10x)` nahradiť (G02).
3. **Generické databázy ovládačov a komunitné driver packy.** SDIO a SamLab sú repackované a platené driver updatery tlačia nesprávne „novšie“ ovládače. Ovládače len z Windows Update a od OEM. Nikdy nemazať násilím boot-kritické alebo používané ovládače (RAPR #353).
4. **Plošné resety ACL a oprávnení a „run all repairs“.** Príčina hlásení o nefunkčných strojoch v Tweaking.com. Opravy majú byť cielené a podmienené diagnózou.
5. **Bezpečnostné downgrady ako predvolená cesta a ich trvalé opätovné aplikovanie.** Vypnutý SMB signing na serveri, guest alebo anonymous prístup, vypnutá RPC privacy, vypnuté PrintNightmare mitigácie alebo vypnutý Defender (Optimizer). Nikdy predvolene a nikdy cez boot tasky.
6. **`irm | iex` bootstrap a správanie „vždy najnovšie“.** Vzdialený kód na klientskom PC, ktorý sa medzi návštevami mení. Zdokumentovaná sťažnosť na WinUtil (#1873). Buildy držať pripnuté a podpísané.
7. **Bundleware, upsell a telemetria v predvolenom stave.** IObit, ponuky v inštalátore Glary, služby CCleanera 7 a znovu zapnuté telemetrické tasky, telemetria v Patch My PC. Zničilo by to pozíciu „nenechávame nič za sebou“.
8. **„Boostery“ pamäte a rezidentné optimalizátory.** PC Manager Boost a ASC Auto RAM Clean sú v podstate len rýchlejší reštart. Rezidentní agenti sú v rozpore s portable modelom. `tune_clear_working_sets` jasne označiť ako dočasné.
9. **Agresívna chirurgia debloatu (odstránenie CBS balíkov, vlastné servicing balíky).** Prístup RemoveWindowsAI Sophia označuje za škodlivý, lebo rozbíja servicing. Používať len podporované politiky. Odstránenie OneDrive na synchronizovanom účte zmazalo používateľom Desktop a Documents (Optimizer FAQ), takže pred ním treba kontrolu synchronizácie. **To sa týka aj našej m13 akcie.**
10. **Mazanie event logov a crash dumpov pred diagnózou.** Predvolené správanie Tronu ničí dôkazy. Najprv diagnostika a archív, deštruktívne čistenie logov a dumpov opt-in a ako posledné.
11. **Pribaľovanie nástrojov, ktoré licencia zakazuje šíriť, a nástroje na obnovu hesiel.** Licencia Sysinternals zakazuje redistribúciu. NirSoft password tools a TDSSKiller spúšťajú EDR a prinášajú právne riziko. Medicat má 31 AV detekcií. Sťahovať za behu z oficiálnych URL alebo vynechať.
12. **Kitchen-sink multiboot ISO a vlastný bootloader.** Medicat má 30 GB+, šedú licenčnú zónu a zlú AV reputáciu. Ventoy nesie binárne bloby a záťaž s údržbou Secure Boot. Radšej zdokumentovať, ako PortableFix používať popri Hiren's a Ventoy.
13. **Surový shell alebo voľné príkazy pre AI agentov.** Windows-MCP a PowerShell.MCP poskytujú plný shell bez náhľadu, undo aj auditu. Akékoľvek agent rozhranie len nad kurátorovaným katalógom a so schválením človekom pre všetko nad SAFE (G27).
14. **Hodinové „fix everything“ behy bez dozoru.** 3–10 h behy Tronu s pokynom „nerušiť“ frustrujú a nesú riziko. Uprednostniť krátke, skontrolované, cielené batche.
15. **Heslá v plain texte a automatický upload logov.** Tron ukladá SMTP heslá do plain-text XML a má `-udl` upload. QMR nastavenia vypíšu Wi-Fi heslo (G18 ho musí redigovať). Nikdy neukladať prihlasovacie údaje a logy vždy redigovať (G20).

---

## Odporúčaná roadmapa

Rozsah vĺn predpokladá jedného maintainera. Časové odhady sú orientačné.

### Vlna 1 – rýchle výhry (0–4 týždne, všetko S, riziko 1)

| # | Položka | Prečo teraz |
|---|---|---|
| 1 | **G07** Secure Boot CA 2023 verdikt (zatiaľ len SAFE status) | Termín 19. 10. 2026; príležitosť byť prví |
| 2 | **G22** `Remove-PfSafe` + CI test junction/symlink – **hotovo** | bezpečnostná chyba rovnakej triedy ako CVE-2026-55567 |
| 3 | **G24 + G01** bod obnovy podľa efektu akcie, aj pre panely Uninstaller a winget; `reg save` hive ako fallback | lacné, uzatvára skutočnú dieru (m13 bez bodu obnovy) |
| 4 | **G11** pre-flight brána (+ vynútenie `requires_admin`) | zabráni opravám na PC s čakajúcim reštartom alebo plným diskom; banner začne platiť |
| 5 | **G12** jedna revízna obrazovka batchu | 8 dialógov → 1, silnejšie bezpečnostné brány |
| 6 | **G16** winget: tri stavy + parsovanie nezávislé od jazyka | dnes falošné „všetko OK“ na SK Windows v klientskom reporte |
| 7 | **G18** WinRE/QMR status + SrtTrail | predpoklad pre Defender Offline a Reset PC |
| 8 | **G33** tichý/offline režim | ping 8.8.8.8 každé 4 s na firemnej sieti klienta |

*Bonus, ak zostane čas pred 13. 10.:* **G29** ESU status (najprv s čítaním HKCU aktuálneho používateľa, po G25 správne). Popri tom **G03 keep-awake** (jeden riadok ctypes, samotný resume až vo vlne 2) a **pridať súbor LICENSE**.

### Vlna 2 – stredné (1–3 mesiace)

| # | Položka | Poznámka |
|---|---|---|
| 1 | **G13** disk verdikt + brána „najprv image“ (`disk_stress: heavy`) | chráni dáta klienta; verdikt môže začať bez celého G02 |
| 2 | **G06** crash triáž (bugcheck, WHEA, Reliability, archív dumpov) | veľká hodnota, nízke riziko |
| 3 | **G03** reboot-resume batchu | uzavrie rozdelené reporty pri chkdsk, WU a DISM |
| 4 | **G14** externá záloha s VSS a SHA-256 manifestom | nadväzuje na G13 a súhlas v G20 |
| 5 | **G19** artefakty v handoff ZIP (`PFARTIFACT:`) | predpoklad pre G06 archív a G17 battery XML |
| 6 | **G08** Defender full, offline scan, história hrozieb, PUA | offline scan až po G18 |
| 7 | **G26** trieda tlačových ovládačov, WPP, PrintBRM, SMB report | aktuálne kvôli zmenám tlače z júla 2026 a 24H2 SMB |
| 8 | **G25** cieľový používateľ (HKU\<SID>) | predpoklad pre G29, G31 a správnosť m13 a m09 |

### Vlna 3 – veľké a architektonické (3–6+ mesiacov)

| # | Položka | Poznámka |
|---|---|---|
| 1 | **G10** deklaratívne `ops:` s undo na pôvodnú hodnotu | najväčšie USP proti WinUtil; zvýši pokrytie undo z 53 akcií |
| 2 | **G05** `items_command` / `apply_command` (fixlist) | odomyká G04 krok 2, časti G26, G15, G23 a vlastné akcie |
| 3 | **G04** autoruns: signed view (krok 1 sa dá už vo vlne 1–2), potom reverzibilné vypnutie | krok 2 závisí od G05 |
| 4 | **G02** findings (`PFJSON`) + verdikty podľa oblastí, zrušenie skóre 100−10x | rámec pre G13, G28 a report „Súhrn pre klienta“ |
| 5 | **G09** `check_command` + drift medzi návštevami | nadväzuje na G10 (probe je vedľajší produkt) |
| 6 | **G15** tiché MSI, NSIS a Inno + hodnotené zvyšky | |
| 7 | **G20** intake, outtake, čas, branding, redakcia | redakcia (GDPR) je možná aj skôr ako samostatný kus |
| 8 | **G32** ed25519 podpis manifestu a updatu + `UserModules/` | dôvera pri USB, ktoré putuje medzi PC |

### Backlog (keď bude kapacita)
- **G17** karta zdravia HW (battery wear najskôr, je to S),
- **G23** všetky profily prehliadačov,
- **G31** Storage Sense (po G25),
- **G27** mapovanie symptómov,
- **G21** CLI (po vyčlenení ActionService),
- **G28** orchestrátor skenerov (po G02),
- **G30** hardvérová identita a detailné porovnanie návštev (pozri Prílohu A).

---

## Prílohy

### Príloha A – Sporné a neoverené body

**Vyradené alebo zúžené medzery**

| ID / tvrdenie | Pôvodná hypotéza | Dôvod, prečo je sporné | Čo z toho zostáva |
|---|---|---|---|
| **G30** Hardvérová identita a porovnanie zdravia medzi návštevami | PortableFix neporovnáva návštevy | **Neprešlo missing_check.** `report._find_previous_report` a `_build_comparison` porovnávajú s predošlým reportom rovnakého PC (delta voľného miesta, počet akcií), `history.recent_runs` napĺňa „Nedávne behy na tomto PC“ a `snapshot.compare_snapshots` robí porovnanie pred a po v rámci behu. | Reálne medzery: (1) história je kľúčovaná podľa `socket.gethostname()`, nie podľa BIOS sériového čísla alebo SMBIOS UUID, takže po premenovaní alebo reimage sa spojenie stratí a dve PC s rovnakým menom sa zmiešajú; (2) medzi návštevami sa porovnáva len voľné miesto a počet akcií, nie SMART, batéria, autoštart ani softvér (ako Autoruns `.arn` alebo ESET SysInspector Compare). Návrh: kľúč = UUID + sériové číslo, s hostname len na zobrazenie. Malá úloha, ktorá sa hodí k G09. |
| **G01** (pôvodné znenie) | Uninstaller, winget a orphan cleanup obchádzajú dry-run, potvrdenie a audit | V kóde to už existuje (pozri G01 vyššie) | Len bod obnovy, chránené aplikácie a zatvorenie procesov |
| **G33** (missing_check) | „Žiadny polling na pozadí“ | Kontrola sa mýlila. Overil som v kóde ping každé 4 s, VPN každých 60 s a senzory každých 2,5 s. | Gap platí v pôvodnom rozsahu |

**Neoverené tvrdenia o konkurencii** (v texte označené ako *neoverené* alebo vyradené)

- UniGetUI: fallback „Pinget“ (bez zdroja, vyradené); limit histórie 1000 operácií; skip-version, pauza na batérii a zatvorenie aplikácie (známe, ale v 2026 release notes nepotvrdené).
- NinjaOne: voľnotextové pole s dôvodom pri stave zdravia.
- d7x: „každý alert otvorí nástroj na opravu“ (absolútne tvrdenie); aktuálny stav vývoja (posledné verejné poznámky 2023); ceny.
- Belarc: zdravie batérie na prvej strane (pravdepodobne nepravdivé).
- TeamViewer: anonymizácia na strane klienta pred uploadom (nepotvrdené). UVK: logo v reporte.
- Syncro: premena worksheetov na položky faktúry (timery áno, worksheety nie).
- O&O ShutUp10++ Premium: automatické opätovné aplikovanie pri drifte. Winhance: zobrazenie aktuálnej, odporúčanej a predvolenej hodnoty vedľa seba (Technical Details panel je podľa README, ale nepotvrdený).
- Win11Debloat: formát zálohy (JSON vs `.reg`); samostatný „SYSTEM mód“.
- Flyoobe: konkrétne preflight blokery (admin, 5 GB, reštart). Tron: odmietnutie behu pri čakajúcom reštarte (možno len varuje).
- Windows Repair Toolbox: detekcia prerušeného reťazca; zdroje si odporujú aj v stave projektu (3.0.4.8 aktívny vs „neoverené“).
- mcp-windbg: automatické `!analyze -v` a zoskupenie podľa bucketu (nerobí to samo).
- Päťstavová taxonómia Secure Boot (RebootPending, BlockedOEMMissingKEK…) je náš návrh, nie funkcia konkurencie.
- BleachBit: zastavenie čistenia cookies pri nečitateľnom keep-liste (bez zdroja, vyradené); Expert Mode v 6.0.0 (stránka sa nedala prečítať).
- MigrationMerlin: verifikačný report (skript sa nestiahol).
- WindowsMize `Get-QuickMachineRecoverySetting.ps1` (nestiahnuté); detail „WinRE verzia 0.0.0.0“.
- Agent in Settings: Undo a chýbajúca slovenčina.
- Vendor weby blokované proxy: NinjaOne, Atera, Syncro, RepairShopr, 0patch, ESET (vrátane EOL SysRescue Live), Clonezilla.org, Macrium, MemTest86, windows-repair-toolbox.com, patchmypc.com, ninite.com. Ceny a stavy týchto produktov sú z druhotných zdrojov.
- Microsoft PITR: „GA 23. 6. 2026“ uvádza jeden zdroj (Redmondmag), iný záznam hovorí o preview. Neoverené.
- Consumer ESU koniec 13. 10. 2026: „podľa dostupných informácií“, nie z primárneho zdroja Microsoftu.
- Niektoré citované zdroje tvrdenie nepodporujú: `man612/Windows-Printer-Sharing-Fix#repair-tiers` pri G10, `LibreHardwareMonitor StorageDevice.cs` pri G13, `CursorTouch/Windows-MCP SECURITY.md` pri G27, `hiratinspace/ScreenTail` market scan pri G20 (sú to plánovacie poznámky iného projektu).

### Príloha B – Zdroje

**Secure Boot, boot, obnova, ESU**
- https://github.com/claude-boucher/CheckCA2023
- https://github.com/cjee21/Check-UEFISecureBootVariables
- https://github.com/Azure/azure-support-scripts/blob/master/RunCommand/Windows/SecureBootCertCheck/readme.md
- https://github.com/allenhouchins/fleet-extensions/blob/main/secureboot_cert_update/README.md
- https://github.com/fleetdm/fleet/blob/main/articles/microsoft-is-rotating-every-windows-pcs-secure-boot-keys.md
- https://github.com/OSDeploy/OSD/blob/master/Public/Functions/WinRE.ps1
- https://github.com/5a9awneh/Rebuild-WinREPartition
- https://github.com/SystechConsulting/systech-itpros-microsoft/blob/main/windows/configuration/quick-machine-recovery/index.md
- https://github.com/agadiffe/WindowsMize/blob/main/src/modules/settings_app/system/private/recovery/Get-QuickMachineRecoverySetting.ps1
- https://github.com/MicrosoftDocs/SupportArticles-docs/blob/main/support/windows-client/performance/windows-boot-issues-troubleshooting.md
- https://github.com/MicrosoftDocs/SupportArticles-docs/blob/main/support/windows-client/performance/live-re-troubleshoot-windows-boot.md
- https://github.com/cscannell-inacloud/Windows-ITPro-Docs/blob/master/mdop/dart-v10/overview-of-the-tools-in-dart-10.md
- https://github.com/abbodi1406/ConsumerESU/blob/master/Consumer_ESU_Enrollment.ps1
- https://redmondmag.com/articles/2026/06/23/microsoft-makes-restore-generally-available-for-windows-11.aspx

**Bezpečnosť, antivírus, perzistencia**
- https://github.com/bleachbit/bleachbit/security/advisories/GHSA-vcjw-px28-5w94
- https://osv.dev/vulnerability/CVE-2026-55567
- https://github.com/MicrosoftDocs/windows-powershell-docs/blob/main/docset/winserver2025-ps/Defender/Start-MpWDOScan.md
- https://github.com/adamblack1111/M365D/blob/public/microsoft-365/security/defender-endpoint/microsoft-defender-offline.md
- https://github.com/dcorrada/POWERSHELL/blob/master/Safety/SafetyScan.ps1
- https://github.com/MicrosoftDocs/sysinternals/blob/main/sysinternals/downloads/autoruns.md
- https://raw.githubusercontent.com/MicrosoftDocs/sysinternals/main/sysinternals/license-terms.md
- https://github.com/rifteyy/frst-snippets
- https://www.carifred.com/uvk/help/script_commands/
- https://github.com/bmrf/tron/blob/master/resources/stage_3_disinfect/stage_3_disinfect.bat
- https://github.com/coolnetworks/COOLForge/blob/main/standalone_scripts/Remove/Run-AllScanners.cmd
- https://github.com/pupontech/screenconnect-cleanup/blob/main/tools/Get-AVTools.ps1
- https://github.com/pupontech/screenconnect-cleanup/blob/main/docs/05-tools-scanners-tron.md
- https://github.com/farag2/Sophia-Script-for-Windows/blob/main/src/Sophia_Script_for_Windows_11/Module/Private/InitialActions.ps1
- https://github.com/builtbybel/Flyoobe/tree/main/Actions/setup-preflight

**Automatizácia, undo, tweaky**
- https://raw.githubusercontent.com/bmrf/tron/master/README.md
- https://raw.githubusercontent.com/bmrf/tron/master/changelog.txt
- https://github.com/ChrisTitusTech/winutil (releases, config/tweaks.json)
- https://github.com/Raphire/Win11Debloat (releases, Config/Features.json, wiki Reverting-Changes, Advanced-Features)
- https://github.com/memstechtips/Winhance (README, Localization/en.json)
- https://github.com/hellzerg/optimizerNXT/blob/main/README.md
- https://github.com/undergroundwires/privacy.sexy/blob/master/docs/desktop/desktop-vs-web-features.md
- https://www.carifred.com/uvk/help/system_repair.php
- https://ubuntuhandbook.org/index.php/2026/04/bleachbit-6-0-0-cookie-manager-expert-mode/

**Odinštalácia, aktualizácie, ovládače**
- https://github.com/BCUninstaller/Bulk-Crap-Uninstaller/blob/master/doc/BCU_manual.html
- https://github.com/BCUninstaller/Bulk-Crap-Uninstaller/issues/734
- https://github.com/BCUninstaller/Bulk-Crap-Uninstaller/releases/tag/v6.3
- https://github.com/Devolutions/UniGetUI/releases/tag/v2026.3.0
- https://github.com/Devolutions/UniGetUI/releases/tag/v2026.2.6
- https://github.com/Devolutions/UniGetUI/blob/main/docs/CLI.md
- https://github.com/Devolutions/UniGetUI/blob/main/docs/PORTABLE.md
- https://github.com/Romanitho/Winget-AutoUpdate
- https://github.com/microsoft/winget-pkgs/commit/52659a3d65c4151846bfd38659ebbe7361c91c8d
- https://github.com/lostindark/DriverStoreExplorer
- https://sourceforge.net/p/snappy-driver-installer-origin/code/HEAD/tree/trunk/docs/changelog.txt

**Diagnostika HW, disky, pády**
- https://github.com/hiyohiyo/CrystalDiskInfo/blob/master/AtaSmart.cpp
- https://github.com/mruffalo/ddrescue/blob/master/doc/ddrescue.texi
- https://github.com/stevenshiau/clonezilla/blob/master/scripts/sbin/ocs-functions
- https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
- https://raw.githubusercontent.com/microsoft/winget-pkgs/master/manifests/r/Resplendence/WhoCrashed/7.10/Resplendence.WhoCrashed.locale.en-US.yaml
- https://raw.githubusercontent.com/microsoft/winget-pkgs/master/manifests/n/NirSoft/BatteryInfoView/1.27/NirSoft.BatteryInfoView.locale.en-US.yaml
- https://github.com/pdqcom/PowerShell-Scanners/blob/master/PowerShell%20Scanners/System%20Stability/System%20Stability.ps1
- https://github.com/svnscha/mcp-windbg/blob/main/docs/scenarios/triage.md
- https://github.com/microsoft/wmi/blob/master/server2019/root/cimv2/Win32_ReliabilityStabilityMetrics.go
- https://github.com/Banani-Rath/commercialization-public/blob/master/design/device-experiences/powercfg-command-line-options.md
- https://github.com/smartmontools/smartmontools/blob/master/smartmontools/NEWS

**Zálohy a migrácia**
- https://github.com/SystechConsulting/systech-itpros-microsoft/blob/main/windows/deployment/usmt/usmt-scanstate-syntax.md
- https://github.com/supermarsx/migration-merlin/blob/main/scripts/post-migration-verify.ps1
- https://github.com/rescuezilla/rescuezilla
- https://github.com/thesourcerer8/hddsuperclone

**Tlač a sieť**
- https://github.com/man612/Windows-Printer-Sharing-Fix (docs/WPP-READINESS.md, docs/MODERN-SMB-RPC.md)
- https://github.com/khairudinfahmi/WindowsPrinterSharingFix
- https://github.com/MicrosoftDocs/windows-driver-docs/blob/staging/windows-driver-docs-pr/print/end-of-servicing-plan-for-third-party-printer-drivers-on-windows.md

**Reporting, RMM, dielne**
- https://github.com/jstott/postman-ninjarmm/blob/master/lib/NinjaRMM-API-v2.yaml
- https://www.d7xtech.com/d7x/manual/d7x-system-info/
- https://github.com/omiinaya/mcp-repairshopr/blob/main/docs/api/ticket.md
- https://github.com/watchmanmonitoring/wm-syncromsp-swagger-client/blob/master/docs/WorksheetResultApi.md
- https://github.com/amidaware/trmm-docs/blob/main/docs/ee/reporting/functions/reporting_basics.md
- https://github.com/MicrosoftDocs/windowsserverdocs/blob/main/WindowsServerDocs/administration/windows-commands/msinfo32.md

**Cleanery**
- https://github.com/bleachbit/bleachbit/releases/tag/v6.0.2
- https://support.ccleaner.com/s/article/retain-cookies-in-ccleaner-7?language=en_US
- https://community.ccleaner.com/t/portable-version-for-ccleaner-7-on-windows-11/158146
- https://www.elevenforum.com/t/ccleaner-7-warning.40596/
- https://www.cyberdrain.com/monitoring-with-powershell-monitoring-storage-sense-settings/
- https://github.com/kaspersmjohansen/StorageSense/blob/main/Configure-StorageSense.ps1
- https://www.wisecleaner.com/blog_2026_05_18_wisecare365_1288.html

**AI a MCP**
- https://github.com/MicrosoftDocs/learn/tree/main/learn-pr/device-partner-university/agent-in-settings
- https://github.com/MicrosoftDocs/windows-ai-docs/blob/docs/docs/mcp/overview.md
- https://github.com/CursorTouch/Windows-MCP
- https://github.com/yotsuda/PowerShell.MCP
- https://github.com/Servosity/msp-skills

**Oficiálne stránky nástrojov z prehľadovej tabuľky** (výber): tweaking.com · thewindowsclub.com/fixwin · windows-repair-toolbox.com · d7xtech.com · learn.microsoft.com (GetHelpCmd, Sysinternals) · carifred.com/uvk · oo-software.com/shutup10 · winaero.com · github.com/builtbybel/Flyoobe · github.com/zoicware/RemoveWindowsAI · ccleaner.com · bleachbit.org · wisecleaner.com · glarysoft.com · ashampoo.com · iobit.com · pcmanager.microsoft.com · privazer.com · hirensbootcd.org · medicatusb.com · launcher.nirsoft.net · portableapps.com · github.com/ventoy/Ventoy · malwarebytes.com (AdwCleaner, Toolset) · emsisoft.com · kaspersky.com (KVRT, KRD) · eset.com · hitmanpro.com · adlice.com · github.com/winsiderss/systeminformer · patchmypc.com · ninite.com · revouninstaller.com · github.com/Wagnard/display-drivers-uninstaller · hwinfo.com · crystalmark.info · aida64.com · ccleaner.com/speccy · ocbase.com · nirsoft.net · resplendence.com · memtest86.com · github.com/memtest86plus/memtest86plus · hdsentinel.com · ninjaone.com · atera.com · syncromsp.com · repairshopr.com · belarc.com · github.com/amidaware/tacticalrmm · macrium.com · veeam.com · easyuefi.com · cgsecurity.org · dmde.com · forensit.com · laplink.com · 0patch.com · github.com/eltonaguiar/BOOTFIXPREMIUM_CURSOR · github.com/andago9/BAIOS
