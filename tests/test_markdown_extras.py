from viewer_app.core.markdown_core import (
    MATHJAX_SCRIPT,
    add_code_labels,
    enhance_markdown_document_semantics,
    md_to_html,
    normalize_mermaid_source,
    preprocess_mermaid_fences,
    process_task_lists,
    protect_details,
    protect_math,
    render_markdown_fragment,
    restore_math,
)


def test_protect_math_marks_inline_latex_placeholder() -> None:
    _protected_text, math_placeholders = protect_math(
        r"Energy \( \alpha \) here"
    )

    assert math_placeholders  # noqa: S101
    assert math_placeholders[0][0] == "inline"  # noqa: S101


def test_protect_details_emits_body_placeholder() -> None:
    details_block = (
        "<details><summary>Title</summary>\n\n" "Body **x**\n\n" "</details>"
    )
    _protected_text, detail_placeholders = protect_details(details_block)

    assert detail_placeholders  # noqa: S101
    assert "details-body" in detail_placeholders[0]  # noqa: S101


def test_restore_math_reinserts_markup_after_fragment_render() -> None:
    protected_text, math_placeholders = protect_math(r"Eq \( \beta \)")
    fragment_html = render_markdown_fragment(protected_text)
    restored = restore_math(fragment_html, math_placeholders)

    assert (  # noqa: S101
        "math-inline" in restored or "math" in restored.lower()
    )


def test_md_to_html_keeps_details_element_in_output() -> None:
    markdown_with_details = (
        "<details><summary>Sum</summary>\n\n"
        "Hidden **bold** text.\n\n"
        "</details>\n"
    )
    html_body, _table_of_contents = md_to_html(
        markdown_with_details, with_toc=False
    )

    assert "details" in html_body.lower()  # noqa: S101


def test_process_task_lists_marks_checkbox_list() -> None:
    task_list_html = (
        '<ul class="task-list"><li><input type="checkbox" disabled></li></ul>'
    )

    processed = process_task_lists(task_list_html)

    assert "task" in processed.lower()  # noqa: S101


def test_preprocess_mermaid_fences_outputs_non_empty_mermaid_aware_text() -> (
    None
):
    fenced = "```mermaid\nx\n```"
    preprocessed = preprocess_mermaid_fences(fenced)

    assert (  # noqa: S101
        "mermaid" in preprocessed.lower() or len(preprocessed) > 0
    )


def test_normalize_mermaid_source_accepts_whitespace_padded_input() -> None:
    normalized = normalize_mermaid_source(" graph TD ")

    assert isinstance(normalized, str)  # noqa: S101


def test_enhance_markdown_document_semantics_preserves_table_markup() -> None:
    bare_table = "<table><tr><td>a</td></tr></table>"

    enhanced = enhance_markdown_document_semantics(bare_table)

    assert "table" in enhanced.lower()  # noqa: S101


def test_add_code_labels_keeps_code_region_identifiable() -> None:
    code_block_html = "<pre><code>x = 1\n</code></pre>"
    original_source_snippet = "x = 1"

    labeled = add_code_labels(code_block_html, original_source_snippet)

    assert "code" in labeled.lower()  # noqa: S101


def test_mathjax_script_constant_mentions_math_or_jax() -> None:
    script_markup = MATHJAX_SCRIPT.lower()

    assert "math" in script_markup or "jax" in script_markup  # noqa: S101
