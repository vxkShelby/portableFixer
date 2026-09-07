# GitHub presentation proposal

Proposal only — nothing here has been applied. Two parts: (1) a profile
README draft for a new `vxkShelby/vxkShelby` repo, (2) concrete header
changes for `portableFixer`'s own README files. A short list of GitHub-side
settings to check is at the bottom.

## What the repo already has (local check)

- `docs/logo.png` — a logo image already exists. Use it in the README
  header instead of skipping a banner or commissioning one.
- `portablefix.ico` — app icon, already exists.
- `.github/workflows/tests.yml` — a "Tests" CI workflow on `windows-latest`,
  triggered on push/PR to `main`. This means a real, honest build-status
  badge is possible right now (not a fake/decorative one).
- No repo-root `LICENSE` file (only a vendored `Vendor/LibreHardwareMonitor/LICENSE.txt`
  for the bundled dependency). A license badge would currently be
  misleading/unresolvable — pick and add a license before adding that badge.
- No `.github/ISSUE_TEMPLATE`, no social preview image on disk (that's
  GitHub-side, see limitation note below).
- Current README tone: terse, technical, Slovak-first with an English
  mirror, no badges, no images, no marketing language — a plain module
  table and procedural sections (build, release process, dev, known
  limitations). This is a legitimate, credible style for a solo sysadmin
  tool; the goal below is to make it easier to scan at a glance, not to
  change its voice.

## GitHub-side settings this proposal can't check locally

These live in GitHub's UI/API, not in the working tree, so verify them
manually:

1. **Repo description** (top of the repo page) — short one-liner, currently
   unknown/unverified locally.
2. **Topics** (tags like `windows`, `pyside6`, `usb`, `system-repair`,
   `portable-apps`, `powershell`) — improves discoverability; check under
   repo Settings → General → Topics.
3. **Social preview image** (Settings → General → Social preview) — the
   image shown when the repo link is shared on social/Slack/Discord. Not
   set by default; `docs/logo.png` could be adapted into a 1280×640 preview.
4. **About section website link** — worth pointing at the Releases page if
   no dedicated site exists.

---

## Part 1 — profile README draft (`vxkShelby/vxkShelby`)

Ready to paste as `README.md` in a new repo named exactly `vxkShelby`
(GitHub auto-detects the special profile repo by username match). Kept
deliberately modest: no stats cards, no wall of badges, no stock "passionate
developer" copy — matches the same restrained, technical voice as the
portableFixer README.

```markdown
### vxkShelby

Solo dev building small, portable Windows tools — things that fix a root
cause instead of hiding a symptom, and that run from a USB stick without
installing anything.

**Currently building:** [PortableFix](https://github.com/vxkShelby/portableFixer)
— a portable Windows 10/11 diagnostic and repair tool (Python + PySide6,
PowerShell-driven actions, dry-run by default, full undo log and audit
trail). 22 modules covering diagnostics, cleanup, repair, security and
hardware sensors.

What I care about in my own projects:
- No installer, no background services, no telemetry — copy a folder, run it, done.
- Every destructive action is reversible or backed by a restore point.
- Dry-run first, confirm before anything MODERATE or worse.

**Stack:** Python · PySide6 · PowerShell · Windows internals (WMI, DISM,
registry, BCD)

---
📫 Reach me via GitHub issues on my repos.
```

Notes on choices:
- No GitHub stats/streak cards — for a two-repo profile they read as
  padding, and the trend for technical audiences in 2025/26 skews back
  toward "show the actual project" over stat widgets.
- No tech-stack badge row/icons grid — three plain-text stack items is more
  credible for a solo dev than a badge wall implying a large toolkit.
  This can be replaced with a shields.io row later once there are more
  repos worth signaling range across.
- One clear pinned/linked project rather than an exhaustive list — let
  pinned repos (set in the GitHub UI, see below) carry the rest.

**Not covered here (GitHub UI, not README content):** pin `portableFixer`
(and any other repos you want featured) via the profile page's "Customize
your pins."

---

## Part 2 — portableFixer README header changes

