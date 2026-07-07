from pathlib import Path

import pytest

from title_language import (
    infer_title_language,
    load_playlist_language,
    normalize_language,
    save_playlist_language,
    title_from_filename,
    title_language_signature,
)


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


def test_playlist_language_file(tmp_path: Path):
    save_playlist_language(tmp_path, "he")
    assert load_playlist_language(tmp_path) == "he"
    stems = ["X - Being", "Y - Free"]
    sig = title_language_signature(stems, playlist_dir=tmp_path, min_frac=0.4)
    assert sig == [{"label": "he", "n": 2, "frac": 1.0}]


def test_force_language_overrides_inference():
    stems = ["A - Send My Love", "B - Being"]
    sig = title_language_signature(stems, force_language="he", min_frac=0.4)
    assert sig == [{"label": "he", "n": 2, "frac": 1.0}]


def test_normalize_language_rejects_invalid():
    with pytest.raises(ValueError):
        normalize_language("not-a-lang")


def test_title_language_signature_inferred():
    stems = [
        "A - אחת",
        "B - שתיים",
        "C - שלוש",
        "D - Send My Love",
    ]
    sig = title_language_signature(stems, min_frac=0.4)
    assert sig == [{"label": "he", "n": 3, "frac": 0.75}]
