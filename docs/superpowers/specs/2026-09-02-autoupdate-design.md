# PortableFix — Design: Auto-update cez GitHub Releases

Rozsah: nová funkcia, nie súčasť pôvodného `PortableFix_SPEC.md` (ten
bol stratený po kompresii kontextu skôr v session; táto funkcia je
navrhnutá z čerstvého rozhovoru s užívateľom, nie z master spec).
Repozitár je odteraz publikovaný na `https://github.com/vxkShelby/portableFixer`
(pridané ako `origin`, `main` pushnutý).

## Rozhodnutia z brainstormingu

- **Portable USB distribúcia zostáva.** Žiadny `.exe` inštalátor (MSI/
  Inno/WiX/NSIS). Appka sa naďalej spúšťa priamo z priečinka (USB alebo
  lokálne), bez inštalácie, bez admin oprávnení na spustenie.
- **Auto-update sa pridáva ako nová schopnosť portable appky**, nie ako
  druhý distribučný kanál. Appka sa sama pri štarte spýta GitHubu, či
  existuje novšia verzia, a vie sa sama vymeniť za novú.
- **Kontrola:** automaticky pri každom štarte, na pozadí, úplne ticho pri
  zlyhaní (offline, timeout, zlá odpoveď) — nesmie nič vypísať ani
  spomaliť normálne použitie appky na diagnostikovanom (často offline)
  počítači.
- **Aplikovanie update-u:** stiahnuť nový `.exe` na pozadí → zobraziť
  banner → po kliknutí užívateľa a potvrdení sa appka reštartuje cez
  pomocný PowerShell skript, ktorý počká na ukončenie bežiaceho procesu,
  vymení súbor, znovu spustí appku.
- **Balenie sa mení z `--onedir` na `--onefile`.** Dôvod zistený až pri
  navrhovaní (pozri sekciu "Prečo onefile" nižšie) — s `--onedir` by
  výmena samotného `PortableFix.exe` NEDOSTATOČNE aktualizovala appku,
  pretože skutočný Python bytecode appky žije v `_internal/PYZ-00.pyz`,
  nie v tenkom bootloader `.exe` súbore. `--onefile` zabalí úplne
  všetko (vrátane `_internal` obsahu) do jedného súboru, takže výmena
  jedného `.exe` je naozaj kompletná a korektná výmena celej appky.

## Prečo je to bezpečné pre existujúci Modules/Data mechanizmus

`portablefix/paths.py:get_base_dir()` pri zmrazenom (PyInstaller) behu
počíta `Path(sys.executable).resolve().parent.parent` — t.j. očakáva,
že `.exe` sedí v `<root>/App/PortableFix.exe`, a `Modules/`, `Data/`,
`Logs/`, `Reports/`, `Backups/` sú **externé súrodenecké priečinky** o
dva levely vyššie, nie súčasť PyInstaller balíka. Toto platí rovnako
pre `--onedir` aj `--onefile` (`sys.executable` v oboch prípadoch
ukazuje na skutočné miesto `.exe` súboru na disku, nie na dočasný
extrakčný priečinok `--onefile` režimu — ten je dostupný len cez
`sys._MEIPASS`, ktorý appka dnes nikde nepoužíva).

Dôsledok: **auto-update sa nikdy nedotýka `Modules/`, `Data/`, `Logs/`,
`Reports/`, `Backups/`** — tie zostávajú presne také, aké boli (vrátane
užívateľových vlastných úprav YAML katalógov, ak nejaké spravil).
Aktualizuje sa výhradne `App/PortableFix.exe`.

## `Data/SHA256SUMS` — nutná koordinácia s existujúcim integrity checkom

