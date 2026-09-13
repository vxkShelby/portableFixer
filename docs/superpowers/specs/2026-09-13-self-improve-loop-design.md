# PortableFix — Design: Self-improve slučka (dev-side "duša")

Rozsah: nová **vývojová** schopnosť projektu, nie súčasť shippovanej
appky. Nič z tohto nebeží u koncového užívateľa PortableFix.exe —
celá slučka žije v repo a beží len na vývojovom stroji.

## Rozhodnutia z brainstormingu

- **Len dev-side.** Archon/self-edit slučka je vývojový nástroj, nie
  runtime feature appky. Shippovaná appka sa nemení.
- **Trigger: automaticky po každom commite/push**, cez git hook —
  ale **signálom-riadené**, nie "vždy spusti ťažkú slučku". Ak hook
  nenájde žiadny reálny problém, Archon sa vôbec nezavolá.
- **Nikdy auto-merge.** Výstup vždy pristáva na novej branch, nikdy
  priamo do `main`. Človek (užívateľ alebo Claude v session) vždy
  rozhoduje merge/zahodiť.
- **Zdroje signálu** (žiadna telemetria/analytics od koncových
  užívateľov — appka nič neposiela domov):
  1. výsledky `pytest` (padajúce testy, nové warnings)
  2. `git log` pattern — opakované `fix:` commity dotýkajúce sa toho
     istého súboru v poslednom okne (napr. posledných 20 commitov)
  3. delta v `Logs/crash.log` (nové záznamy od posledného behu slučky)
- **Pamäť/identita: `docs/SOUL.md`, commitnutý do gitu.** Rastúci
  denník — nie config, nie DB. Zapisuje sa LEN beh so signálom (Archon
  sa reálne zavolal); tichý beh (nič nenašiel) sa loguje iba do
  `Logs/self_improve.log`, nie do SOUL.md — inak by SOUL.md rýchlo
  zaplnil šum z každého commitu.

## Architektúra

```
git commit/push
      │
      ▼
.githooks/post-commit (trackovaný v repo)
      │  (spustí na pozadí, neblokuje commit)
      ▼
scripts/self_improve.py  — "signal gatherer"
      │
      ├─ pytest --tb=no -q  → parsuje počet failov
      ├─ git log --oneline -20 → počíta opakovania súboru vo fix-commitoch
      └─ Logs/crash.log → diff od posledného zaznamenaného offsetu
      │
      ▼
  signál nájdený? ──ne──► zapíš "quiet" riadok do docs/SOUL.md, koniec
      │ áno
      ▼
archon workflow run archon-implement
  (providers.claude — existujúca claude CLI session, žiadny API kľúč)
  kontext: čo presne signál gatherer našiel (failing test names,
  súbor s opakovanými fixmi, nový crash traceback)
  max 5 iterácií (rovnaký cap ako fix-loop v subagent-driven-development)
      │
      ▼
výstup na branch `self-improve/<YYYY-MM-DD>-<slug>`
      │
      ▼
append záznam do docs/SOUL.md:
  dátum, čo spustilo beh, čo Archon zmenil (súbory), výsledok testov
  na novej branch, meno branch, stav = "čaká na review"
      │
      ▼
človek prezrie branch (git diff / PR), rozhodne merge alebo zahodiť
      │
      ▼
záznam v docs/SOUL.md sa doplní o rozhodnutie (merged/discarded + dátum)
```

## Komponenty

### 1. `.githooks/post-commit`
Shell/PowerShell skript trackovaný v repo (nie `.git/hooks/`, ktorý sa
necommituje). Aktivácia je jednorazový lokálny setup:
`git config core.hooksPath .githooks`. Hook iba spustí
`scripts/self_improve.py` **odpojene od terminálu** (background
process, presmerovaný stdout/stderr do `Logs/self_improve.log`), aby
`git commit` nikdy nečakal na dokončenie slučky.

