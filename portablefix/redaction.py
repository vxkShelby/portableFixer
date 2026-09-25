"""Masking personal data for a report handed to the client (research G20).

With the technician's "Redact for the client" setting on, the report and the
handoff package's own text files go out with user names in paths (and the
PC's profile names wherever else they appear), IP and MAC addresses, serial
numbers, product-key fragments and Wi-Fi network names replaced by
placeholders. Applied only when those files are rendered or
exported - the audit log on the stick always keeps the full record.

Pure and Qt-free on purpose: the same functions serve the report, the
handoff zip and the tests. Everything here matches PortableFix's own output
(PowerShell property names such as SerialNumber, which are never localized)
or the shape of a value (an address, a key) - never localized Windows text.
Best effort by design: a value printed without anything that identifies it
(a serial number in a table column) cannot be told apart from any other text.
"""

import ipaddress
import os
import re
from collections.abc import Iterable
from pathlib import Path

USER = "<user>"
IP = "<ip>"
MAC = "<mac>"
SERIAL = "<serial>"
KEY = "<key>"
SSID = "<ssid>"

# Profile folders every Windows has - naming them says nothing about the client.
_SHARED_PROFILES = {"public", "default", "default user", "all users", "defaultapppool"}
# Built-in or generic account names: masked inside a C:\Users\ path like any
# other, but never searched for across the whole document - "user" or
# "Administrator" there is ordinary text far more often than a person.
_GENERIC_ACCOUNTS = _SHARED_PROFILES | {
    "administrator", "admin", "user", "users", "guest", "owner", "defaultaccount",
    "wdagutilityaccount", "system", "local service", "network service",
}

# C:\Users\<name>\..., also with "/" or a JSON-escaped "\\" as separator. The
# name runs up to the next separator. One with nothing after it ("LocalPath
# : C:\Users\Jan Novak", a Format-Table row) may be a full name of up to three
# words when the value ends there - at the line end, a quote or a column gap
# of two spaces; otherwise it ends at whitespace, because the rest of the
# line may be ordinary text.
_USER_PATH = re.compile(
    r"(?P<prefix>(?<![A-Za-z])[A-Za-z]:(?P<sep>\\\\|\\|/)Users(?P=sep))"
    r"(?:(?P<name>[^\\/:*?\"<>|\r\n\t]+?)(?=(?P=sep))"
    r"|(?P<full>[^\\/:*?\"<>|\s'&;,)\]]+(?: [^\\/:*?\"<>|\s'&;,)\]]+){1,2})(?=[ \t]*(?:[\r\n\"']|$)|[ \t]{2})"
    r"|(?P<tail>[^\\/:*?\"<>|\s'&;,)\]]+))",
    re.IGNORECASE,
)

# Four dotted numbers that are not part of a longer dotted run (10.0.26100.1
# fails on 26100 anyway; 1.2.3.4.5 is a version, not an address).
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)")
# A four-part version that fits in octets ("Version=2.0.0.0",
# "FileVersion : 6.3.9600.1") is named as one right before it.
_VERSION_LABEL = re.compile(r"(?:version|\bver\.?|\bbuild)\"?[ \t]*[:=]?[ \t]*\"?$", re.IGNORECASE)
# A Format-Table header line followed by its dashes - a dotted quad in the
# column under a *Version header (DriverVersion in the m10 driver lists) is a
# version too.
_TABLE_RULE = re.compile(r"[ \t]*-+(?:[ \t]+-+)*[ \t]*")

_H = r"[0-9A-Fa-f]{1,4}"
# Either all eight groups, or a "::" form with a group on each side of it
# ("fe80::1c2b:3d4e", "2001:db8::1"). Times (12:34:56), MACs (six groups,
# no "::") and PowerShell's [Type]::Member never qualify.
_IPV6 = re.compile(
    rf"(?<![\w:.])(?:(?:{_H}:){{7}}{_H}|(?:{_H}:){{1,6}}(?::{_H}){{1,6}})(?:%\w+)?(?![\w:])"
)