`portablefix/integrity.py:check_integrity()` (volané z `main.py` pri
každom štarte) porovnáva `Data/SHA256SUMS` proti skutočným súborom pod
`App/` a `Modules/` a pri nezhode zobrazí varovanie
(`integrity_warning`, "subory boli zmenene"). Po výmene `.exe`
auto-updaterom by starý záznam pre `App/PortableFix.exe` v
`Data/SHA256SUMS` **nesedel** s novým súborom → falošné poplach ihneď
po legitímnom update.

Riešenie: pomocný PowerShell skript (popísaný nižšie), ktorý appku
reštartuje, po výmene `.exe` **prepočíta jeho SHA256** (`Get-FileHash`)
a **aktualizuje príslušný riadok v `Data/SHA256SUMS`** (nahradí riadok
končiaci na `App/PortableFix.exe`, alebo ho pridá, ak chýba) — čisto v
PowerShell, bez závislosti na Pythone (na cieľovom stroji nemusí byť
Python nainštalovaný, appka beží ako samostatný `.exe`). Cesty v
`SHA256SUMS` sú vždy s dopredným lomítkom (`scripts/generate_sha256sums.py`
zapisuje `relative_to(base_dir).as_posix()`), takže skript hľadá presne
`App/PortableFix.exe`, žiadna alternácia pre spätné lomítko netreba.

## Verzia — nový zdroj pravdy

Nový súbor `portablefix/version.py`:
```python
APP_VERSION = "1.0.0"
```
Toto je štartovacia hodnota reprezentujúca aktuálny stav appky (v repe
doteraz nebola žiadna verzia sledovaná — žiadny `VERSION` súbor, žiadny
git tag). Zvyšovanie čísla je súčasť manuálneho release procesu (pozri
nižšie), nie automatizované touto zmenou.

## Nový modul `portablefix/updater.py`

Rovnaký architektonický vzor ako `restore_point.py` (samostatný modul s
čistou logikou + `QThread` wrapper pre neblokujúce spustenie z GUI).

### Dátová trieda

```python
@dataclass
class UpdateInfo:
    version: str            # napr. "1.1.0" (bez "v" prefixu)
    download_url: str       # browser_download_url pre PortableFix.exe asset
    sha256_url: str | None  # browser_download_url pre PortableFix.exe.sha256 asset, ak existuje
    notes: str               # release body text (zobrazené v banneri/detaile)
```

### Porovnanie verzií (nie naivný string compare)

```python
def parse_version(v: str) -> tuple[int, ...]:
    v = v.lstrip("vV")
    parts = []
    for p in v.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)

def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)
```
`"1.10.0" > "1.9.0"` musí platiť numericky (10 > 9), nie ako string
porovnanie (`"1.10.0" < "1.9.0"` ako reťazce) — test na toto explicitne
existuje (pozri sekciu Testovanie).

### Kontrola dostupnosti (beží na pozadí, GET na GitHub API)

```python
GITHUB_API_LATEST_RELEASE = "https://api.github.com/repos/vxkShelby/portableFixer/releases/latest"

def check_for_update(current_version: str, timeout: float = 5.0) -> UpdateInfo | None:
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST_RELEASE,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "PortableFix-Updater"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "")
        if not tag or not is_newer(tag, current_version):
            return None
        assets = data.get("assets", [])
        exe_asset = next((a for a in assets if a.get("name", "").lower() == "portablefix.exe"), None)
        if not exe_asset:
            return None
        sha_asset = next((a for a in assets if a.get("name", "").lower() == "portablefix.exe.sha256"), None)
        return UpdateInfo(
            version=tag.lstrip("vV"),
            download_url=exe_asset["browser_download_url"],
            sha256_url=sha_asset["browser_download_url"] if sha_asset else None,
            notes=data.get("body", ""),
        )
    except Exception:
        return None
```
Široký `except Exception` je zámerný — akákoľvek chyba (offline,
timeout, GitHub rate-limit, zlý JSON, chýbajúce polia) sa má stíšiť na
`None`, presne podľa rozhodnutia "ticho zlyhať". Žiadny nový pip
dependency — `urllib.request`/`json` sú stdlib, konzistentné s
existujúcim minimalistickým prístupom appky (žiadne `requests` a pod.).
Používa sa štandardné, neautentifikované volanie GitHub REST API (60
req/hod na IP — dostatočné pre kontrolu raz za spustenie appky).

