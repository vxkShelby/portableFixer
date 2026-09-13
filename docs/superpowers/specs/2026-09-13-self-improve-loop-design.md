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
  signál nájdený? ──ne──► zapíš riadok do Logs/self_improve.log, koniec
      │ áno
      ▼
archon workflow run self-improve "<trigger text>"
  (vlastný workflow v .archon/workflows/self-improve.yaml;
  providers.claude — existujúca claude CLI session, žiadny API kľúč)
  Archon sám: izoluje beh do vlastného git worktree/branch,
  opravuje kód v slučke (max 5 iterácií, until_bash: pytest),
  commitne, pushne, otvorí PR cez `gh pr create` — NIKDY nemergne
      │
      ▼
append záznam do docs/SOUL.md:
  dátum, čo spustilo beh, PR link/branch meno, výsledok testov,
  stav = "čaká na review"
      │
      ▼
človek prezrie PR na GitHube, rozhodne merge alebo zavrieť
```

**Prečo nie manuálne vytváranie branch v našom skripte:** Archon pri
`workflow run` sám izoluje každý beh do vlastného git worktree (overené
v oficiálnej dokumentácii, nie predpoklad) — náš orchestrátor teda
nepotrebuje vlastnú `git checkout -b` logiku ani vlastný "vytvor PR"
krok, len zavolá Archon a zaznamená výsledok.

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
- ak nič z toho nenašlo signál → zapíš riadok do `Logs/self_improve.log`
  a skonči (SOUL.md sa nedotýka — pozri nižšie prečo)
- ak signál → zostav krátky text popisujúci čo sa našlo a zavolaj
  `archon workflow run self-improve "<text>"` (overený tvar príkazu,
  pozri "Archon CLI — overené fakty" nižšie)

### 2a. `.archon/workflows/self-improve.yaml` — vlastný Archon workflow
Žiadny vstavaný Archon workflow sa nevolá `archon-implement` — treba
vlastný, uložený v repo (Archon podporuje custom workflows presne
takto: `.yaml` súbor v `.archon/workflows/`, prepíše rovnomenný
vstavaný ak by existoval). Kostra (overená proti reálnej syntaxi
z dokumentácie, nie vymyslená):

```yaml
name: self-improve
description: Preskúma nahlásený signál, opraví ho, overí testami, otvorí PR. Nikdy nemerguje.

nodes:
  - id: investigate-and-fix
    loop:
      prompt: |
        Lokálny signal-gatherer skript našiel tento problém v repo:

        $ARGUMENTS

        Nájdi root cause, oprav ho. Po každej zmene spusti `pytest`.
        Drž zmeny minimálne, drž sa existujúcich konvencií repo.
      max_iterations: 5
      until_bash: "pytest --tb=no -q"
      fresh_context: false

  - id: create-pr
    depends_on: [investigate-and-fix]
    prompt: |
      Commitni zmeny (jednoriadková imperatívna správa, fix:/feat:
      prefix podľa štýlu `git log` tohto repo). Pushni branch a otvor
      pull request cez `gh pr create` proti `main`, telo PR nech
      sumarizuje aký signál to spustil a čo sa zmenilo. Nemerguj ho.
```

`max_iterations: 5` + `until_bash` je presne mechanizmus zo skutočnej
Archon dokumentácie na "slučka končí keď bash check prejde, inak po
N pokusoch" — pytest je tu ten skutočný exit kritérium, preto sa
nepoužíva textový `until:` promise (dokumentácia explicitne odporúča
nepoužívať oba naraz, keď je bash check ten skutočný gate).

### 3. `docs/SOUL.md`
Markdown, append-only štýl (nové záznamy na koniec alebo na začiatok —
rozhodneme pri review). Formát jedného záznamu:

```markdown
## 2026-09-14 — self-improve run

