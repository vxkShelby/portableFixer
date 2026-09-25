"""Masking personal data for a report handed to the client (research G20).

With the technician's "Redact for the client" setting on, the report and the
handoff package's own text files go out with user names in paths, IP and
MAC addresses, serial numbers, product-key fragments and Wi-Fi network names
replaced by placeholders. Applied only when those files are rendered or
exported - the audit log on the stick always keeps the full record.

Pure and Qt-free on purpose: the same functions serve the report, the
handoff zip and the tests. Everything here matches PortableFix's own output
(PowerShell property names such as SerialNumber, which are never localized)
or the shape of a value (an address, a key) - never localized Windows text.
Best effort by design: a value printed without anything that identifies it
(a serial number in a table column) cannot be told apart from any other text.
"""

import ipaddress
import re
from collections.abc import Iterable

USER = "<user>"
IP = "<ip>"
MAC = "<mac>"
SERIAL = "<serial>"
KEY = "<key>"
SSID = "<ssid>"

# Profile folders every Windows has - naming them says nothing about the client.
_SHARED_PROFILES = {"public", "default", "default user", "all users", "defaultapppool"}

# C:\Users\<name>\..., also with "/" or a JSON-escaped "\\" as separator. The
# name runs up to the next separator; one with nothing after it ends at
# whitespace or a quote, because the rest of the line may be ordinary text.
_USER_PATH = re.compile(
    r"(?P<prefix>(?<![A-Za-z])[A-Za-z]:(?P<sep>\\\\|\\|/)Users(?P=sep))"
    r"(?:(?P<name>[^\\/:*?\"<>|\r\n\t]+?)(?=(?P=sep))|(?P<tail>[^\\/:*?\"<>|\s'&;,)\]]+))",
    re.IGNORECASE,
)

# Four dotted numbers that are not part of a longer dotted run (10.0.26100.1
# fails on 26100 anyway; 1.2.3.4.5 is a version, not an address).
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)")
# A four-part version that fits in octets ("Version=2.0.0.0",
# "FileVersion : 6.3.9600.1") is named as one right before it.
_VERSION_LABEL = re.compile(r"(?:version|\bver\.?|\bbuild)\"?[ \t]*[:=]?[ \t]*\"?$", re.IGNORECASE)

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
_SERIAL = re.compile(
    r"(?P<key>\b(?:[A-Za-z]*SerialNumber\w*|Serial Number|Serial)\"?[ \t]*[:=][ \t]*\"?)"
    r"(?P<value>[^\s\",;][^\r\n\",;]*?)(?=\s*(?:[\",;\r\n]|$))",
    re.IGNORECASE,
)

# The partial product key line of SoftwareLicensingProduct (five characters).
_PARTIAL_KEY = re.compile(
    r"(?P<key>\bPartialProductKey\"?[ \t]*[:=][ \t]*\"?)(?P<value>[A-Za-z0-9]{5})\b",
)

# "SSID : name" as netsh wlan prints it (the field names are not localized)
# and "SSID": "..." in JSON. BSSID is a MAC and handled by _MAC.
_SSID = re.compile(
    r"(?P<key>(?<![A-Za-z])SSID\"?[ \t]*[:=][ \t]*\"?)(?P<value>[^\r\n\"]*?[^\s\"])(?=\s*(?:\"|\r|\n|$))",
)

# Values collected once (an SSID, a serial number) are masked wherever else
# they appear too - e.g. the Wi-Fi profile line naming the same network. Too
# short a value would mask ordinary words.
_MIN_COLLECTED_LENGTH = 3


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


def redact_text(text: str, keep: Iterable[str] = ()) -> str:
    """`text` with personal data replaced by placeholders (<user>, <ip>,
    <mac>, <serial>, <key>, <ssid>). `keep`: values never masked (the
    computer name, the technician and client). Idempotent."""
    if not isinstance(text, str) or not text:
        return text
    kept = _normalize_keep(keep)

    def user(match: re.Match) -> str:
        name = match.group("name") if match.group("name") is not None else match.group("tail")
        if name.strip().lower() in _SHARED_PROFILES:
            return match.group(0)
        return match.group("prefix") + USER

    collected = _Collected()
    collected.add_from(text)
    text = _sub(_USER_PATH, text, (), user)
    text = _sub(_SSID, text, kept, lambda m: m.group("key") + SSID)
    text = _sub(_SERIAL, text, kept, lambda m: m.group("key") + SERIAL)
    text = _sub(_PARTIAL_KEY, text, kept, lambda m: m.group("key") + KEY)
    text = _sub(_MAC, text, kept, lambda m: MAC)
    text = _sub(_IPV6, text, kept, lambda m: m.group(0) if _keep_ipv6(m.group(0)) else IP)
    text = _sub(_IPV4, text, kept, lambda m: m.group(0) if _keep_ipv4(m.group(0)) or _after_version_label(m) else IP)
    text = _sub(_KEY, text, kept, lambda m: KEY if _looks_like_key(m.group(0)) else m.group(0))
    return _mask_values(text, collected.kinds, kept)


def _looks_like_key(value: str) -> bool:
    groups = value.split("-")
    if len(groups) >= 3:
        return True
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


class _Collected:
    """Value -> placeholder of the SSIDs and serial numbers found by key."""

    def __init__(self) -> None:
        self.kinds: dict[str, str] = {}

    def add_from(self, text: str) -> None:
        for pattern, placeholder in ((_SSID, SSID), (_SERIAL, SERIAL)):
            for match in pattern.finditer(text):
                value = match.group("value").strip()
                if len(value) >= _MIN_COLLECTED_LENGTH and value not in (SSID, SERIAL):
                    self.kinds.setdefault(value, placeholder)


def _mask_values(text: str, kinds: dict[str, str], keep: tuple[str, ...]) -> str:
    for value in sorted(kinds, key=len, reverse=True):
        if len(value) < _MIN_COLLECTED_LENGTH or value.lower() in keep:
            continue
        # Whole values only, so a network called "Home" leaves "HomeGroup".
        pattern = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")
        placeholder = kinds[value]
        text = _sub(pattern, text, keep, lambda m, p=placeholder: p)
    return text


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


def redact_data(data, keep: Iterable[str] = ()):
    """A copy of a JSON-like value (a report, an audit entry) with every
    string redacted, except the structural fields above. SSIDs and serial
    numbers found anywhere in it are masked everywhere in it."""
    kept = tuple(keep)
    collected = _Collected()
    for text in _strings(data):
        collected.add_from(text)
    extra = dict(collected.kinds)

    def walk(value):
        if isinstance(value, str):
            return _redact_with(value, kept, extra)
        if isinstance(value, dict):
            return {k: (v if k in _STRUCTURAL_KEYS else walk(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return walk(data)


def _redact_with(text: str, keep: tuple[str, ...], kinds: dict[str, str]) -> str:
    redacted = redact_text(text, keep)
    return _mask_values(redacted, kinds, _normalize_keep(keep))