### Stiahnutie + overenie

```python
def download_update(info: UpdateInfo, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    exe_path = dest_dir / "PortableFix.new.exe"
    urllib.request.urlretrieve(info.download_url, exe_path)
    if info.sha256_url:
        with urllib.request.urlopen(info.sha256_url, timeout=10) as resp:
            expected = resp.read().decode("utf-8").strip().split()[0].lower()
        actual = compute_sha256(exe_path)  # reuse z portablefix.integrity
        if actual.lower() != expected:
            exe_path.unlink(missing_ok=True)
            raise UpdateVerificationError("Downloaded file does not match expected SHA256.")
    return exe_path
```
`UpdateVerificationError` — nová jednoduchá výnimka v `updater.py`.
Znovupoužije `portablefix.integrity.compute_sha256` (existuje už dnes,
netreba duplikovať). Ak release nemá `.sha256` asset (napr. staršia
verzia bez neho), stiahnutie prebehne bez overenia — nie je to
blokujúca chyba, len znížená istota.

### Aplikovanie — generovaný PowerShell swap skript

```python
def build_swap_script(current_pid: int, old_exe: Path, new_exe: Path, sums_path: Path) -> str: ...

def apply_update(new_exe_path: Path, current_exe_path: Path, sums_path: Path) -> None:
    script_text = build_swap_script(os.getpid(), current_exe_path, new_exe_path, sums_path)
    script_path = Path(tempfile.gettempdir()) / f"portablefix_update_{os.getpid()}.ps1"
    script_path.write_text(script_text, encoding="utf-8")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script_path)],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
```

Vygenerovaný skript (presné znenie, cesty vložené ako parametre —
citlivé na medzery v ceste, keďže `USB Fixer` priečinok má medzeru v
názve, treba dôsledne úvodzovkovať):

```powershell
$ErrorActionPreference = "SilentlyContinue"
for ($i = 0; $i -lt 30; $i++) {
    if (-not (Get-Process -Id <PID> -EA SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
Start-Sleep -Milliseconds 300
Move-Item -Path "<old_exe>" -Destination "<old_exe>.old" -Force
Move-Item -Path "<new_exe>" -Destination "<old_exe>" -Force
Remove-Item -Path "<old_exe>.old" -Force -EA SilentlyContinue
try {
    $hash = (Get-FileHash -Path "<old_exe>" -Algorithm SHA256).Hash.ToLower()
    if (Test-Path "<sums_path>") {
        $lines = Get-Content "<sums_path>"
        $newLines = @()
        $found = $false
        foreach ($line in $lines) {
            if ($line -match 'App/PortableFix\.exe$') {
                $newLines += "$hash  App/PortableFix.exe"
                $found = $true
            } else {
                $newLines += $line
            }
        }
        if (-not $found) { $newLines += "$hash  App/PortableFix.exe" }
        Set-Content -Path "<sums_path>" -Value $newLines -Encoding ASCII
    }
} catch {}
Start-Process -FilePath "<old_exe>"
```

Poradie je dôležité: skript čaká, kým `Get-Process -Id <PID>` prestane
nájsť bežiaci proces (appka sa medzitým sama korektne ukončí cez
`QApplication.instance().quit()`), až potom vymieňa súbory — žiadna
krehká časová závislosť na Python strane, čakací cyklus je v samotnom
skripte. `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` zabezpečí, že
PowerShell proces prežije zánik appky, ktorá ho spustila.

`Move-Item` (nie `Remove-Item` + `Copy-Item`) na výmenu, aby okno, kedy
`App/PortableFix.exe` neexistuje vôbec, bolo minimálne.

## Kontrola zapisovateľnosti pred pokusom o update

