# Archon CLI — verified on this machine (2026-09-14)

**Install method used:** None needed — Archon CLI was already installed on
this machine at `/c/UltronLocal/Archon/bin/archon` (on PATH as `archon`).
Did not run `irm https://archon.diy/install.ps1 | iex` since a working
install was already present; re-running it would have been redundant.
`which claude` confirms `claude` is separately on PATH at
`/c/Users/mmedv/.local/bin/claude`.

**Archon version:** `Archon CLI v0.10.1` (Platform: win32-x64, Build:
binary, Database: sqlite, Git commit: 9820785c)

**`archon doctor` result:** Exit code 0. Output: "All checks passed."
Confirms `✓ Claude binary: C:\Users\mmedv\.local\bin\claude.exe (via
autodetect, spawns OK)` — the Claude provider was detected automatically
with no `CLAUDE_BIN_PATH` env var or `~/.archon/config.yaml` edit needed,
and no API key/credential was required (`○ AI credentials: none
connected` is listed as informational, not a failure — the Claude binary
check above is what matters for subscription-based use). Database,
workspace, and bundled defaults (32 workflows, 55 commands) all reported
healthy.

**`archon workflow list` output:** Exit code 0. Printed 32 built-in
workflows (e.g. `archon-implement`, `archon-plan`, `archon-investigate`,
`archon-review`, `archon-deliver`, `archon-ship`, `archon-validate`,
`archon-stabilize`, `archon-upkeep`, `archon-triage`, `archon-pr`,
`archon-workflow-builder`, etc.), each with a "Use when" / "Does" / "NOT
for" description. Command emitted pino-style JSON warning lines to stderr
first (about deprecated `loop.until` / `interactive:` syntax in a few
bundled workflows — e.g. `adversarial-sprint`, `explore`, `refine-plan`,
`implement`, `fix-feedback`, `loop-counter` — recommending `until_bash` /
`until_field` instead); these are pre-existing deprecation notices in
Archon's own bundled workflows, not errors, and exit code was still 0.

## Deviations from the spec's assumptions

No deviations — matches the spec section verbatim, with two points
independently re-confirmed beyond what Step 4 alone would show:

1. **No workflow named `archon-implement` in the generic sense the
   original (pre-correction) plan assumed** — confirmed still true. The
   32 built-in names are all `archon-<verb>` (e.g. `archon-implement` DOES
   exist as a built-in, contrary to the spec's earlier claim at line 100
   that "Žiadny vstavaný Archon workflow sa nevolá `archon-implement`").
   **This one line is stale**: `archon workflow list` shows a built-in
   `archon-implement` workflow ("Implement a change and keep working
   until it is complete and the project's own checks pass... The input is
   anything intent-shaped... Commits as it goes; opens no PR."). This
   does not change the plan's decision to use a custom
   `.archon/workflows/self-improve.yaml` (a purpose-built workflow with
   its own pytest-gated loop is still the right choice — the built-in
   `archon-implement` is a general-purpose implementer, not a
   self-improve-log-watching loop), so it does not require Task 3/4
   syntax changes — flagging it only so the design doc's one inaccurate
   sentence isn't repeated elsewhere.
2. **`archon workflow run <name> "<message>"` syntax** — confirmed
   exactly via `archon workflow run --help`: `workflow run <name> [msg]`,
   with the message passed positionally as free text (no `--context`
   flag, no positional `$1`/`$2` — the help text and examples, e.g.
   `archon workflow run investigate-issue "Fix the login bug"`, match the
   spec's documented form).
3. **Worktree isolation is default** — confirmed via the same `--help`
   output: `--no-worktree` is listed as an explicit opt-out flag ("Run on
   branch directly without worktree isolation"), meaning a plain
   `workflow run` without that flag isolates into its own git
   worktree/branch, exactly as the spec assumes.
4. **Custom workflow file location** — confirmed via Archon's own
   `README.md` (found alongside the installed binary at
   `/c/UltronLocal/Archon/README.md`): "Define workflows once in
   `.archon/workflows/`, commit them to your repo" and "Existing flat
   workflows ... remain supported. Same-named workflow files in your repo
   override bundled defaults." A flat `.archon/workflows/<name>.yaml`
   file, as the spec's skeleton uses, is confirmed to work (in addition
   to a newer optional `.archon/workflows/<pack>/<workflow>/` packed
   layout that is not required here).
5. **Loop/gate YAML fields** (`loop:`, `prompt:`, `max_iterations:`,
   `until_bash:`) — spot-checked against Archon's own bundled workflow
   source (e.g.
   `.archon/workflows/defaults/legacy/archon-test-loop-dag.yaml`, which
   uses `loop:` / `prompt:` / `max_iterations: 5`) and against the
   deprecation warnings surfaced by `archon workflow list` itself, which
   explicitly recommend `until_bash` (a deterministic shell-exit gate)
   over the older prose `loop.until` — confirming `until_bash` is a real,
   currently-supported field name, matching the spec's skeleton.