# 00-1A-2B-3C-4D-5E / 00:1a:2b:3c:4d:5e, one separator throughout. A GUID
# (8-4-4-4-12) or a hash never has six two-digit groups.
_MAC = re.compile(r"(?<![\w:-])[0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\1[0-9A-Fa-f]{2}){4}(?![\w:-])")

# XXXXX-XXXXX-XXXXX(-...): three or more five-character groups, or two when
# they mix letters and digits (a masked key "XXXXX-3V66T", a product ID).
_KEY = re.compile(r"(?<![\w-])[A-Z0-9]{5}(?:-[A-Z0-9]{5})+(?![\w-])")

# "SerialNumber : ABC123" (Format-List), "SerialNumber=..." and the JSON
# "SerialNumber": "..." form - also BIOS/disk variants like
# SerialNumberID and "Serial Number". Only the value is masked.
# The value is greedy up to its last non-blank character, never a lazy scan
# with a whitespace lookahead - that is quadratic on a long run of spaces.
_SERIAL = re.compile(
    r"(?P<key>\b(?:[A-Za-z]*SerialNumber\w*|Serial Number|Serial)\"?[ \t]*[:=][ \t]*\"?)"
    r"(?P<value>[^\s\",;](?:[^\r\n\",;]*[^\s\",;])?)(?=[^\S\r\n]*(?:[\",;\r\n]|$))",
    re.IGNORECASE,
)

# The partial product key line of SoftwareLicensingProduct (five characters).
_PARTIAL_KEY = re.compile(
    r"(?P<key>\bPartialProductKey\"?[ \t]*[:=][ \t]*\"?)(?P<value>[A-Za-z0-9]{5})\b",
)

# "SSID : name" as netsh wlan prints it (the field names are not localized)
# and "SSID": "..." in JSON. BSSID is a MAC and handled by _MAC.
_SSID = re.compile(
    r"(?P<key>(?<![A-Za-z])SSID\"?[ \t]*[:=][ \t]*\"?)(?P<value>[^\s\"](?:[^\r\n\"]*[^\s\"])?)(?=[^\S\r\n]*(?:\"|\r|\n|$))",
)

# Values collected once (an SSID, a serial number, a profile name) are masked
# wherever else they appear too - e.g. the Wi-Fi profile line naming the
# same network, or "PC\Jan Novak" next to C:\Users\Jan Novak\. Too short a
# value would mask ordinary words; so would a short all-letter one ("Home" as
# an SSID would turn "Windows 11 Home" into "Windows 11 <ssid>").
_MIN_COLLECTED_LENGTH = 3
_MIN_COLLECTED_WORD_LENGTH = 5
# BIOS/board placeholders and Windows edition words: masked on their own
# labelled line, never searched for elsewhere.
_NOT_COLLECTED = {
    "home", "pro", "professional", "enterprise", "education", "core", "windows",
    "default string", "to be filled by o.e.m.", "system serial number", "chassis serial number",
    "not specified", "not applicable", "none", "n/a", "oem", "o.e.m.", "unknown", "invalid",
}


def _keep_ipv4(text: str) -> bool:
    # Loopback, "no address", masks and multicast say nothing about the
    # client's network; neither do the public resolvers PortableFix itself
    # can set (net_set_public_dns).
    try:
        address = ipaddress.IPv4Address(text)
    except ValueError:
        return True  # 999.1.1.1 - not an address at all.
    if address.is_loopback or address.is_unspecified or address.is_multicast or text.startswith("255."):
        return True
    return text in {"8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1", "9.9.9.9", "149.112.112.112"}


def _after_version_label(match: re.Match) -> bool:
    before = match.string[max(0, match.start() - 24):match.start()]
    return _VERSION_LABEL.search(before) is not None


