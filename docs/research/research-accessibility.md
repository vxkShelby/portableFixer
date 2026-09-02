# PortableFix GUI — Accessibility Audit

Scope: `portablefix/gui/main_window.py` (697 lines), `portablefix/gui/style.py` (230 lines), `portablefix/i18n.py`,
`portablefix/report.py`, `portablefix/models.py`, `main.py`, `requirements.txt`, `scripts/build.ps1`, and all 12
`Modules/*/actions.yaml` catalogs. Read-only; no files modified.

This is a **narrow accessibility pass**, distinct from the earlier general Design/UX audit
(`docs/research/research-design-audit.md`, `research-design-additions.md`). Those two files already found and (per
git log) mostly implemented: the search box, presets, status bar, per-action status labels, cancel button, scroll
areas on both the action list and the batch-summary dialog, and the `#565f89` contrast fix. **This report does not
repeat those findings.** It focuses on keyboard-only operability, screen-reader/accessible-name exposure, color-only
signaling introduced by the newer widgets, font/DPI behavior, and Windows assistive-tech interop (High Contrast,
Narrator, Magnifier) — verified against the *current* code, which has grown since the design audit (main_window.py
was 451 lines then, is 697 now; the catalog was 82 actions, is 108 now).

Dataset used below (from `Modules/*/actions.yaml`, 12 modules, computed via a Python/PyYAML pass, not eyeballed):
- **108 actions total.** Category split: DIAGNOSTICS 24, CLEANUP 37, REPAIR 37, SECURITY 10.
- Risk split: SAFE 58, MODERATE 39, DESTRUCTIVE 7, REQUIRES_REBOOT 4.
- **All 108 actions have a non-empty `description_en`** (verified programmatically) — every action has real
  descriptive text in the codebase; see Finding 1 for why almost none of it reaches assistive tech.

Grep sweeps run across the whole repo to ground the claims below (all returned **zero matches** unless noted):
`setAccessibleName|setAccessibleDescription|QAccessible`, `HighContrast|styleHints|colorScheme|QPalette`,
`QShortcut|setTabOrder|keyPressEvent|setFocusPolicy`, `AA_EnableHighDpiScaling|HighDpi|dpiAware`. One match:
`setToolTip` (`main_window.py:273`, the only accessibility-adjacent API call in the codebase).

---

## Top findings

| # | Sev | Finding |
|---|---|---|
| 1 | **HIGH** | Every action's descriptive text (108/108 actions) is exposed **only** as a `QToolTip` — mouse-hover only, unreachable by keyboard or screen reader. |
| 2 | **HIGH** | Risk badge and running/status text are separate non-focusable `QLabel`s never tied to the checkbox's accessible name — a screen reader tabbing through actions never hears the risk tier or run status. |
| 3 | **MEDIUM-HIGH** | App-wide custom QSS (`main.py:19`, `main_window.py:111`) hardcodes every color and has no code path detecting/respecting Windows High Contrast mode — it silently overrides that OS accessibility feature everywhere except the four unstyled `QMessageBox` dialogs. |
| 4 | **MEDIUM** | The status bar — now the app's primary channel for selection count, risk level, and batch progress — updates via `showMessage()` with nothing making those changes proactively announced to a screen reader. |
| 5 | **MEDIUM** | Language toggle (`main_window.py:441-447`) tears down and rebuilds the entire central widget, resets the category tab to index 0, and sets no explicit focus afterward — a keyboard-only user loses their place on every SK/EN switch. |
| 6 | **LOW-MEDIUM** | Windows 11's dedicated "Text size" accessibility slider has no effect on this app (platform limitation, not a code bug) — only full display/DPI scaling works, and that path is verified correct. |

---

## 1. Keyboard-only operability

