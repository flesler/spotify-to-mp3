from title_language import infer_title_language, title_from_filename, title_language_signature


def test_title_from_filename():
    assert title_from_filename("Bonobo - Tides") == "Tides"


def test_hebrew_script():
    assert infer_title_language("אוהב אותך") == "he"


def test_spanish_without_accents():
    assert infer_title_language("Mas o Menos Bien") == "es"
    assert infer_title_language("Nina voladora") == "es"
    assert infer_title_language("En Domingo Las Ninas van a Jugar al Parque") == "es"


def test_english_untagged():
    assert infer_title_language("Send My Love") is None
    assert infer_title_language("Being") is None
    assert infer_title_language("Maagalim") is None


def test_playlist_override():
    stems = ["X - Being", "Y - Free", "Z - Maagalim"]
    sig = title_language_signature(stems, playlist="Hebreo", min_frac=0.4)
    assert sig == [{"label": "he", "n": 3, "frac": 1.0}]


def test_title_language_signature():
    stems = [
        "A - אחת",
        "B - שתיים",
        "C - שלוש",
        "D - Send My Love",
    ]
    sig = title_language_signature(stems, min_frac=0.4)
    assert sig == [{"label": "he", "n": 3, "frac": 0.75}]