def _version_columns(text: str) -> list[tuple[int, int, set[int]]]:
    """(start, end, columns) of each Format-Table body whose header names a
    *Version column: the column offsets where those headers start. Computed
    once per text; PowerShell aligns a string column to its header."""
    if "vers" not in text.lower():
        return []
    tables: list[tuple[int, int, set[int]]] = []
    lines = text.splitlines(keepends=True)
    offset = 0
    previous = ""
    current: tuple[int, set[int]] | None = None
    for line in lines:
        bare = line.rstrip("\r\n")
        if current is not None and not bare.strip():
            tables.append((current[0], offset, current[1]))
            current = None
        if current is None and previous.strip() and _TABLE_RULE.fullmatch(bare):
            columns = {m.start() for m in re.finditer(r"\S+", previous) if "vers" in m.group(0).lower()}
            if columns:
                current = (offset + len(line), columns)
        previous = bare
        offset += len(line)
    if current is not None:
        tables.append((current[0], offset, current[1]))
    return tables


def _in_version_column(match: re.Match, tables: list[tuple[int, int, set[int]]]) -> bool:
    for start, end, columns in tables:
        if start <= match.start() < end:
            line_start = match.string.rfind("\n", 0, match.start()) + 1
            return match.start() - line_start in columns
    return False


def _keep_ipv6(text: str) -> bool:
    try:
        address = ipaddress.IPv6Address(text.split("%", 1)[0])
    except ValueError:
        return True
    return address.is_loopback or address.is_unspecified or address.is_multicast


def _kept_spans(text: str, keep: tuple[str, ...]) -> list[tuple[int, int]]:
    spans = []
    lowered = text.lower()
    for term in keep:
        start = lowered.find(term)
        while start != -1:
            spans.append((start, start + len(term)))
            start = lowered.find(term, start + 1)
    return spans


def _inside(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= start and end <= e for s, e in spans)


def _sub(pattern: re.Pattern, text: str, keep: tuple[str, ...], replace) -> str:
    # The computer name and the technician/client fields were entered on
    # purpose - a match lying wholly inside one of them stays (a hostname
    # like ABCD1-EFGH2 looks like a key fragment).
    spans = _kept_spans(text, keep)

    def one(match: re.Match) -> str:
        if spans and _inside(match.start(), match.end(), spans):
            return match.group(0)
        return replace(match)

    return pattern.sub(one, text)


def _normalize_keep(keep: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({k.strip().lower() for k in keep if isinstance(k, str) and k.strip()}, key=len, reverse=True))


def redact_text(text: str, keep: Iterable[str] = (), mask: Iterable[str] = ()) -> str:
    """`text` with personal data replaced by placeholders (<user>, <ip>,
    <mac>, <serial>, <key>, <ssid>). `keep`: values never masked (the
    computer name, the technician and client). `mask`: user profile names
    masked wherever they appear (see local_profile_names). Idempotent."""
    if not isinstance(text, str) or not text:
        return text
    collected = _Collected()
    collected.add_names(mask)
    collected.add_from(text)
    return _redact(text, _normalize_keep(keep), collected.masker())


def _redact(text: str, kept: tuple[str, ...], masker: "_Masker") -> str:
    if not isinstance(text, str) or not text:
        return text

    def user(match: re.Match) -> str:
        name = next(match.group(g) for g in ("name", "full", "tail") if match.group(g) is not None)
        if name.strip().lower() in _SHARED_PROFILES:
            return match.group(0)
        return match.group("prefix") + USER

    def ipv4(match: re.Match) -> str:
        value = match.group(0)
        if _keep_ipv4(value) or _after_version_label(match) or (tables and _in_version_column(match, tables)):
            return value
        return IP

    text = _sub(_USER_PATH, text, (), user)
    text = _sub(_SSID, text, kept, lambda m: m.group("key") + SSID)
    text = _sub(_SERIAL, text, kept, lambda m: m.group("key") + SERIAL)
    text = _sub(_PARTIAL_KEY, text, kept, lambda m: m.group("key") + KEY)
    text = _sub(_MAC, text, kept, lambda m: MAC)
    text = _sub(_IPV6, text, kept, lambda m: m.group(0) if _keep_ipv6(m.group(0)) else IP)
    # Column offsets are taken from the text as it is now - the masks above
    # may have shortened lines.
    tables = _version_columns(text)
    text = _sub(_IPV4, text, kept, ipv4)
    text = _sub(_KEY, text, kept, lambda m: KEY if _looks_like_key(m.group(0)) else m.group(0))
    return masker.apply(text, kept)