### Recommended badge row

All using shields.io, all backed by something real in this repo (no vanity
badges):

```markdown
[![Tests](https://github.com/vxkShelby/portableFixer/actions/workflows/tests.yml/badge.svg)](https://github.com/vxkShelby/portableFixer/actions/workflows/tests.yml)
[![Latest release](https://img.shields.io/github/v/release/vxkShelby/portableFixer)](https://github.com/vxkShelby/portableFixer/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/vxkShelby/portableFixer/total)](https://github.com/vxkShelby/portableFixer/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6)](https://github.com/vxkShelby/portableFixer)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB)](https://www.python.org/)
```

Deliberately **not** including a license badge until a `LICENSE` file is
added (a badge pointing at a non-existent license is worse than no badge).
Once added:

```markdown
[![License](https://img.shields.io/github/license/vxkShelby/portableFixer)](LICENSE)
```

### Before/after for `README.md` header (same pattern for `README.en.md`)

Before:

```markdown
# PortableFix

*[English version: README.en.md](README.en.md)*

Prenosný diagnostický a opravný nástroj pre Windows 10/11, určený na beh
z USB kľúča. Python 3.12 + PySide6 GUI, akcie vykonáva cez PowerShell.
```

After:

```markdown
<p align="center">
  <img src="docs/logo.png" alt="PortableFix" width="120">
</p>

<h1 align="center">PortableFix</h1>
<p align="center"><i><a href="README.en.md">English version</a></i></p>

<p align="center">
[![Tests](https://github.com/vxkShelby/portableFixer/actions/workflows/tests.yml/badge.svg)](https://github.com/vxkShelby/portableFixer/actions/workflows/tests.yml)
[![Latest release](https://img.shields.io/github/v/release/vxkShelby/portableFixer)](https://github.com/vxkShelby/portableFixer/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/vxkShelby/portableFixer/total)](https://github.com/vxkShelby/portableFixer/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6)](https://github.com/vxkShelby/portableFixer)
</p>

Prenosný diagnostický a opravný nástroj pre Windows 10/11, určený na beh
z USB kľúča. Python 3.12 + PySide6 GUI, akcie vykonáva cez PowerShell.
```

Rationale:
- `docs/logo.png` already exists — use it small and centered, not as a
  full-width marketing banner. A single-dev diagnostic tool doesn't need a
  hero banner; a modest icon + title reads as more credible, not less.
- Badge row goes under the title, not scattered inline, and stays to 4
  badges that are all true today — avoids the "AI slop" look of 8+ badges
  where half are static/fake.
- Keep the existing plain-language intro paragraph as-is; it already does
  the "why/what" job in two sentences.

### Screenshot/demo section — recommended addition

There is currently no screenshot or GIF anywhere in the repo. For a GUI
tool this is the single highest-value addition (a module table doesn't
show what the app looks like). Suggested insertion point: right after the
intro paragraph, before "Rýchly štart" / "Quick start":

```markdown
## Náhľad

![PortableFix screenshot](docs/screenshot.png)
```

(English mirror: `## Screenshot`). This needs an actual screenshot captured
and saved to `docs/screenshot.png` — not something this proposal can
generate. A short GIF of a DRY-RUN batch run + generated HTML report would
be even stronger (shows the safety story, not just the UI chrome), but a
static screenshot is the low-effort version worth doing first.

### Module table — verdict: keep it, don't restructure

The existing 22-row module table (`Modul | Kategória | Obsah`) is already
the right format — dense, scannable, sorted by module ID for cross-reference
with the code (`Modules/<id>/`). Converting it to grouped headers/collapsible
sections would look more "designed" but would break the direct ID lookup
that the rest of the docs (release notes, code) rely on. No change
recommended here beyond what's already good.

### Sections that should stay exactly as-is

"Bezpečnostné mechanizmy" / "Safety mechanisms", the release process, and
"Známe obmedzenia" / "Known limitations" are unusually honest and detailed
for a solo project — this is a strength, not a section to trim for
"looking clean." Resist the urge to shorten these for visual tidiness.
