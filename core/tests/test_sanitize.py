import unicodedata

import pytest

from rushbase_core.sanitize import (
    collision_key,
    first_free_index,
    numbered_name,
    sanitize_component,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ('a<b>c:d"e/f\\g|h?i*j', "a_b_c_d_e_f_g_h_i_j"),
        ("tab\there\nnewline\x00nul\x7f", "tab_here_newline_nul_"),
        ("trailing dot.", "trailing dot"),
        ("trailing space   ", "trailing space"),
        ("end. . .", "end"),
        ("  leading space", "leading space"),
        (".hidden", "_hidden"),
        ("..", "_"),
        (".", "_"),
        ("", "_"),
        ("   ", "_"),
        ("...", "_"),
    ],
)
def test_reserved_characters_dots_and_blanks(raw: str, expected: str) -> None:
    assert sanitize_component(raw) == expected


@pytest.mark.parametrize("name", ["CON", "con", "Prn", "AUX", "NUL", "COM1", "lpt9", "CON.txt"])
def test_reserved_device_names_get_a_prefix(name: str) -> None:
    assert sanitize_component(name) == f"_{name}"


def test_names_that_merely_start_like_reserved_names_are_kept() -> None:
    assert sanitize_component("CONSOLE") == "CONSOLE"
    assert sanitize_component("COM10") == "COM10"


def test_unicode_is_normalised_to_nfc() -> None:
    decomposed = unicodedata.normalize("NFD", "Café Zürich")
    assert decomposed != "Café Zürich"
    assert sanitize_component(decomposed) == "Café Zürich"


def test_component_limits_in_characters_and_bytes() -> None:
    assert len(sanitize_component("x" * 500)) == 120
    long_cjk = sanitize_component("日" * 200, max_chars=200)
    assert len(long_cjk.encode("utf-8")) <= 255
    assert sanitize_component("ab" * 70 + ". ", max_chars=140).endswith("b")
    cut_into_dot = sanitize_component("a" * 119 + ". more")
    assert not cut_into_dot.endswith((".", " "))


def test_collision_key_ignores_case_and_normalisation() -> None:
    assert collision_key("Café") == collision_key("CAFÉ")
    assert collision_key("Straße") == collision_key("STRASSE")


def test_numbered_names_keep_extension_and_hidden_files() -> None:
    assert numbered_name("clip.mov", 1) == "clip.mov"
    assert numbered_name("clip.mov", 2) == "clip_2.mov"
    assert numbered_name("archive.tar.gz", 3) == "archive.tar_3.gz"
    assert numbered_name("noext", 2) == "noext_2"
    assert numbered_name(".hidden", 2) == ".hidden_2"


def test_first_free_index_is_shared_by_all_files_of_an_asset() -> None:
    taken = {collision_key(n) for n in ["IMG_1.JPG", "img_1_2.jpg"]}
    assert first_free_index(["IMG_1.CR3", "IMG_1.JPG"], taken) == 3
    assert first_free_index(["IMG_2.CR3"], taken) == 1