def _looks_like_key(value: str) -> bool:
    groups = value.split("-")
    if len(groups) >= 3:
        return True
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def _worth_collecting(value: str) -> bool:
    if len(value) < _MIN_COLLECTED_LENGTH or value.lower() in _NOT_COLLECTED:
        return False
    if value in (USER, SSID, SERIAL):
        return False
    # "Home", "Dell": an all-letter word this short is too likely to be
    # ordinary text somewhere else in the report.
    return not (value.isalpha() and len(value) < _MIN_COLLECTED_WORD_LENGTH)


def _worth_collecting_name(name: str) -> bool:
    # Profile names are masked case-insensitively and are short more often
    # than SSIDs ("eva"), so only the length and the generic names count.
    lowered = name.strip().lower()
    return len(lowered) >= _MIN_COLLECTED_LENGTH and lowered not in _GENERIC_ACCOUNTS and "<" not in lowered


class _Collected:
    """Value -> placeholder of the SSIDs and serial numbers found by key,
    and the user profile names found in C:\\Users\\<name>\\ paths."""

    def __init__(self) -> None:
        self.kinds: dict[str, str] = {}
        self.names: set[str] = set()

    def add_from(self, text: str) -> None:
        for pattern, placeholder in ((_SSID, SSID), (_SERIAL, SERIAL)):
            for match in pattern.finditer(text):
                value = match.group("value").strip()
                if _worth_collecting(value):
                    self.kinds.setdefault(value, placeholder)
        # Only a name followed by a separator is certain to be the whole
        # folder name; a trailing one may have swallowed or lost a word.
        for match in _USER_PATH.finditer(text):
            if match.group("name") is not None:
                self.add_names((match.group("name"),))

    def add_names(self, names: Iterable[str]) -> None:
        for name in names:
            if isinstance(name, str) and _worth_collecting_name(name):
                self.names.add(name.strip().lower())

    def masker(self) -> "_Masker":
        return _Masker(self.kinds, self.names)


_WORD = re.compile(r"\w+")


class _ValueSet:
    """Whole-value matching of many collected values. Each text is first
    split into words, and only the values whose first word occurs in it go
    into the regex - one alternation over every value (or one regex per
    value) made a large audit log with many values take minutes."""

    def __init__(self, values: Iterable[str], ignore_case: bool) -> None:
        self._fold = str.lower if ignore_case else (lambda text: text)
        self._flags = re.IGNORECASE if ignore_case else 0
        self._by_word: dict[str, list[str]] = {}
        self._always: list[str] = []
        self._patterns: dict[tuple[str, ...], re.Pattern] = {}
        for value in values:
            # A match starts at a word boundary, so a value's first word is
            # a whole word of any text that contains it.
            first = _WORD.match(value)
            if first is None:
                self._always.append(value)
            else:
                self._by_word.setdefault(self._fold(first.group(0)), []).append(value)

    def pattern_for(self, text: str) -> re.Pattern | None:
        words = set(_WORD.findall(self._fold(text))) if self._by_word else set()
        present = list(self._always)
        for word in words & self._by_word.keys():
            present.extend(self._by_word[word])
        if not present:
            return None
        key = tuple(sorted(present, key=lambda v: (-len(v), v)))
        pattern = self._patterns.get(key)
        if pattern is None:
            # Whole values only, so a network called "Novak5G" leaves
            # "Novak5GHz"; the longest first, so "Home 5G" wins over "Home".
            pattern = re.compile(rf"(?<!\w)(?:{'|'.join(re.escape(v) for v in key)})(?!\w)", self._flags)
            if len(self._patterns) < 256:
                self._patterns[key] = pattern
        return pattern


