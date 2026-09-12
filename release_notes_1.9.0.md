## New

- **New app icon and topbar logo**: redrawn as a single-tone cyan shield+wrench on dark navy to match the redesigned UI (the old icon was an unrelated blue/green combo).

## Fixed

- **Winget update "hang"**: a single winget upgrade call can legitimately take minutes with only a busy progress bar for feedback - added a ticking elapsed-seconds counter so it's visibly still alive.
- On timeout, `winget upgrade` was killed but any installer/MSI child process it spawned kept running and could hold a file lock, causing repeated slowness on retry - now the whole process tree is killed via `taskkill /F /T`.
- The winget panel's auto-check timer could fire mid-update and reset the in-progress row list out from under it - now skipped while an update is running.
- **The v1.8.0 pill-rounding redesign didn't actually render rounded** on a real run - the app never set Qt's Fusion style, so the native Windows style silently ignored/misrendered the QSS `border-radius`/gradients. Forced Fusion so the stylesheet renders as designed.

Full history since v1.8.0: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.8.0...v1.9.0).