`resolve_writable_base_dir()` už dnes deteguje needytovateľný base_dir
(napr. skutočne uzamknutý USB) a appka beží ďalej cez `%TEMP%` fallback
pre stavové dáta (Logs/Reports/Backups/settings). Auto-update navyše
potrebuje zápis do `App/` samotného (kam `state_dir` fallback nezasahuje
— `App/` je vždy tam, kde appka fyzicky je). `download_update`/
`apply_update` sa nemajú ani pokúšať bežať, ak `App/` priečinok nie je
zapisovateľný — banner v tomto prípade ukáže chybu namiesto tichého
zlyhania v polovici výmeny (na rozdiel od kontroly dostupnosti, ktorá
ostáva plne tichá).

## GUI integrácia (`portablefix/gui/main_window.py`)

- `UpdateCheckRunner(QThread)` v `updater.py` — signál
  `update_available = Signal(object)` (`UpdateInfo | None`). Spustí sa
  krátko po zobrazení hlavného okna (neblokuje `_build_ui()`).
- `_on_update_available(info)`: `None` → nič. `UpdateInfo` → zobrazí
  malý dismissovateľný banner (nový riadok pod `top_bar`, nie modálne
  okno — appka nesmie prerušiť prácu technika vyskakovacím dialógom pri
  štarte) s textom `"Nová verzia {version} je dostupná"` + tlačidlá
  `Aktualizovať` / `Zavrieť`.
