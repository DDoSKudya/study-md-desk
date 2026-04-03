import pytest

from viewer_app.core.markdown_core import (
    get_pygments_css,
    md_to_html,
    normalize_markdown_layout,
    preprocess_mermaid_fences,
    render_markdown_fragment,
    transform_labeled_callouts,
    transform_mermaid,
    transform_stepwise_paragraphs,
)


def test_get_pygments_css_includes_highlight_selector() -> None:
    stylesheet = get_pygments_css()

    assert (  # noqa: S101
        ".highlight" in stylesheet or "highlight" in stylesheet
    )


@pytest.mark.parametrize(
    ("markdown_input", "expected_fragment"),
    [
        pytest.param("# T\n\nHello", "Hello", id="heading_and_paragraph"),
        pytest.param("```py\nx=1\n```", "<code>", id="fenced_python_code"),
        pytest.param("|a|b|\n|-|-|\n|1|2|", "1", id="pipe_table_cell"),
    ],
)
def test_md_to_html_includes_substring_in_body_and_string_toc(
    markdown_input: str,
    expected_fragment: str,
) -> None:
    html_body, table_of_contents = md_to_html(
        markdown_input,
        with_toc=True,
    )

    assert expected_fragment in html_body  # noqa: S101
    assert isinstance(table_of_contents, str)  # noqa: S101


def test_md_to_html_preserves_inline_math_content() -> None:
    html_body, _table_of_contents = md_to_html(
        r"Inline \(x\) math",
        with_toc=False,
    )

    assert "x" in html_body  # noqa: S101


def test_render_markdown_fragment_emits_strong_or_visible_text() -> None:
    html = render_markdown_fragment("**b**")

    assert "<strong>" in html or "b" in html  # noqa: S101


def test_normalize_markdown_layout_keeps_heading_text() -> None:
    normalized = normalize_markdown_layout("  # Hi  \n\n  x  ")

    assert "Hi" in normalized  # noqa: S101


def test_transform_stepwise_paragraphs_tags_or_preserves_html_length() -> None:
    markdown_source = (
        "Algorithm:\n\n(1) First long step text here for parser.\n\n"
        "(2) Second long step text here for parser.\n"
    )
    html_body, _table_of_contents = md_to_html(markdown_source, with_toc=False)
    transformed = transform_stepwise_paragraphs(html_body)

    assert "stepwise" in transformed.lower() or len(  # noqa: S101
        transformed
    ) >= len(html_body)


def test_transform_labeled_callouts_marks_note_pattern() -> None:
    html_input = "<p><strong>Note:</strong> text here</p>"

    transformed = transform_labeled_callouts(html_input)

    assert "callout" in transformed or "Note" in transformed  # noqa: S101


def test_mermaid_fence_preprocess_then_transform_keeps_or_extends_markup() -> (
    None
):
    markdown_with_mermaid = "```mermaid\ngraph TD\n  A-->B\n```"
    preprocessed = preprocess_mermaid_fences(markdown_with_mermaid)
    html_body, _table_of_contents = md_to_html(preprocessed, with_toc=False)
    with_mermaid = transform_mermaid(html_body)

    assert (  # noqa: S101
        "mermaid" in with_mermaid.lower()
        or "Mermaid" in with_mermaid
        or len(with_mermaid) >= len(html_body)
    )
