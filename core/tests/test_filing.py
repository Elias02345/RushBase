from datetime import date

import pytest

from rushbase_core.filing import (
    DEFAULT_TEMPLATE,
    FilingContext,
    TemplateError,
    fit_path_budget,
    parse_template,
    reconcile_case,
    render_directory,
)

CTX = FilingContext(
    media_type="video",
    capture_date=date(2026, 3, 9),
    batch_label="Harbour/Day 1",
    camera_model="ILME-FX3",
)


def test_default_template_renders_the_documented_layout() -> None:
    rendered = render_directory(parse_template(DEFAULT_TEMPLATE), CTX)
    assert rendered.path == "Content/video/2026/2026-03-09_Harbour_Day 1"
    assert any("sanitised" in w for w in rendered.warnings)


def test_fallbacks_apply_in_order_placeholder_rule_builtin() -> None:
    template = parse_template("Content/{field.brand|General}/{batch_hint_location}/{camera_model}")
    empty = FilingContext(media_type="photo")
    rendered = render_directory(template, empty, {"batch_hint_location": "Nowhere"})
    assert rendered.components == ("Content", "General", "Nowhere", "unknown")
    assert any("camera_model" in w for w in rendered.warnings)


def test_missing_capture_date_uses_the_undated_default() -> None:
    rendered = render_directory(
        parse_template(DEFAULT_TEMPLATE), FilingContext(media_type="audio", batch_label=None)
    )
    assert rendered.path == "Content/audio/undated/undated_Unsorted"


def test_date_without_format_uses_iso_and_taxonomy_values_are_used() -> None:
    ctx = FilingContext(
        media_type="video", capture_date=date(2025, 1, 2), taxonomy={"brand": "Acme: Berlin"}
    )
    rendered = render_directory(parse_template("Content/{field.brand}/{capture_date}"), ctx)
    assert rendered.components == ("Content", "Acme_ Berlin", "2025-01-02")


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("Library/{media_type}", "must start with"),
        ("Content", "at least one directory"),
        ("Content/{bogus}", "unknown field"),
        ("Content/{media_type:%Y}", "does not take a format"),
        ("Content/{capture_date:%Q}", "unsupported date directive"),
        ("Content/{capture_date:}", "empty date format"),
        ("Content/{media_type", "unbalanced"),
        ("Content/media}", "unbalanced"),
        ("Content//x", "empty path segment"),
        ("Content/{media_type|}", "empty fallback"),
        ("Content/a/b/c/d/e/f", "too deep"),
        ("Content/{field.bad key}", "unknown field"),
    ],
)
def test_invalid_templates_are_rejected(template: str, message: str) -> None:
    with pytest.raises(TemplateError, match=message):
        parse_template(template)


def test_slashes_inside_a_placeholder_do_not_split_components() -> None:
    template = parse_template("Content/{capture_date:%Y-%m}/{media_type}")
    assert template.depth == 2
    assert template.fields() == {"capture_date", "media_type"}


def test_maximum_depth_is_five_levels_below_content() -> None:
    assert parse_template("Content/a/b/c/d/e").depth == 5


def test_budget_shrinks_the_longest_component_and_reports_failure() -> None:
    components = ("Content", "video", "x" * 120, "short")
    fitted, fits = fit_path_budget(components, tail_length=40, budget=100)
    assert fits
    assert len("/".join(fitted)) + 1 + 40 <= 100
    assert (fitted[0], fitted[1], fitted[3]) == ("Content", "video", "short")
    _, fits = fit_path_budget(("Content", "a" * 8, "b" * 8), tail_length=300, budget=100)
    assert not fits


def test_reconcile_adopts_existing_spelling_case_and_normalisation() -> None:
    existing = {
        (): ["Content", "Input"],
        ("Content",): ["Video", "Photo"],
        ("Content", "Video"): ["2026"],
        ("Content", "Video", "2026"): ["Café"],  # NFC
    }
    nfd = "Café"
    components, notes = reconcile_case(
        ("Content", "video", "2026", nfd), lambda parts: existing.get(parts, [])
    )
    assert components == ("Content", "Video", "2026", "Café")
    assert len(notes) == 2