- Klik na `Aktualizovať` → `QMessageBox.question` potvrdenie → spustí
  download (vlastný krátkotrvajúci `QThread`, banner medzitým ukáže
  "Sťahujem...") → pri úspechu druhé potvrdenie ("Appka sa teraz
  reštartuje a aktualizuje na verziu {version}. Pokračovať?") →
  `updater.apply_update(...)` → `QApplication.instance().quit()`.
- Zlyhanie sťahovania / SHA256 nesúhlasí / `App/` nezapisovateľný →
  banner prejde do chybového stavu s textom chyby + možnosťou skúsiť
  znova, appka pokračuje bežať bez prerušenia.
- Nové i18n kľúče (SK+EN, cez existujúci `i18n.py` mechanizmus a jeho
  parity test): `update_available_banner`, `update_button`,
  `update_dismiss`, `update_confirm_download`, `update_confirm_restart`,
  `update_download_failed`, `update_verify_failed`,
  `update_not_writable`.

## `scripts/build.ps1` — zmena balenia

- `--onedir` → `--onefile` (jediná zmena PyInstaller flagu).
- Odstráni sa celý blok "flatten nested PortableFix folder" — s
  `--onefile` a `--distpath $distStage --name PortableFix` PyInstaller
  zapíše `.exe` priamo do `$distStage\PortableFix.exe`, žiadny vnorený
  podpriečinok nevzniká (ten problém bol špecifický pre `--onedir`
  COLLECT krok).
- `--noconsole`, `--icon`, `--add-data` (Modules/Data/ico) zostávajú
  bezo zmeny — `--add-data` naďalej bunduje kópiu dovnútra `.exe` ako
  doteraz (redundantné, appka ju nečíta, `get_base_dir()` vždy siaha po
  externých priečinkoch — toto je existujúce, nemenené správanie, len
  teraz explicitne zdokumentované).
- Podpisovanie (`signtool`) zostáva samostatný manuálny krok po builde,
  bezo zmeny.

## Release proces (manuálny runbook, nie automatizovaný touto zmenou)

1. Zvýšiť `APP_VERSION` v `portablefix/version.py`.
2. `scripts/build.ps1` (teraz `--onefile`) → `App/PortableFix.exe`.
3. Podpísať (`signtool sign ...`, existujúci krok).
4. `scripts/generate_sha256sums.py .` (existujúci krok, aktualizuje
   `Data/SHA256SUMS`).
5. Vypočítať samostatný hash pre update-mechanizmus:
   `(Get-FileHash App\PortableFix.exe -Algorithm SHA256).Hash` → uložiť
   ako jednoriadkový text do `App/PortableFix.exe.sha256` (lokálny
   working súbor, nie súčasť git repa — `App/` je už dnes celý v
   `.gitignore`, netreba nový záznam).
6. Vytvoriť GitHub Release s tagom `v<verzia>` (napr. `v1.1.0`),
   nahrať `App/PortableFix.exe` a `App/PortableFix.exe.sha256` ako
   assety.

Automatizácia tohto runbooku (napr. `gh release create` skript) je
mimo rozsahu tejto zmeny — možné budúce vylepšenie, nie súčasť tohto
plánu.

## Testovanie

Nová `tests/test_updater.py`, rovnaká disciplína ako zvyšok projektu
(unit testy s mockovaným `urllib.request`, žiadne skutočné sieťové
volania v test suite):

- `test_parse_version_handles_v_prefix_and_multi_digit_components` —
  `"v1.10.2"` → `(1, 10, 2)`, a explicitne `is_newer("1.10.0",
  "1.9.0")` je `True` (numerické, nie string porovnanie).
- `test_check_for_update_returns_none_when_remote_not_newer_or_equal`
- `test_check_for_update_returns_info_when_remote_newer` (mock
  `urlopen` vracia kanonický GitHub release JSON s `PortableFix.exe` a
  `PortableFix.exe.sha256` assetmi)
- `test_check_for_update_returns_none_on_network_error` (mock vyhodí
  `URLError`/`TimeoutError`)
- `test_check_for_update_returns_none_on_malformed_json`
- `test_check_for_update_returns_none_when_no_exe_asset_present`
- `test_download_update_succeeds_when_hash_matches`
- `test_download_update_raises_and_cleans_up_on_hash_mismatch`
- `test_download_update_skips_verification_when_no_sha256_asset`
- `test_build_swap_script_parses_as_valid_powershell` — rovnaký
  `[scriptblock]::Create($env:PFCMD)` vzor použitý celú túto session na
  parse-validáciu YAML katalógových príkazov, teraz na vygenerovaný
  swap skript (žiadne skutočné spustenie/výmena súborov v teste).
- `test_build_swap_script_quotes_paths_with_spaces` — konkrétne overí
  cestu obsahujúcu medzeru (`C:\Users\...\USB Fixer\App\...`) je v
  skripte korektne úvodzovkovaná.

GUI testy (`tests/test_gui_main_window.py`), mockujúci
`updater.check_for_update`/`UpdateCheckRunner` výsledok priamo (žiadne
skutočné HTTP v GUI testoch):
- banner sa zobrazí keď je `UpdateInfo` dostupný, so správnym textom
  verzie.
- banner sa nezobrazí keď runner vráti `None`.
- klik na `Zavrieť` banner skryje bez ďalších side-effectov.

## Mimo rozsahu tejto zmeny (výslovne odložené)

- Automatizácia GitHub Release vytvárania (krok 6 runbooku zostáva
  manuálny).
- Rollback UI ak nová verzia po update-e nefunguje (`<exe>.old` sa v
  swap skripte krátko vytvorí, ale hneď zmaže — žiadna trvalá záloha
  starej verzie, žiadne "vráť predchádzajúcu verziu" tlačidlo).
- Update kanály (beta/stable) — vždy len `releases/latest`.
- Delta/inkrementálne update-y — vždy celý `.exe` znova.
- Akákoľvek zmena `--onedir` build cesty ako voliteľnej alternatívy —
  `--onefile` sa stáva jediným podporovaným spôsobom balenia.
- Podpisovanie novým (nie self-signed) certifikátom — mimo rozsahu,
  auto-update funguje rovnako s aktuálnym self-signed prístupom (SHA256
  overenie je nezávislé od code-signing dôveryhodnosti).