**Trigger:** 2 failing tests v tests/test_executor.py po commite abc123
**Archon výstup:** posledných ~500 znakov stdoutu z `archon workflow run`
(obsahuje spravidla PR link, ak sa create-pr krok dokončil)
**Testy:** 494 passed
**Rozhodnutie:** _(čaká na review)_
```

Zapisuje sa LEN beh so signálom (Archon sa reálne zavolal); tichý beh
ide iba do `Logs/self_improve.log`, nikdy do SOUL.md.

### 4. Bezpečnostné zábrany
- žiadny auto-merge — `create-pr` krok len otvorí PR cez `gh pr
  create`, nikdy nevolá `gh pr merge`; merge je vždy manuálny krok
  človeka na GitHube
- max 5 iterácií Archon slučky (`max_iterations: 5` + `until_bash`,
  zabráni nekonečnému/drahému behu)
- ak po 5 iteráciách testy stále nie sú zelené, `investigate-and-fix`
  skončí aj tak (Archon loop cap), `create-pr` beží ďalej a PR sa
  otvorí s aktuálnym (možno nedokončeným) stavom — reviewer to uvidí
  v CI/testoch na PR, žiadne špeciálne značenie netreba
- hook beží len lokálne u vývojára, nikdy v CI (aby sa nezdvojoval a
  neplatilo sa za claude usage v CI behoch)
- Archon vlastná telemetria sa vypína (`ARCHON_TELEMETRY_DISABLED=1`
  v prostredí, kde beží hook) — konzistentné s tým, že appka samotná
  tiež nič neposiela domov

## Testovanie

- `scripts/self_improve.py`: jednotkové testy pre `has_signal(...)`
  logiku (mockované vstupy — zoznam commitov, fail count, crash log
  diff), žiadne skutočné volanie pytestu/archonu/gitu v testoch.
- `.githooks/post-commit`: netestuje sa automatizovane (shell skript,
  jeden riadok), over sa manuálne pri prvom nasadení.
- `docs/SOUL.md` writer: jednotkový test že append vytvorí správne
  formátovaný záznam.

## Archon CLI — overené fakty (2026-09-14, z archon.diy/docs)

Pôvodný predpoklad (`archon-implement` workflow, `--context` flag,
manuálne vytváranie branch) bol nesprávny — Archon v2 (aktuálna verzia,
`coleam00/Archon`) funguje inak, overené priamo z oficiálnej
dokumentácie:

- **Inštalácia (Windows):** `irm https://archon.diy/install.ps1 | iex`
  (standalone binary). Binárka nebalí Claude Code — treba mať `claude`
  CLI nainštalovaný zvlášť a buď na PATH, alebo `CLAUDE_BIN_PATH`
  nastavený / `assistants.claude.claudeBinaryPath` v `~/.archon/config.yaml`.
- **`archon doctor`** — health check, exit 0 = OK, exit 1 = kritická
  chyba. `archon doctor --full` navyše skontroluje aj nepoužívané
  providery.
- **Spustenie workflow:** `archon workflow run <name> "<message>"` —
  `<message>` je celý text, dostupný vo workflow prompte ako
  `$ARGUMENTS`/`$USER_MESSAGE`. Žiadne pozičné `$1`/`$2` argumenty,
  žiadny `--context` flag.
- **Worktree izolácia je default správanie** — každý `workflow run`
  dostane vlastný git worktree/branch automaticky (`--no-worktree` by
  to vypol, ale to nechceme). Náš orchestrátor teda nikdy sám nevytvára
  branch.
- **Vlastné workflow súbory:** `.archon/workflows/<name>.yaml` v repo,
  `name:` + `nodes:` (DAG, `depends_on`), `loop:` blok s `prompt`,
  `max_iterations`, `until_bash` (deterministický shell-exit gate —
  presne to čo potrebujeme pre pytest) alebo `until:` (textový
  `<promise>` signál od AI). Dokumentácia explicitne odporúča použiť
  LEN `until_bash` keď je test suite skutočné kritérium — presne náš
  prípad.
- **PR vytvorenie nie je vstavaná Archon funkcia** — agent v prompte
  dostane inštrukciu spustiť `gh pr create` sám (Archon pre "folder"/
  git repo projekty nezasahuje do git/gh, necháva to na agenta).
- **Telemetria:** Archon posiela anonymné usage eventy, vypnuteľné cez
  `ARCHON_TELEMETRY_DISABLED=1` alebo `DO_NOT_TRACK=1` v prostredí.

## Zostávajúce riziká

1. **Windows-špecifiká:** `.githooks/post-commit` na Windows potrebuje
   buď byť `.sh` spúšťaný cez Git Bash (git hooks fungujú cez shebang
   aj na Windows vďaka Git for Windows), alebo `.cmd`/`.ps1` wrapper.
   Over funkčnosť background/detached spustenia na Windows (`Start-Process
   -WindowStyle Hidden`, nie viditeľné cmd okno — nadväzuje na už
   existujúcu užívateľovu preferenciu "nikdy nenechávaj otvorené cmd
   okná").