class _Masker:
    """The collected values of one document, prepared once for all its strings."""

    def __init__(self, kinds: dict[str, str], names: set[str]) -> None:
        self._kinds = kinds
        self._values = _ValueSet(kinds, ignore_case=False)
        # Windows compares account and folder names case-insensitively.
        self._users = _ValueSet(names, ignore_case=True)

    def apply(self, text: str, kept: tuple[str, ...]) -> str:
        pattern = self._values.pattern_for(text)
        if pattern is not None:
            def one(match: re.Match) -> str:
                value = match.group(0)
                return value if value.lower() in kept else self._kinds.get(value, value)

            text = _sub(pattern, text, kept, one)
        pattern = self._users.pattern_for(text)
        if pattern is not None:
            # A profile name is personal whatever else it matches, like the
            # C:\Users\ path it came from - the kept values do not protect it.
            text = pattern.sub(USER, text)
        return text


def local_profile_names(users_dir: Path | None = None) -> list[str]:
    """The user profile folder names on this PC (C:\\Users\\*), apart from
    the shared ones - masked across a redacted document, so a name printed
    without a path after it ("PC\\Jan Novak") or at the end of a line is
    caught too. Empty off Windows or when the folder cannot be read."""
    if users_dir is None:
        if os.name != "nt":
            return []
        users_dir = Path(os.environ.get("SystemDrive", "C:") + "\\") / "Users"
    try:
        entries = list(Path(users_dir).iterdir())
    except OSError:
        return []
    names = []
    for entry in entries:
        try:
            if entry.is_dir() and entry.name.lower() not in _SHARED_PROFILES:
                names.append(entry.name)
        except OSError:
            continue
    return sorted(names)


def account_names(accounts: Iterable[str]) -> list[str]:
    """The account part of "DOMAIN\\user" names (the target user of G25),
    for `mask`: an AzureAD or renamed account ("AzureAD\\JanNovak") need not
    match its profile folder ("jan.novak"), so local_profile_names alone
    would leave it readable in a redacted report."""
    names = []
    for account in accounts:
        if isinstance(account, str) and account.strip():
            names.append(account.strip().rsplit("\\", 1)[-1])
    return names


# Report fields that are structure, not content: ids, timestamps, the
# technician's own entries and the computer name, kept on purpose.
_STRUCTURAL_KEYS = frozenset({
    "run_id", "previous_run_id", "hostname", "language", "generated_at", "previous_generated_at",
    "timestamp", "taken_at", "job", "module_id", "action_id", "kind", "risk", "category",
    "decision", "key", "label_key", "trend", "redacted",
})


def _strings(value) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in _STRUCTURAL_KEYS:
                yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def redact_data(data, keep: Iterable[str] = (), mask: Iterable[str] = ()):
    """A copy of a JSON-like value (a report, an audit entry) with every
    string redacted, except the structural fields above. SSIDs, serial
    numbers and profile names found anywhere in it - and the profile names
    in `mask` - are masked everywhere in it."""
    kept = _normalize_keep(keep)
    collected = _Collected()
    collected.add_names(mask)
    for text in _strings(data):
        collected.add_from(text)
    masker = collected.masker()

    def walk(value):
        if isinstance(value, str):
            return _redact(value, kept, masker)
        if isinstance(value, dict):
            return {k: (v if k in _STRUCTURAL_KEYS else walk(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return walk(data)