### 2. `scripts/self_improve.py` — signal gatherer
Čistá Python logika, testovateľná bez Qt/gitu naživo (funkcie berú
vstup ako parametre, nie priamo `subprocess.run` volania rozhádzané
všade — aspoň tá časť čo rozhoduje "je signál/nie je signál" musí byť
čistá funkcia s jednotkovým testom).

Zodpovednosti:
- spusti `pytest`, zrátaj fail count
- prejdi posledných N git commitov, nájdi súbor dotknutý ≥3× v `fix:`
  commitoch
- načítaj `Logs/crash.log`, porovnaj s uloženým offsetom v
  `docs/.self_improve_state.json` (posledný spracovaný byte offset —
  jednoduchý spôsob "čo je nové od minula")
- ak nič z toho nenašlo signál → zapíš tichý riadok do SOUL.md
  (voliteľné, pozri "Otvorené otázky") a skonči
- ak signál → zostav krátky kontext text a zavolaj `archon workflow
  run archon-implement --context <text>` (presný tvar CLI argumentov
  sa musí overiť pri `archon doctor`/inštalácii — pozri riziká nižšie)

### 3. `docs/SOUL.md`
Markdown, append-only štýl (nové záznamy na koniec alebo na začiatok —
rozhodneme pri review). Formát jedného záznamu:

```markdown
## 2026-09-14 — self-improve run

**Trigger:** 2 failing tests v tests/test_executor.py po commite abc123
**Archon návrh:** oprava race condition v ActionRunner.cancel()
**Branch:** self-improve/2026-09-14-executor-race-fix
**Testy na branch:** 494 passed
**Rozhodnutie:** _(čaká na review)_
```

### 4. Bezpečnostné zábrany
- žiadny auto-merge, žiadny auto-push do `main`/`origin` bez človeka
- max 5 iterácií Archon slučky (zabráni nekonečnému/drahému behu)
- ak Archon po 5 iteráciách nedosiahne zelené testy, branch sa aj tak
  vytvorí (na diagnostiku), ale SOUL.md záznam jasne označí "needs
  human — tests still red"
- hook beží len lokálne u vývojára, nikdy v CI (aby sa nezdvojoval a
  neplatilo sa za claude usage v CI behoch)

## Testovanie

- `scripts/self_improve.py`: jednotkové testy pre `has_signal(...)`
  logiku (mockované vstupy — zoznam commitov, fail count, crash log
  diff), žiadne skutočné volanie pytestu/archonu/gitu v testoch.
- `.githooks/post-commit`: netestuje sa automatizovane (shell skript,
  jeden riadok), over sa manuálne pri prvom nasadení.
- `docs/SOUL.md` writer: jednotkový test že append vytvorí správne
  formátovaný záznam.

## Otvorené otázky / riziká na overenie pred plánom

1. **Presné správanie `archon workflow run`** (accepted flags, ako sa
   mu odovzdáva kontext, čo presne robí `archon-implement` workflow
   oproti `archon-review`) nie je overené v tomto repo — bolo overené
   v inej (Ultron) session, nie tu. Prvý implementačný task musí byť
   `archon doctor` + skúšobný ručný beh, skôr než sa naň stavia zvyšok
   slučky. Ak sa správanie líši od predpokladu vyššie, táto sekcia sa
   musí prepísať pred pokračovaním.
2. **Windows-špecifiká:** `.githooks/post-commit` na Windows potrebuje
   buď byť `.sh` spúšťaný cez Git Bash (git hooks fungujú cez shebang
   aj na Windows vďaka Git for Windows), alebo `.cmd`/`.ps1` wrapper.
   Over funkčnosť background/detached spustenia na Windows (`Start-Process
   -WindowStyle Hidden`, nie viditeľné cmd okno — nadväzuje na už
   existujúcu užívateľovu preferenciu "nikdy nenechávaj otvorené cmd
   okná").