Traced every control named in the brief by reading the widget-construction code directly (no `keyPressEvent`
overrides, no custom event filters, no `setFocusPolicy` calls anywhere in the repo — confirmed by grep, meaning
every widget keeps Qt's default focus behavior).

| Control | Keyboard path | Verdict |
|---|---|---|
| Category switching | `QListWidget#categoryList` (`main_window.py:173-178`), default `StrongFocus`; Up/Down arrow changes `currentRow` → fires `currentRowChanged` → `_on_category_changed` (`main_window.py:329-331`) | Works, no mouse needed |
| Select an action | `QCheckBox` per row (`main_window.py:272`), default `StrongFocus`; Space toggles | Works |
| Select all / SAFE only / clear (global + per-category) | Plain `QPushButton`s via `_make_selection_button` (`main_window.py:365-371`); Tab + Enter/Space | Works |
| Presets (Quick Clean / Full Diagnostic / Privacy Debloat) | Same `QPushButton` pattern (`main_window.py:208-216`) | Works |
| Search box | `QLineEdit` (`main_window.py:218-223`), standard text-field keyboard behavior | Works |
| Run / Cancel | `QPushButton`s (`main_window.py:295-304`) | Works |
| Risky/destructive confirmation | Stock `QMessageBox.question`/`.warning` (`main_window.py:632-663`) — Qt's built-in Tab-between-buttons, Enter = default button, Esc = No | Works |
| Language toggle | `QPushButton` (`main_window.py:138-140`) | Reachable, but see Finding 5 for what happens *after* |
| Batch summary dialog | `QDialog.show()` (non-modal, `main_window.py:435`); "Open report" is a normal `QPushButton` | Reachable; see Finding 7 for a caveat |

**No keyboard trap exists anywhere in the app.** This is the one unambiguously good result of this audit: every
action named in the brief is fully operable without a mouse today, entirely on Qt's default focus/activation
behavior — nothing here needed the app to write its own keyboard handling. (The *visibility* of the keyboard focus
ring is a separate, already-covered finding — design-audit §4/§5 and design-additions item 22 — not repeated here.)

## 2. Screen reader compatibility

### Finding 1 (HIGH) — Descriptive text is tooltip-only

```
checkbox = QCheckBox(action.label(self.settings.language))
checkbox.setToolTip(action.description(self.settings.language))   # main_window.py:272-273
```

`action.description()` (`models.py:34-35`) pulls `description_sk`/`description_en` — real, useful context written
for every single one of the 108 actions (e.g. `"Removes Prefetch files (temporarily slows next boot)"`,
`Modules/m02_cleanup/actions.yaml:38`). `setToolTip` is the **only** call to any tooltip/description API in the
entire codebase (grep confirms exactly one hit). `QToolTip` requires hovering the mouse and pausing — it is not
triggered by keyboard focus and is not read by Narrator/NVDA as part of a control's accessible description unless
the assistive tech separately intercepts the tooltip popup (inconsistent across screen readers, not guaranteed).
Meanwhile `setAccessibleDescription` — the API that *does* reliably reach Narrator/NVDA on focus — is never called
anywhere (zero grep hits for `setAccessible`/`QAccessible` in the whole repo).

Net effect: a technician who cannot use a mouse (motor impairment, or navigating with a screen reader) sees/hears
only the short action label (e.g. "Prefetch cache") and never the risk-relevant caveat in the description ("...
temporarily slows next boot") that a sighted mouse user gets for free by hovering.

**Fix:** one extra line next to the existing `setToolTip` call —
`checkbox.setAccessibleDescription(action.description(self.settings.language))`. Same string, already computed,
now reaches assistive tech too.

### Finding 2 (HIGH) — Risk and status conveyed only by sibling widgets, not the control's own accessible name

```python
checkbox = QCheckBox(action.label(self.settings.language))     # main_window.py:272
...
badge = QLabel(action.risk.value)                                # main_window.py:277 — separate widget
...
status_label = QLabel("")                                        # main_window.py:281 — separate widget
```

Qt's accessibility bridge derives a `QCheckBox`'s accessible Name from its own `text()` by default (standard
`QAccessibleButton` behavior — there is no override here, confirmed by the grep sweep). The risk badge and the
running/OK/FAILED status text are **separate `QLabel` siblings** in the same row, not the checkbox's buddy and not
folded into its accessible name/description via any API call. `QLabel` does not accept keyboard focus (no
`setFocusPolicy` call anywhere gives it one), so a screen reader driven by Tab — the standard interaction model for
reaching *interactive* controls — never lands on these labels at all. The practical result: tabbing through the
action list, a screen-reader user hears only e.g. "Prefetch cache, checkbox, not checked" — never "SAFE", never
"MODERATE"/"DESTRUCTIVE"/"REQUIRES_REBOOT", and during a run, never "RUNNING" / "OK (3.2s)" / "FAILED". This is a
meaningfully different risk-communication failure than the color-only concern the design audit already ruled out —
the text *is* there and *is* non-color-coded, but it's structurally invisible to keyboard-driven screen-reader
navigation because it lives on a widget that focus never reaches.

**Fix:** fold both into the checkbox itself, e.g.
`checkbox.setAccessibleName(f"{action.label(lang)} — risk: {action.risk.value}")` at row-build time, and re-set
`checkbox.setAccessibleDescription(...)` (or a small `setAccessibleName` update) inside `_set_action_status`
(`main_window.py:558-565`) whenever the state changes, so refocusing the checkbox after a run announces the outcome.

### Finding 4 (MEDIUM) — Status bar changes aren't proactively announced

`self.statusBar().showMessage(...)` is called at `main_window.py:353`, `361-363`, and `607-611` — this is the
*entire* mechanism for "N selected / highest risk: X" and "Running 4/12: <label>" feedback (added since the design
audit, per that report's own top recommendation). `QStatusBar` text changes do not generate an accessibility
notification screen readers proactively speak (there's no ARIA-live equivalent wired up, and no code here does
anything toolkit-side to request one). A sighted user glances at the bottom of the window; a screen-reader user must
deliberately navigate to and re-read the status bar to learn the same information — it will never interrupt them
mid-task the way the visual indicator does for a sighted user. Not a blocker (the information is *reachable*), but
it means the one new "give feedback back to the user" mechanism this app just gained is one-way for non-visual
users.

### Finding 5 (MEDIUM) — Language toggle drops keyboard position

```python
def _on_toggle_language(self) -> None:
    self.settings.language = "en" if self.settings.language == "sk" else "sk"
    old_central = self.centralWidget()
    self._action_checkboxes = {}
    self._build_ui()                      # main_window.py:441-447
    if old_central is not None:
        old_central.deleteLater()
```

`_build_ui()` unconditionally re-creates every widget from scratch and always calls
`self.category_list.setCurrentRow(0)` (`main_window.py:316`) — the category tab always resets to the first one, and
nothing sets focus onto any specific widget afterward. A mouse user barely notices (click the language button again
if needed); a keyboard-only or screen-reader user loses both their current category and their tab-order position
every single time they switch SK/EN, and has to re-navigate from the top of the window to get back to where they
were. This is a distinct, keyboard-specific consequence of the same function the design audit's i18n section
(§6) covered from a *content-correctness* angle only (diacritics, missing translations) — not overlapping with that
finding.

**Fix:** remember the selected category index and the identity of the currently-focused action id (if any) before
`_build_ui()`, and restore both (`category_list.setCurrentRow(saved_index)`, `checkbox.setFocus()`) after.

### Finding 7 (LOW, low-confidence) — Non-modal batch-summary dialog focus

`dialog.show()` (`main_window.py:435`) is non-modal with no explicit `.activateWindow()` or `.setFocus()` call
anywhere (grep confirms zero hits for either across the repo) — it relies entirely on Qt/Windows' default top-level
window activation to bring itself forward and receive focus when a batch finishes unattended. This is very likely
fine in practice (Windows fires a foreground-activation accessibility event Narrator listens for regardless of
toolkit), but this could not be verified by reading code alone — flagging as a low-severity, low-confidence note
rather than a confirmed gap, per the instruction to stay evidence-based rather than speculate.

## 3. Color contrast / color-only signaling (widgets added since the design audit)

The design audit already verified risk badges are text+color (not color-only) and fixed the one measured contrast
failure (`#565f89`). Re-checked specifically for the newer widgets it didn't cover:

- **Action status label** (`style.py:182-188`, `QLabel#actionStatus[state=...]`) — RUNNING/OK/FAILED colors
  (`#e0af68`/`#9ece6a`/`#f7768e`) are paired with translated *words* (`status_running`/`status_ok`/`status_failed`
  in `i18n.py`), not color alone. **Not color-only** — clean.
- **Batch-summary result rows** (`main_window.py:410-416`, `style.py:218-219`) — `summaryRow[ok=...]` sets text
  color, but each row is also literally prefixed `f"[{status}] {label}"` (line 413) where `status` is the same
  translated OK/FAILED word. **Not color-only** — clean.
- **Search box focus ring** (`style.py:197-199`, `QLineEdit#searchBox:focus`) — only interactive control in the
  newer batch that *does* get an explicit `:focus` QSS rule (accent-blue border). Good, though it highlights by
  contrast that checkboxes/buttons still don't (already flagged, design-audit §4/§5 — not repeated here).
- **Preset / selection buttons** — plain `QPushButton#selectionBtn`, same styling family already contrast-checked
  in the design audit (`#a9b1d6` text, well above 4.5:1). No new gap.

No new color-only signal was introduced by the work done since the design audit.

## 4. Font sizing / High-DPI

- Every `font-size` declaration in `style.py` uses **`pt` units** (10pt base, 14pt title, 9pt pill, 11pt heading,
  8pt badge, 8.5pt selection button, 9pt scope label, 8pt status, 9pt console) — confirmed by re-reading the full
  file; **no hardcoded `px` font sizes exist anywhere**. Qt point sizes scale with both the OS logical-DPI setting
  and Qt's own high-DPI scale factor, unlike pixel sizes, which would not.
- `requirements.txt` pins `PySide6==6.7.2` (Qt 6.7). Qt 6 enables per-monitor-v2 DPI awareness and high-DPI
  scaling **by default** — no `Qt.AA_EnableHighDpiScaling` call is needed (and none exists; the attribute is a
  legacy Qt5 no-op in Qt6 anyway). No custom manifest is embedded by `scripts/build.ps1`'s PyInstaller invocation
  (no `--manifest`/`--uac-admin` flag), so the app relies on Qt6's own built-in DPI-awareness registration rather
  than an external one — consistent with a modern Qt6 baseline and not itself a gap.
- **Finding 6 (LOW-MEDIUM, platform limitation, not a code bug):** Windows 11's standalone "Make text bigger"
  accessibility slider (Settings → Accessibility → Text size) is a separate mechanism from full display/DPI
  scaling — it only affects apps built against the WinRT/WinUI text-scaling API. PortableFix is a classic Win32/Qt6
  desktop app and does not (and structurally cannot, without a WinUI rewrite) opt into that API, so a low-vision
  technician relying specifically on that slider gets no effect on this app. The lever that *does* work — full
  display scaling (125%/150%/etc., System → Display → Scale) — is correctly respected per the two points above.
  This is shared by essentially every non-WinUI Windows desktop app (Qt, WinForms, most Electron apps included) and
  isn't a PortableFix-specific defect; noting it because the brief specifically asked about "font sizing/high-DPI"
  and a technician troubleshooting *someone else's* pre-configured accessibility settings would plausibly hit this
  and wonder why the in-app text didn't grow.
- No in-app font-scale control exists (already flagged as Minor in the design audit §5 — not repeated as a new
  finding here, only cross-referenced since it's directly relevant to the platform-limitation point above: an
  in-app `Ctrl+=/-` font scaler would be the one mitigation available for the WinRT text-scaling gap, since the app
  can't hook the OS API itself).

## 5. Windows built-in accessibility tools

- **Magnifier:** no findings — Magnifier operates at the OS compositor level and has no special interaction with
  how an app renders; nothing in the code (fixed 1000×700 initial size, no `setFixedSize`) would interfere with it.
- **Narrator / NVDA:** covered above (Findings 1, 2, 4, 5, 7) — basic navigation and control announcement work
  (every control has *a* name, from Qt's default text-based accessible name), but several pieces of state
  (risk tier, run status, description text, live selection/progress feedback) don't reach the accessible tree the
  way a screen-reader user would need them to.
- **High Contrast mode (Finding 3, MEDIUM-HIGH):** `main.py:19` (`app.setStyleSheet(style.STYLE)`, applied before
  the window even exists) and `main_window.py:111` (`self.setStyleSheet(style.STYLE)`) both hardcode every color
  role via QSS. Grepping the whole repository for any Windows-high-contrast-aware code
  (`HighContrast`, `styleHints`, `colorScheme`, `QPalette`) returns **zero matches** — there is no code path that
  detects the OS is in High Contrast mode, and none that would fall back to the native/system palette if it is.
  Qt's documented behavior is that an application-level QSS stylesheet's explicit color declarations take priority
  over the `QPalette` the platform theme (including a High Contrast theme) would otherwise supply — so a technician
  or customer machine running Windows High Contrast gets PortableFix's fixed Tokyo Night palette regardless. This
  matters specifically for this app's use case: a technician plugging a USB tool into *someone else's* machine has
  no control over — and often no advance knowledge of — that machine's existing accessibility configuration, and
  the tool currently can't adapt to it.
  **Fix (nontrivial, flagging rather than prescribing a one-liner):** check
  `QApplication.styleHints()` / listen for the OS theme-change notification at startup and skip
  `setStyleSheet(style.STYLE)` (falling back to the native Fusion/Windows style) when Windows High Contrast is
  active. This is real, cross-cutting work — sizing it as a "larger bet," not a quick win.
  **Partial mitigation already present, incidentally:** the four `QMessageBox` confirmation dialogs are the one
  part of the UI with no QSS override (`style.py` has no `QMessageBox` selector — same gap the design audit flagged
  as Critical #1 for *visual* reasons) — which means, as an accidental side effect, these safety-critical Yes/No
  prompts are exactly the ones that *do* still pick up a Windows High Contrast theme correctly. Worth knowing before
  fixing design-audit #1: styling `QMessageBox` for visual consistency (as recommended there) would need to be done
  in a way that doesn't also defeat this High Contrast pass-through (e.g. skip applying the custom stylesheet to
  message boxes specifically when High Contrast is detected).

---

## Verified clean

- **Full keyboard operability**, confirmed by tracing every named control (§1 above) — no keyboard traps, no
  missing keyboard path for any action in the brief's list (select actions, run batch, confirm dialogs, cancel,
  search, presets, category switching, language toggle all work without a mouse).
- **No icon-only or glyph-only controls anywhere.** Every button and status indicator uses plain, translated text
  (confirmed by reading the entire widget-construction code in `_build_ui`) — nothing depends on iconography a
  screen reader would have to guess at.
- **Risk badges and status labels are text+color, not color-only**, for both the widgets the design audit already
  checked and the ones added since (§3 above).
- **No hardcoded pixel font sizes** — every `style.py` font-size rule uses `pt`, which scales with Windows display
  scaling (§4).
- **PySide6 6.7.2 / Qt 6** has per-monitor DPI awareness on by default; no manifest workaround needed or missing.
- The safety-critical confirmation dialogs (destructive/risky-action prompts, restore-point-failure prompt) use
  Qt's stock `QMessageBox`, which — as a side effect of the (separately flagged) missing QSS styling — correctly
  follows the OS's Windows High Contrast palette when active, unlike the rest of the app.
