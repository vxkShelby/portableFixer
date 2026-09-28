"""Research G27: offline symptom -> preset suggestions (portablefix/symptoms.py)."""

from pathlib import Path

import pytest

from portablefix import symptoms
from portablefix.models import ActionDef, RiskLevel
from portablefix.module_engine import load_all_modules

MODULES_DIR = Path(__file__).resolve().parent.parent / "Modules"
SYMPTOMS_PATH = MODULES_DIR / symptoms.SYMPTOMS_FILE


@pytest.fixture(scope="module")
def catalog() -> dict[str, ActionDef]:
    modules, errors = load_all_modules(MODULES_DIR)
    assert errors == []
    return {a.id: a for m in modules for a in m.actions}


@pytest.fixture(scope="module")
def table(catalog):
    loaded, errors = symptoms.load(SYMPTOMS_PATH, catalog)
    assert errors == []
    return loaded


def _top(table, text):
    found = symptoms.suggest(table, text)
    return found[0].symptom.id if found else None


# --- the shipped file ---------------------------------------------------------

def test_shipped_file_loads_clean_against_the_catalog(catalog):
    table, errors = symptoms.load(SYMPTOMS_PATH, catalog)
    assert errors == []
    assert len(table.symptoms) >= 3


def test_every_shipped_diagnostic_is_safe_and_every_fix_exists(table, catalog):
    for symptom in table.symptoms:
        assert symptom.diagnostics, symptom.id
        for aid in symptom.diagnostics:
            assert catalog[aid].risk is RiskLevel.SAFE, f"{symptom.id}: {aid}"
        for aid in symptom.fixes:
            assert aid in catalog, f"{symptom.id}: {aid}"


@pytest.mark.parametrize("complaint, expected", [
    ("Outlook sa neotvára", "outlook_wont_open"),
    ("outlook sa neotvara", "outlook_wont_open"),
    ("OUTLOOK SA NEOTVÁRAJÚ", "outlook_wont_open"),
    ("nejde internet", "no_internet"),
    ("internet nefunguje", "no_internet"),
    ("pomaly sa spúšťa", "slow_startup"),
    ("počítač strašne dlho nabieha", "slow_startup"),
    ("slow boot", "slow_startup"),
    ("my email is not working", "outlook_wont_open"),
])
def test_client_complaints_find_their_symptom(table, complaint, expected):
    assert _top(table, complaint) == expected


def test_a_generic_word_alone_matches_nothing(table):
    assert symptoms.suggest(table, "nejde") == []
    assert symptoms.suggest(table, "nefunguje to") == []


def test_empty_and_stop_word_queries_match_nothing(table):
    assert symptoms.suggest(table, "") == []
    assert symptoms.suggest(table, "   ") == []
    assert symptoms.suggest(table, "sa to je") == []


def test_matched_words_are_the_technicians_own(table):
    match = symptoms.suggest(table, "Outlook sa neotvára")[0]
    assert match.matched_words == ("Outlook", "neotvára")


# --- normalization ------------------------------------------------------------

def test_fold_removes_diacritics_and_case():
    assert symptoms.fold("Tlačiareň NEJDE, ľšťžýáíé") == "tlaciaren nejde, lstzyaie"


def test_stem_strips_one_suffix_and_keeps_three_letters():
    assert symptoms.stem("spustenie") == "spust"
    assert symptoms.stem("pomaly") == "pomal"
    assert symptoms.stem("pomala") == "pomal"  # not "pom": a long ending needs a longer rest
    assert symptoms.stem("net") == "net"


def test_words_drop_stop_words_and_join_apostrophes():
    assert [t for _, t in symptoms.words("Outlook won't open")] == ["outlook", "wont", "open"]
    assert [t for _, t in symptoms.words("počítač sa to")] == []


# --- the loader ---------------------------------------------------------------

def _entry(**overrides):
    entry = {
        "id": "printer_offline", "title_sk": "Tlačiareň", "title_en": "Printer",
        "why_sk": "Preto.", "why_en": "Because.",
        "phrases_sk": ["nejde tlačiareň"], "phrases_en": ["printer offline"],
        "diagnostics": ["safe_one"], "fixes": ["moderate_one"],
    }
    entry.update(overrides)
    return entry


