from portablefix.i18n import _STRINGS, translate


def test_translate_known_key_sk():
    assert translate("app_title", "sk") == "PortableFix"


def test_translate_known_key_en_differs_from_sk():
    sk = translate("readonly_banner", "sk")
    en = translate("readonly_banner", "en")
    assert sk != en
    assert "administrator" in en.lower() or "admin" in en.lower()


def test_translate_unknown_language_falls_back_to_sk():
    assert translate("app_title", "de") == translate("app_title", "sk")


def test_translate_unknown_key_returns_key_itself():
    assert translate("no_such_key", "sk") == "no_such_key"


def test_sk_and_en_dicts_have_identical_keys():
    assert _STRINGS["sk"].keys() == _STRINGS["en"].keys()
