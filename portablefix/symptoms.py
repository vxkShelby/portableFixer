"""Offline symptom -> preset suggestions (research G27).

A technician starts from the client's complaint ("Outlook sa neotvára",
"nejde internet"), not from the module list. Modules/symptoms.yaml maps
complaint phrases (SK and EN) to existing catalog actions: SAFE diagnostics
to run first and the fixes that usually follow, with a one-line "why".

Matching is deliberately simple and offline: lower case, no diacritics, a
light Slovak (and English) suffix strip, a synonym table from the YAML, and
a score per phrase. Nothing here runs an action - a suggestion only selects
actions in the window, where the normal batch review approves anything that
is not SAFE.

The file lives in Modules/ rather than Data/: Modules/ is covered by
Data/SHA256SUMS and replaced by updates, Data/ ships only an allowlist.

Qt-free on purpose: the window uses it now; a later headless interface
(the doc's read-only `symptom_to_plan`) can use suggest() + plan() as is.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import ActionDef, RiskLevel

SYMPTOMS_FILE = "symptoms.yaml"
MAX_RESULTS = 3
# Below this a match is noise (one generic word out of a long phrase).
MIN_SCORE = 0.34
# A weak term ("nejde", "problem") counts, but never matches on its own.
WEAK_WEIGHT = 0.4
# Prefix matching (tlaciar / tlaciarn) only for terms at least this long.
MIN_PREFIX = 4
MAX_QUERY_CHARS = 300

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Joined, not split on: won't -> wont, wi-fi -> wifi, e-mail -> email.
_JOINERS = str.maketrans("", "", "'’`-")

# Words that say nothing about the problem. Normalized (no diacritics).
# Not "nas" ("nás"): folded it is also "NAS", the network storage box.
STOP_WORDS = frozenset(
    """
    a aj ako ale ani bo by do je ho i k ked ku ma mi mne moj moja moje mu
    na nam o od po pri s sa si so su ta tak ten to tu uz v vo vobec z ze
    zo stale strasne velmi dost uplne celkom trochu hrozne furt
    pocitac pocitaci pocitaca pc notebook notebooku ntb laptop windows win
    an and are be been i in is it its me my of on or the this to very when
    keeps computer
    """.split()
)

# Longest first; one suffix is stripped per word, never below 3 letters.
_SUFFIXES = tuple(sorted(
    """
    ovanie enie anie ovat ujem ujes ujeme ujete ovia och ami ach ovi ova ove
    ych ymi ich imi eho emu ieho iemu uje ujú uju aju eju ia ie iu ej ou om
    ov ho mi il ila ilo ili al ala alo ali at it et ing es ed s y a e i o u
    """.split(),
    key=len, reverse=True,
))


def fold(text: str) -> str:
    """Lower case without diacritics: "Neotvára" -> "neotvara"."""
    decomposed = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def stem(word: str) -> str:
    """One ending off: "spustenie" -> "spust", "pomalá" -> "pomal". A long
    ending needs a longer rest, or "pomala" would lose "ala"."""
    for suffix in _SUFFIXES:
        rest = len(word) - len(suffix)
        if word.endswith(suffix) and rest >= (4 if len(suffix) >= 3 else 3):
            return word[: -len(suffix)]
    return word


def words(text: str) -> list[tuple[str, str]]:
    """(original word, stemmed folded word) for every word that is not a
    stop word - the original is kept for the "matched: ..." hint."""
    result = []
    for match in re.finditer(r"[\w'’`-]+", str(text)[:MAX_QUERY_CHARS]):
        original = match.group(0)
        for token in _TOKEN_RE.findall(fold(original).translate(_JOINERS)):
            if token not in STOP_WORDS:
                result.append((original.strip("'’`-"), stem(token)))
    return result


@dataclass(frozen=True)
class Symptom:
    id: str
    title_sk: str
    title_en: str
    why_sk: str
    why_en: str
    phrases: tuple[str, ...]
    diagnostics: tuple[str, ...]
    fixes: tuple[str, ...] = ()

    def title(self, language: str) -> str:
        return self.title_en if language == "en" else self.title_sk

    def why(self, language: str) -> str:
        return self.why_en if language == "en" else self.why_sk


@dataclass
class SymptomTable:
    symptoms: list[Symptom] = field(default_factory=list)
    # stemmed variant -> stemmed canonical term
    synonyms: dict[str, str] = field(default_factory=dict)
    weak: frozenset[str] = frozenset()
    # symptom id -> one term set per phrase (built once, on load)
    _phrase_terms: dict[str, list[frozenset[str]]] = field(default_factory=dict, repr=False)

    def terms(self, text: str) -> list[tuple[str, str]]:
        """(original word, canonical term) - words() plus the synonym table."""
        return [(orig, self.synonyms.get(term, term)) for orig, term in words(text)]

    def index(self) -> None:
        self._phrase_terms = {
            s.id: [ts for ts in (frozenset(t for _, t in self.terms(p)) for p in s.phrases) if ts]
            for s in self.symptoms
        }


@dataclass(frozen=True)
class SymptomMatch:
    symptom: Symptom
    score: float
    matched_words: tuple[str, ...]


class SymptomsError(ValueError):
    pass


def _str(raw: dict, key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SymptomsError(f"{where}: '{key}' must be a non-empty string")
    return " ".join(value.split())


def _id_list(raw: dict, key: str, where: str, required: bool) -> tuple[str, ...]:
    value = raw.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise SymptomsError(f"{where}: '{key}' must be a list of action ids")
    if required and not value:
        raise SymptomsError(f"{where}: '{key}' must name at least one action")
    return tuple(dict.fromkeys(value))


def parse(data) -> tuple[SymptomTable, list[str]]:
    """The table from the parsed YAML. A malformed symptom is skipped with an
    error line (one bad entry must not hide the others); a malformed
    top level raises SymptomsError."""
    if not isinstance(data, dict):
        raise SymptomsError("the file must be a mapping with 'symptoms'")
    errors: list[str] = []
    table = SymptomTable()

    raw_synonyms = data.get("synonyms") or {}
    if not isinstance(raw_synonyms, dict):
        raise SymptomsError("'synonyms' must map a term to a list of variants")
    # stemmed word -> the canonical term that claimed it (itself included):
    # after stemming two entries can collide ("spusti" under one term,
    # "spustenie" = the other term) and the second would silently win.
    owners: dict[str, str] = {}
    for canonical, variants in raw_synonyms.items():
        canon = [t for _, t in words(str(canonical))]
        if len(canon) != 1 or not isinstance(variants, list):
            errors.append(f"synonyms: '{canonical}' must be one word with a list of variants")
            continue
        for word, stemmed in [(canonical, canon)] + [(v, [t for _, t in words(v)] if isinstance(v, str) else []) for v in variants]:
            if len(stemmed) != 1:
                errors.append(f"synonyms.{canonical}: '{word}' must be one word")
                continue
            claimed = owners.setdefault(stemmed[0], canon[0])
            if claimed != canon[0]:
                errors.append(f"synonyms.{canonical}: '{word}' is already a variant of '{claimed}'")
            elif stemmed[0] != canon[0]:
                table.synonyms[stemmed[0]] = canon[0]

    raw_weak = data.get("weak") or []
    if not isinstance(raw_weak, list):
        raise SymptomsError("'weak' must be a list of words")
    # A bare `no` in YAML is False - str() would quietly make it "False".
    for w in raw_weak:
        if not isinstance(w, str):
            errors.append(f"weak: {w!r} is not a word (quote it)")
    table.weak = frozenset(table.synonyms.get(t, t) for w in raw_weak if isinstance(w, str) for _, t in words(w))

    raw_symptoms = data.get("symptoms")
    if not isinstance(raw_symptoms, list):
        raise SymptomsError("'symptoms' must be a list")
    seen: set[str] = set()
    for index, raw in enumerate(raw_symptoms):
        where = f"symptoms[{index}]"
        try:
            if not isinstance(raw, dict):
                raise SymptomsError(f"{where}: must be a mapping")
            sid = raw.get("id")
            if not isinstance(sid, str) or not _ID_RE.match(sid):
                raise SymptomsError(f"{where}: 'id' must be lower_snake_case")
            where = f"symptom '{sid}'"
            if sid in seen:
                raise SymptomsError(f"{where}: duplicate id")
            phrases = []
            for key in ("phrases_sk", "phrases_en"):
                value = raw.get(key) or []
                if not isinstance(value, list) or not all(isinstance(p, str) and p.strip() for p in value):
                    raise SymptomsError(f"{where}: '{key}' must be a list of phrases")
                phrases += value
            if not phrases:
                raise SymptomsError(f"{where}: needs at least one phrase")
            symptom = Symptom(
                id=sid,
                title_sk=_str(raw, "title_sk", where), title_en=_str(raw, "title_en", where),
                why_sk=_str(raw, "why_sk", where), why_en=_str(raw, "why_en", where),
                phrases=tuple(phrases),
                diagnostics=_id_list(raw, "diagnostics", where, required=True),
                fixes=_id_list(raw, "fixes", where, required=False),
            )
        except SymptomsError as exc:
            errors.append(str(exc))
            continue
        seen.add(sid)
        table.symptoms.append(symptom)
    table.index()
    return table, errors


def check_catalog(table: SymptomTable, actions: dict[str, ActionDef]) -> list[str]:
    """Drops what the catalog does not back, in place: unknown action ids,
    and a "diagnostic" that is not SAFE - a diagnostic is what a suggestion
    may select to run first (and what a read-only interface may one day run
    unattended), so it must never change the system. A symptom left with no
    diagnostic is dropped. Returns one line per problem."""
    errors: list[str] = []
    kept: list[Symptom] = []
    for symptom in table.symptoms:
        diagnostics = []
        for aid in symptom.diagnostics:
            action = actions.get(aid)
            if action is None:
                errors.append(f"symptom '{symptom.id}': unknown diagnostic '{aid}'")
            elif action.risk is not RiskLevel.SAFE:
                errors.append(f"symptom '{symptom.id}': diagnostic '{aid}' is {action.risk.value}, not SAFE")
            else:
                diagnostics.append(aid)
        fixes = []
        for aid in symptom.fixes:
            if aid not in actions:
                errors.append(f"symptom '{symptom.id}': unknown fix '{aid}'")
            elif aid not in diagnostics:
                fixes.append(aid)
        if not diagnostics:
            errors.append(f"symptom '{symptom.id}': no usable diagnostic - skipped")
            continue
        kept.append(Symptom(
            symptom.id, symptom.title_sk, symptom.title_en, symptom.why_sk, symptom.why_en,
            symptom.phrases, tuple(diagnostics), tuple(fixes),
        ))
    table.symptoms = kept
    table.index()
    return errors


def load(path: Path, actions: dict[str, ActionDef] | None = None) -> tuple[SymptomTable, list[str]]:
    """The table from a file, never raising: a missing or broken file is an
    empty table and an error line (the window works without suggestions)."""
    path = Path(path)
    if not path.is_file():
        return SymptomTable(), []
    try:
        table, errors = parse(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (SymptomsError, yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        return SymptomTable(), [f"{path}: {exc}"]
    if actions is not None:
        errors += check_catalog(table, actions)
    return table, [f"{path}: {e}" for e in errors]


def _term_match(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= MIN_PREFIX and long_.startswith(short)


def _score(query: list[tuple[str, str]], phrase: frozenset[str], weak: frozenset[str]) -> tuple[float, set[str]]:
    """0..1 - how much of the phrase the complaint covers (mostly) and how
    much of the complaint the phrase explains; 0 without a strong term."""
    def weight(term: str) -> float:
        return WEAK_WEIGHT if term in weak else 1.0

    q_terms = {t for _, t in query}
    hit_phrase = {p for p in phrase if any(_term_match(q, p) for q in q_terms)}
    hit_query = {q for q in q_terms if any(_term_match(q, p) for p in phrase)}
    if not any(t not in weak for t in hit_query):
        return 0.0, set()
    phrase_cover = sum(map(weight, hit_phrase)) / sum(map(weight, phrase))
    query_cover = sum(map(weight, hit_query)) / sum(map(weight, q_terms))
    return 0.65 * phrase_cover + 0.35 * query_cover, hit_query


def suggest(table: SymptomTable, text: str, limit: int = MAX_RESULTS) -> list[SymptomMatch]:
    """The best symptoms for a complaint, best first, at most `limit`."""
    query = table.terms(text)
    if not query:
        return []
    results = []
    for symptom in table.symptoms:
        best, best_hits = 0.0, set()
        for phrase in table._phrase_terms.get(symptom.id, []):
            score, hits = _score(query, phrase, table.weak)
            if score > best:
                best, best_hits = score, hits
        if best >= MIN_SCORE:
            matched = tuple(dict.fromkeys(orig for orig, term in query if term in best_hits))
            results.append(SymptomMatch(symptom, round(best, 3), matched))
    results.sort(key=lambda m: (-m.score, m.symptom.id))
    return results[:limit]


def plan(symptom: Symptom, actions: dict[str, ActionDef], language: str) -> dict:
    """What a suggestion would select, as plain data: the diagnostics (SAFE
    by check_catalog) and the fixes with their risk. `needs_approval` says a
    fix is not SAFE - the window's batch review approves those, and any
    future headless caller must hand them to the GUI, never run them."""
    def entry(aid: str) -> dict:
        action = actions.get(aid)
        return {
            "id": aid,
            "label": action.label(language) if action else aid,
            "risk": action.risk.value if action else "UNKNOWN",
        }

    fixes = [entry(aid) for aid in symptom.fixes if aid in actions]
    return {
        "symptom": symptom.id,
        "title": symptom.title(language),
        "why": symptom.why(language),
        "diagnostics": [entry(aid) for aid in symptom.diagnostics if aid in actions],
        "fixes": fixes,
        "needs_approval": any(f["risk"] != RiskLevel.SAFE.value for f in fixes),
    }