def _action(aid, risk):
    return ActionDef(id=aid, label_sk=aid, label_en=aid, risk=risk, command="Write-Output x")


FAKE_CATALOG = {
    "safe_one": _action("safe_one", RiskLevel.SAFE),
    "moderate_one": _action("moderate_one", RiskLevel.MODERATE),
}


def test_a_non_safe_diagnostic_is_dropped_with_an_error():
    table, errors = symptoms.parse({"symptoms": [_entry(diagnostics=["moderate_one", "safe_one"])]})
    assert errors == []
    errors = symptoms.check_catalog(table, FAKE_CATALOG)
    assert any("moderate_one" in e and "not SAFE" in e for e in errors)
    assert table.symptoms[0].diagnostics == ("safe_one",)


def test_a_symptom_without_a_usable_diagnostic_is_skipped():
    table, _ = symptoms.parse({"symptoms": [_entry(diagnostics=["moderate_one", "nope"])]})
    errors = symptoms.check_catalog(table, FAKE_CATALOG)
    assert table.symptoms == []
    assert any("unknown diagnostic 'nope'" in e for e in errors)
    assert symptoms.suggest(table, "nejde tlačiareň") == []


def test_unknown_fix_is_dropped():
    table, _ = symptoms.parse({"symptoms": [_entry(fixes=["gone", "moderate_one"])]})
    errors = symptoms.check_catalog(table, FAKE_CATALOG)
    assert errors == ["symptom 'printer_offline': unknown fix 'gone'"]
    assert table.symptoms[0].fixes == ("moderate_one",)


def test_one_malformed_symptom_does_not_hide_the_others():
    table, errors = symptoms.parse({"symptoms": [_entry(id="Bad Id"), _entry(), _entry()]})
    assert [s.id for s in table.symptoms] == ["printer_offline"]
    assert len(errors) == 2  # the bad id, the duplicate


def test_colliding_synonym_variants_are_reported():
    _, errors = symptoms.parse({
        "synonyms": {"spusta": ["spustenie"], "otvara": ["spusti"]},
        "symptoms": [_entry()],
    })
    assert any("already a variant" in e for e in errors)


def test_synonyms_apply_to_query_and_phrases():
    table, _ = symptoms.parse({
        "synonyms": {"tlaciaren": ["printer", "tlac"]},
        "weak": ["nejde"],
        "symptoms": [_entry(phrases_sk=["nejde tlačiareň"], phrases_en=[])],
    })
    assert _top(table, "printer nejde") == "printer_offline"
    assert _top(table, "tlač nejde") == "printer_offline"


def test_missing_file_is_an_empty_table(tmp_path):
    table, errors = symptoms.load(tmp_path / "nope.yaml")
    assert table.symptoms == [] and errors == []


def test_broken_file_is_an_empty_table_and_an_error(tmp_path):
    path = tmp_path / "symptoms.yaml"
    path.write_text("symptoms: [unclosed", encoding="utf-8")
    table, errors = symptoms.load(path)
    assert table.symptoms == []
    assert len(errors) == 1


def test_symptoms_file_is_not_mistaken_for_a_module():
    # Modules/*/actions.yaml is the module glob; the top-level file is not one.
    modules, errors = load_all_modules(MODULES_DIR)
    assert errors == []
    assert all(m.module_id != "symptoms" for m in modules)


# --- the plan -----------------------------------------------------------------

def test_plan_lists_risks_and_flags_approval():
    table, _ = symptoms.parse({"symptoms": [_entry()]})
    symptoms.check_catalog(table, FAKE_CATALOG)
    result = symptoms.plan(table.symptoms[0], FAKE_CATALOG, "en")
    assert result["diagnostics"] == [{"id": "safe_one", "label": "safe_one", "risk": "SAFE"}]
    assert result["fixes"] == [{"id": "moderate_one", "label": "moderate_one", "risk": "MODERATE"}]
    assert result["needs_approval"] is True
    assert result["title"] == "Printer" and result["why"] == "Because."


def test_plan_without_fixes_needs_no_approval():
    table, _ = symptoms.parse({"symptoms": [_entry(fixes=[])]})
    symptoms.check_catalog(table, FAKE_CATALOG)
    assert symptoms.plan(table.symptoms[0], FAKE_CATALOG, "sk")["needs_approval"] is False
