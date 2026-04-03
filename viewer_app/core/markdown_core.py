"""
This module converts Markdown into enriched HTML for a desktop viewer,
adding semantics, styling hooks, and support for math and Mermaid
diagrams.

It acts as the core Markdown rendering and post-processing pipeline in
the application.

It defines language-label maps, regex patterns, and constants to
recognize code fences, Mermaid blocks, math markers, details tags,
tables, callouts, and stepwise instructions.

Functions like get_pygments_css and add_code_labels integrate with
Pygments and the original Markdown source to label code blocks with
human-friendly language names.

Math-related helpers (protect_math and restore_math) temporarily replace
LaTeX-style inline and block math with tokens during Markdown conversion
and then restore them as escaped HTML wrappers.

Details-related helpers (protect_details and restore_details) lift
<details>/<summary> blocks out of the Markdown flow, render their inner
Markdown separately, and reinsert them as structured collapsible
sections.

normalize_markdown_layout adjusts spacing around tables and headings so
the markdown library parses tables reliably.

process_task_lists rewrites [ ] and [x] list items into disabled
checkbox list items for visual task lists.

The stepwise helpers detect paragraphs that look like “Step-by-step” or
“Algorithm” instructions and convert them into dedicated stepwise
blocks with titles, intros, and ordered lists of steps.

transform_labeled_callouts finds paragraphs labeled like “Note: …” or
“Warning - …” and turns them into semantic callout containers with
title and body.

Semantic enhancement helpers (_inject_heading_level_classes,
_mark_first_blockquote_lede, _wrap_tables_scroll,
_add_details_collapsible_class) annotate headings, the first blockquote,
tables, and details elements with classes and wrappers to improve
styling and layout.

Mermaid-related helpers (normalize_mermaid_source,
preprocess_mermaid_fences, transform_mermaid,
_mermaid_code_inner_to_source, _safe_transform_mermaid) normalize
Mermaid syntax, replace fenced blocks with <pre class="mermaid">
elements, base64-encode the source, and ensure client-side rendering
works regardless of syntax-highlighting HTML.

_render_markdown_fragment renders small Markdown snippets to HTML with
code highlighting, callouts, task lists, stepwise formatting, and
semantic enhancements but without a table of contents.

_md_convert is a low-level wrapper around the markdown library that runs
conversion with configurable extensions and optionally captures the
generated table of contents.

md_to_html is the main entry point that wires everything together: it
preprocesses  Mermaid, protects details and math, normalizes layout,
runs markdown conversion with fallback extension sets, and
post-processes the HTML (labels, math, details, tasks, stepwise,
callouts, semantics, Mermaid).

The module also exposes a MathJax configuration script string used to
enable  client-side rendering of in-page math in the final HTML.
"""

from __future__ import annotations

import base64
import re
import traceback
from re import Match, Pattern

import html
from markdown.core import Markdown
from typing import Any, Final, Literal, Mapping, Sequence, TypeAlias

_LANG_MAP: Final[dict[str, str]] = {
    "python": "Python",
    "py": "Python",
    "bash": "Bash",
    "sh": "Shell",
    "shell": "Shell",
    "zsh": "Shell",
    "sql": "SQL",
    "pgsql": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "plpgsql": "PL/pgSQL",
    "text": "Text",
    "txt": "Text",
    "plain": "Text",
    "http": "HTTP",
    "json": "JSON",
    "yaml": "YAML",
    "yml": "YAML",
    "javascript": "JavaScript",
    "js": "JavaScript",
    "typescript": "TypeScript",
    "ts": "TypeScript",
    "css": "CSS",
    "html": "HTML",
    "xml": "XML",
    "go": "Go",
    "rust": "Rust",
    "java": "Java",
    "kotlin": "Kotlin",
    "c": "C",
    "cpp": "C++",
    "cs": "C#",
    "csharp": "C#",
    "conf": "Config",
    "hcl": "HCL",
    "toml": "TOML",
    "ini": "Config",
    "dockerfile": "Dockerfile",
    "makefile": "Makefile",
    "pseudo": "Pseudocode",
    "pseudocode": "Pseudocode",
    "terraform": "Terraform",
    "nginx": "Nginx",
    "protobuf": "Protobuf",
    "proto": "Protobuf",
}

_CODE_FENCE_LANG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"```[ \t]*(\w+)[ \t]*\n"
)
_DIV_HIGHLIGHT_OPENER: Final[re.Pattern[str]] = re.compile(
    r'<div class="highlight">'
)

_MATH_BLOCK_TOKEN: Final[str] = "XYZMATHBLOCK"
_MATH_INLINE_TOKEN: Final[str] = "XYZMATHLINE"
_MATH_TOKEN_SEP: Final[str] = "XYZEND"
_DETAILS_TOKEN: Final[str] = "XYZDETAILS"

MathKind: TypeAlias = Literal["block", "inline"]
MathPlaceholder: TypeAlias = tuple[MathKind, str]

_TABLE_SEPARATOR_LINE: Final[re.Pattern[str]] = re.compile(
    r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$"
)

_MERMAID_DATA_LANG_BLOCK: Final[re.Pattern[str]] = re.compile(
    r'<div class="highlight"[^>]*\bdata-lang\s*=\s*["\']Mermaid["\'][^>]*>\s*'
    r"<pre[^>]*>\s*(?:<span[^>]*></span>\s*)?"
    r"<code[^>]*>([\s\S]*?)</code>\s*</pre>\s*</div>",
    re.IGNORECASE,
)
_MERMAID_LANGUAGE_CLASS_BLOCK: Final[re.Pattern[str]] = re.compile(
    r'<div class="highlight"[^>]*>\s*<pre[^>]*>\s*(?:<span[^>]*></span>\s*)?'
    r'<code[^>]*class\s*=\s*["\'][^"\']*language-mermaid[^"\']*["\'][^>]*>'
    r"([\s\S]*?)</code>\s*</pre>\s*</div>",
    re.IGNORECASE,
)
_MERMAID_FENCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"```[ \t]*mermaid\s*([\s\S]*?)```",
    re.IGNORECASE,
)

_CALLOUT_LABELS: Final[dict[str, str]] = {
    "important": "important",
    "summary": "summary",
    "conclusion": "summary",
    "tip": "tip",
    "note": "note",
    "danger": "danger",
    "remember": "remember",
    "goal": "note",
    "task": "note",
    "definition": "note",
    "key": "tip",
}

_MIN_STEPWISE_ITEMS: Final[int] = 2
_FIRST_STEP_NUMBER: Final[str] = "1"


def get_pygments_css() -> str:
    """
    Generate CSS rules for syntax-highlighted code blocks.

    This helper asks Pygments to build a stylesheet for HTML code
    snippets using a friendly theme and falls back to an empty string
    if Pygments cannot be used.

    Returns:
        str:
            A CSS stylesheet fragment defining styles for elements under
            the .highlight class, or an empty string when Pygments is
            not installed or an error occurs during CSS generation.
    """
    try:
        from pygments.formatters import HtmlFormatter

        return HtmlFormatter[Any](
            style="friendly", cssclass="highlight"
        ).get_style_defs(".highlight")
    except ImportError:
        return ""
    except Exception as exc:
        print(f"Could not build Pygments CSS: {exc}")
        traceback.print_exc()
        return ""


def add_code_labels(rendered_html: str, source_text: str) -> str:
    """
    Annotate rendered code blocks with human-friendly language labels.

    This helper scans the original Markdown source for fenced code
    languages and injects corresponding data-lang attributes into the
    highlight containers in the rendered HTML.

    Args:
        rendered_html (str):
            HTML produced from Markdown that contains <div
            class="highlight"> wrappers for code blocks.
        source_text (str):
            Original Markdown source text that may include fenced code
            blocks with explicit language identifiers.

    Returns:
        str:
            The rendered HTML with each div.highlight opener replaced by
            a version that includes a data-lang attribute derived from
            the source language or a sensible fallback label.
    """
    lang_matches: list[str] = [
        m.group(1).lower()
        for m in _CODE_FENCE_LANG_PATTERN.finditer(source_text)
    ]

    def replacer(_match: re.Match[str]) -> str:
        """
        Replace a code block language with a human-friendly label.

        This helper pops the first language from the list of detected
        languages and looks up a corresponding human-friendly label in
        the _LANG_MAP dictionary. If no language is found, it falls back
        to a generic "Example" label.

        Args:
            _match (re.Match[str]):
                The match object for the code block language.

        Returns:
            str:
                A string containing the HTML for a <div
                class="highlight"> opener with a data-lang attribute
                set to the human-friendly label or "Example" if no label
                is found.
        """
        lang: str = lang_matches.pop(0) if lang_matches else ""
        label: str = _LANG_MAP.get(
            lang, lang.capitalize() if lang else "Example"
        )
        return f'<div class="highlight" data-lang="{label}">'

    return _DIV_HIGHLIGHT_OPENER.sub(replacer, rendered_html)


def _looks_like_latex(inner: str) -> bool:
    """
    Determine whether a string likely contains LaTeX-style commands.

    This helper inspects the text for backslash-prefixed alphabetic
    sequences that resemble LaTeX control words such as \frac or
    \alpha.

    Args:
        inner (str):
            Candidate string to inspect for LaTeX-style command
            patterns.

    Returns:
        bool:
            True if the text appears to contain LaTeX command sequences,
            or False if no such patterns are detected.
    """
    return bool(re.search(r"\\[A-Za-z]+", inner or ""))


def protect_math(text: str) -> tuple[str, list[MathPlaceholder]]:
    """
    Temporarily replace LaTeX-style math segments with placeholder
    tokens.

    This function scans the text for inline and block math markers,
    swaps recognized LaTeX fragments with opaque tokens, and records
    the original segments for later restoration.

    Args:
        text (str):
            Raw Markdown source that may contain LaTeX-style inline or
            block math expressions.

    Returns:
        tuple[str, list[MathPlaceholder]]:
            A pair containing the transformed text with math replaced by
            placeholders, and a list of captured math segments along
            with their kind ("inline" or "block") in placeholder order.
    """
    placeholders: list[MathPlaceholder] = []

    def repl_block(match: re.Match[str]) -> str:
        """
        Replace a block-level LaTeX math expression with a placeholder
        token.

        This helper extracts the math content from a block-level match
        and checks if it looks like LaTeX. If so, it appends a
        placeholder tuple to the placeholders list and returns a token
        that will be replaced later with the actual math content.

        Args:
            match (re.Match[str]):
                    The match object for the LaTeX block.

        Returns:
            str:
                A string containing the placeholder token for the LaTeX
                block.
        """
        inner: str | Any = match.group(1) if match.lastindex else ""
        if not _looks_like_latex(inner):
            return match.group(0)
        idx: int = len(placeholders)
        placeholders.append(("block", match.group(0)))
        return f"\n\n{_MATH_BLOCK_TOKEN}{idx}{_MATH_TOKEN_SEP}\n\n"

    def repl_inline(match: re.Match[str]) -> str:
        """
        Replace an inline LaTeX math expression with a placeholder
        token.

        This helper extracts the math content from an inline match and
        checks if it looks like LaTeX. If so, it appends a placeholder
        tuple to the placeholders list and returns a token that will be
        replaced later with the actual math content.

        Args:
            match (re.Match[str]):
                    The match object for the LaTeX inline.

        Returns:
            str:
                A string containing the placeholder token for the LaTeX
                inline.
        """
        inner: str | Any = match.group(1) if match.lastindex else ""
        if not _looks_like_latex(inner):
            return match.group(0)
        idx: int = len(placeholders)
        placeholders.append(("inline", match.group(0)))
        return f"{_MATH_INLINE_TOKEN}{idx}{_MATH_TOKEN_SEP}"

    text = re.sub(r"\\\[((?:.|\n)*?)\\\]", repl_block, text)
    text = re.sub(r"\\\(((?:.|\n)*?)\\\)", repl_inline, text)
    return text, placeholders


def _replace_paragraph_wrapped_token(
    rendered_html: str, token: str, replacement: str
) -> str:
    """
    Replace a standalone token paragraph with a richer HTML fragment.

    This helper ensures paragraph-wrapped placeholder tokens are swapped
    with their final HTML representation while also handling any
    remaining bare token occurrences.

    Args:
        rendered_html (str):
            HTML content that may contain placeholder tokens inside
            paragraph tags or in other positions.
        token (str):
            Placeholder token string that should be located and
            substituted in the HTML.
        replacement (str):
            Fully rendered HTML fragment that should replace each
            occurrence of the placeholder token.

    Returns:
        str:
            Updated HTML string where paragraph-wrapped tokens are
            replaced by the supplied fragment and any remaining bare
            tokens are also substituted.
    """
    rendered_html = re.sub(
        rf"<p>\s*{re.escape(token)}\s*</p>",
        lambda _m, repl=replacement: repl,
        rendered_html,
    )
    return rendered_html.replace(token, replacement)


def restore_math(
    rendered_html: str, placeholders: Sequence[MathPlaceholder]
) -> str:
    """
    Restore previously protected LaTeX-style math segments into HTML.

    This function walks the sequence of captured math placeholders and
    re-injects each original fragment into the rendered HTML as either
    an inline or block-level math container.

    Args:
        rendered_html (str):
            HTML text that still contains placeholder tokens
            representing protected math segments.
        placeholders (Sequence[MathPlaceholder]):
            Ordered collection of math placeholders, where each entry
            stores the kind ("block" or "inline") and the original math
            source text.

    Returns:
        str:
            HTML string in which all math placeholder tokens have been
            replaced with escaped inline or block math elements
            suitable for client-side rendering.
    """
    for idx, (kind, raw) in enumerate[MathPlaceholder](placeholders):
        safe: str = html.escape(raw)
        if kind == "block":
            token: str = f"{_MATH_BLOCK_TOKEN}{idx}{_MATH_TOKEN_SEP}"
            replacement: str = f'<div class="math-display">{safe}</div>'
            rendered_html = _replace_paragraph_wrapped_token(
                rendered_html, token, replacement
            )
        else:
            token = f"{_MATH_INLINE_TOKEN}{idx}{_MATH_TOKEN_SEP}"
            rendered_html = rendered_html.replace(
                token, f'<span class="math-inline">{safe}</span>'
            )
    return rendered_html


def _safe_add_code_labels(rendered_html: str, source_text: str) -> str:
    """
    Safely add language labels to code blocks in rendered HTML.

    This wrapper preserves the original HTML if labeling fails for any
    reason.

    The function delegates to add_code_labels to inject human-friendly
    language names but catches any unexpected errors. When an exception
    occurs, it logs diagnostic information and returns the unmodified
    HTML so Markdown rendering remains robust.

    Args:
        rendered_html (str):
            HTML produced from Markdown that may contain code-highlight
            wrappers needing data-lang labels.
        source_text (str):
            Original Markdown source corresponding to the rendered HTML,
            used to infer code block languages.

    Returns:
        str:
            Either the HTML enhanced with code language labels when
            successful, or the original rendered_html if an error
            occurs during labeling.
    """
    try:
        return add_code_labels(rendered_html, source_text)
    except Exception as exc:
        print(f"add_code_labels failed: {exc}")
        traceback.print_exc()
        return rendered_html


def render_markdown_fragment(text: str) -> str:
    """
    Render a small Markdown fragment into enhanced HTML suitable for
    inline display.

    This function focuses on producing feature-rich HTML for snippets
    without handling global concerns like tables of contents.

    The function first normalizes Mermaid diagram fences so they render
    via the client-side engine. It then converts the Markdown to HTML
    with syntax highlighting and applies several post-processing steps,
    including code labeling, task list styling, stepwise formatting,
    callout detection, and semantic heading and container enhancements.

    Args:
        text (str):
            Raw Markdown fragment to be converted into styled HTML,
            which may include code blocks, Mermaid diagrams, task
            lists, and special prose patterns.

    Returns:
        str:
            HTML string representing the rendered fragment, with Mermaid
            blocks preprocessed and structural enhancements applied for
            better presentation in the viewer.
    """
    import markdown

    text = preprocess_mermaid_fences(text)
    rendered_html: str = markdown.markdown(
        text,
        extensions=["extra", "nl2br", "sane_lists", "codehilite"],
        extension_configs={
            "codehilite": {
                "css_class": "highlight",
                "linenums": False,
                "guess_lang": False,
            }
        },
    )
    rendered_html = _safe_add_code_labels(rendered_html, text)
    rendered_html = _safe_transform_mermaid(rendered_html)
    rendered_html = process_task_lists(rendered_html)
    rendered_html = transform_stepwise_paragraphs(rendered_html)
    rendered_html = transform_labeled_callouts(rendered_html)
    return enhance_markdown_document_semantics(rendered_html)


def normalize_markdown_layout(text: str) -> str:
    def is_table_line(line: str) -> bool:
        """
        Normalize Markdown table and heading spacing for consistent
        layout.

        This helper inspects each line to decide whether it should be
        treated as part of a pipe-style table row.

        Args:
            line (str):
                Single line of Markdown text to test for table row
                structure.

        Returns:
            bool:
                True if the line appears to be a non-empty
                pipe-delimited table row (starting with | and
                containing at least one additional pipe), otherwise
                False.
        """
        stripped: str = line.strip()
        return (
            bool(stripped) and stripped.startswith("|") and "|" in stripped[1:]
        )

    def is_heading_or_block_start(line: str) -> bool:
        """
        Determine whether a line starts a new structural Markdown block.

        This helper checks for headings, horizontal rules, HTML
        comments, and blockquote markers to identify boundaries between
        sections.

        Args:
            line (str):
                Single line of Markdown text to test for heading or
                block-start syntax.

        Returns:
            bool:
                True if the trimmed line begins with a heading marker,
                matches a horizontal rule, starts an HTML comment, or
                begins a blockquote, otherwise False.
        """
        stripped: str = line.strip()
        return (
            stripped.startswith("#")
            or stripped in {"---", "***", "___"}
            or stripped.startswith("<!--")
            or stripped.startswith(">")
        )

    lines: list[str] = text.splitlines()
    output: list[str] = []
    for idx, line in enumerate[str](lines):
        prev_line: str = lines[idx - 1] if idx > 0 else ""
        next_line: str = lines[idx + 1] if idx + 1 < len(lines) else ""
        if (
            is_heading_or_block_start(line)
            and is_table_line(line=prev_line)
            and (not output or output[-1] != "")
        ):
            output.append("")
        output.append(line)
        if is_table_line(line) and is_heading_or_block_start(line=next_line):
            output.append("")
        elif (
            _TABLE_SEPARATOR_LINE.match(line)
            and next_line
            and not is_table_line(line=next_line)
        ):
            output.append("")
    return "\n".join(output)


def protect_details(text: str) -> tuple[str, list[str]]:
    """
    Protect Markdown <details> blocks by replacing them with tokens.

    This function extracts details/summary sections so their inner
    Markdown can be rendered independently and later restored as rich
    collapsible HTML.

    The function scans the text for HTML <details> elements with nested
    <summary> content, normalizes and renders the body Markdown, and
    stores the resulting HTML in a placeholder list. It replaces each
    matched details block with a unique token, allowing subsequent
    Markdown processing to ignore the original raw HTML.

    Args:
        text (str):
            Raw Markdown source that may contain HTML <details> blocks
            whose bodies should be rendered separately and reinserted
            later.

    Returns:
        tuple[str, list[str]]:
            A pair where the first element is the Markdown text with
            each details block replaced by a placeholder token, and the
            second element is an ordered list of fully rendered details
            HTML snippets aligned with those tokens.
    """
    placeholders: list[str] = []
    pattern: re.Pattern[str] = re.compile(
        r"<details\b([^>]*)>\s*<summary\b([^>]*)>(.*?)</summary>(.*?)</details>",
        flags=re.IGNORECASE | re.DOTALL,
    )

    def repl(match: re.Match[str]) -> str:
        """
        Normalize Mermaid diagram source text for consistent client
        rendering.

        This function rewrites common Mermaid shorthand and edge cases
        so the resulting graph definitions are more robust across
        themes and browser environments.

        The function first converts legacy flowchart declarations to
        standard graph syntax while preserving indentation. It then
        walks the source to stabilize subgraph titles, dotted edge
        labels, and node labels that contain raw HTML by injecting
        stable identifiers and quoting as needed, producing a sanitized
        Mermaid definition that still reflects the original intent.

        Args:
            text (str):
                Raw Mermaid diagram source, possibly containing
                flowchart prefixes, unquoted HTML labels, or loosely
                formatted edge definitions.

        Returns:
            str:
                A normalized Mermaid source string with canonical graph
                headers, safely quoted HTML labels, and standardized
                edge and subgraph syntax suitable for downstream
                encoding and rendering.
        """
        idx: int = len(placeholders)
        details_attrs = re.sub(
            r'\s+markdown\s*=\s*"[^"]*"',
            "",
            match.group(1),
            flags=re.IGNORECASE,
        )
        summary_attrs: str | Any = match.group(2) or ""
        summary_text: str | Any = html.escape(
            html.unescape(match.group(3).strip())
        )
        body_src: str = normalize_markdown_layout(text=match.group(4).strip())
        body_src, body_math = protect_math(text=body_src)
        body_html: str = render_markdown_fragment(text=body_src)
        body_html = restore_math(
            rendered_html=body_html, placeholders=body_math
        )
        placeholders.append(
            f"<details{details_attrs}><summary{summary_attrs}>{summary_text}</summary>"
            f'<div class="details-body">{body_html}</div></details>'
        )
        return f"\n\n{_DETAILS_TOKEN}{idx}{_MATH_TOKEN_SEP}\n\n"

    return pattern.sub(repl, text), placeholders


def restore_details(rendered_html: str, placeholders: Sequence[str]) -> str:
    """
    Reinsert previously protected details blocks into rendered HTML.

    This function replaces placeholder tokens with their corresponding
    details markup, handling both standalone paragraphs and inline
    token occurrences.

    Args:
        rendered_html (str):
            HTML text that still contains placeholder tokens
            representing protected details sections.
        placeholders (Sequence[str]):
            Ordered collection of fully rendered <details> HTML
            fragments, aligned by index with the placeholder tokens in
            the HTML.

    Returns:
        str:
            HTML string in which all details placeholder tokens have
            been replaced with their rendered <details> blocks,
            preserving document structure and readability.
    """
    for idx, rendered in enumerate[str](placeholders):
        token: str = f"{_DETAILS_TOKEN}{idx}{_MATH_TOKEN_SEP}"
        rendered_html = re.sub(
            rf"<p>\s*{re.escape(token)}\s*</p>",
            lambda _m, repl=rendered: repl,
            rendered_html,
        )
        rendered_html = rendered_html.replace(token, rendered)
    return rendered_html


def process_task_lists(rendered_html: str) -> str:
    """
    Convert Markdown-style task list items into interactive checkbox
    HTML.

    This function scans rendered list items for leading [ ] or [x]
    markers and upgrades them into disabled checkbox elements with a
    task list CSS class.

    Args:
        rendered_html (str):
            HTML produced from Markdown that may contain <li> elements
            whose text starts with [ ] or [x] style task list markers.

    Returns:
        str:
            HTML string where recognized task list items are rewritten
            as <li class="task-list-item"> elements containing disabled
            checkbox inputs that visually represent completion state.
    """
    rendered_html = re.sub(
        r"<li>\s*\[ \]\s*",
        '<li class="task-list-item"><input type="checkbox" disabled> ',
        rendered_html,
    )
    rendered_html = re.sub(
        r"<li>\s*\[x\]\s*",
        '<li class="task-list-item"><input type="checkbox" checked disabled> ',
        rendered_html,
        flags=re.IGNORECASE,
    )
    return rendered_html


def _step_token_number(token: re.Match[str]) -> str:
    """
    Extract the numeric value from a matched step token.

    This helper normalizes different step token formats so subsequent
    logic can treat them as a simple string number.

    Args:
        token (re.Match[str]):
            Regular expression match object capturing a step indicator,
            such as "(1)" or "1.", with the numeric part in group 1 or
            group 2.

    Returns:
        str:
            The extracted numeric portion of the step token as a string,
            or an empty string if no number is present.
    """
    return token.group(1) or token.group(2) or ""


def _strip_html_to_plain(text: str) -> str:
    """
    Strip HTML tags from text and return a plain-text representation.

    This helper is used to derive human-readable content from HTML
    fragments for tasks like label detection or summary extraction.

    Args:
        text (str):
            Input string that may contain HTML tags and entities to be
            removed or decoded.

    Returns:
        str:
            Plain-text version of the input with HTML entities unescaped
            and all tags removed, trimmed of leading and trailing
            whitespace.
    """
    return re.sub(r"<[^>]+>", "", html.unescape(text)).strip()


def _build_stepwise_intro_html(header_plain: str) -> str:
    """
    Build an introductory paragraph for a stepwise instructions block.

    This function extracts an optional prose lead-in from a header
    string and formats it as HTML preceding the ordered list of steps.

    The function looks for a trailing "Algorithm" marker in the header
    and removes it when present, leaving only the descriptive
    introduction. If any non-empty introduction text remains, it is
    HTML-escaped and wrapped in a <p class="stepwise-intro"> element.

    Args:
        header_plain (str):
            Plain-text header content that may contain a descriptive
            lead-in and an optional "Algorithm" marker.

    Returns:
        str:
            An HTML <p> element containing the escaped introduction text
            when available, or an empty string if no meaningful intro
            can be derived.
    """
    algo_marker: Match[str] | None = re.search(
        r"(?iu)\balgorithm\s*[:\-]\s*", header_plain
    )
    if algo_marker:
        intro_text: str = header_plain[: algo_marker.start()].strip()
    else:
        intro_text = header_plain.strip()
    if not intro_text:
        return ""
    return f'<p class="stepwise-intro">{html.escape(intro_text)}</p>'


def _extract_stepwise_items(
    inner: str, tokens: Sequence[re.Match[str]]
) -> list[str]:
    """
    Split a stepwise paragraph into individual step content segments.

    This helper uses precomputed step tokens to slice the inner HTML
    into per-step bodies suitable for wrapping in list items.

    The function iterates over each matched step token and takes the
    text between it and the next token, trimming whitespace and
    collecting only non-empty segments. The final list of step bodies
    can then be rendered as an ordered set of instructions.

    Args:
        inner (str):
            Full inner HTML of the paragraph that contains labeled step
            markers.
        tokens (Sequence[re.Match[str]]):
            Ordered sequence of regular expression matches, each
            identifying the position of a numbered step token within
            inner.

    Returns:
        list[str]:
            List of trimmed HTML fragments, one for each detected step,
            in the same order as the original tokens.
    """
    items: list[str] = []
    for idx, token in enumerate[Match[str]](tokens):
        start: int = token.end()
        end: int = (
            tokens[idx + 1].start() if idx + 1 < len(tokens) else len(inner)
        )
        content: str = inner[start:end].strip()
        if content:
            items.append(content)
    return items


def transform_stepwise_paragraphs(rendered_html: str) -> str:
    """
    Convert paragraphs with numbered steps into structured stepwise
    blocks.

    This function turns qualifying prose instructions into a styled
    container with an optional intro and an ordered list of steps.

    The function searches for paragraphs whose plain-text content begins
    with a stepwise lead-in such as "Step-by-step" or "Algorithm" and
    contains numbered step tokens. When the sequence of tokens forms a
    valid algorithm (starting at the first step and meeting a minimum
    length), the paragraph is replaced with a dedicated stepwise block
    element that includes a title, introductory text, and an ordered
    list representing each step.

    Args:
        rendered_html (str):
            Full HTML document or fragment whose paragraphs may contain
            stepwise instructions written as numbered prose.

    Returns:
        str:
            HTML string in which eligible stepwise paragraphs have been
            transformed into semantic stepwise blocks, while
            non-matching paragraphs remain unchanged.
    """
    paragraph_re: Pattern[str] = re.compile(
        r"<p>([\s\S]*?)</p>", re.IGNORECASE
    )
    step_token_re: Pattern[str] = re.compile(r"(?:\((\d+)\)|(\d+)[\.\)])\s+")
    lead_re: Pattern[str] = re.compile(
        r"(?iu)^(step-by-step|algorithm|walkthrough)\s*[:\-]"
    )

    def repl(match: re.Match[str]) -> str:
        """
        Replace a stepwise paragraph with a structured stepwise block.

        This helper detects and transforms qualifying prose instructions
        written as numbered steps into a styled container with an
        optional intro and an ordered list of steps.

        Args:
            match (re.Match[str]):
                    Regular expression match object capturing the inner
                    HTML of a stepwise paragraph, which may contain
                    leading prose and numbered step indicators.

        Returns:
            str:
                HTML string where the original paragraph is replaced by
                a dedicated stepwise block element containing a title,
                introductory text, and an ordered list of steps, or the
                original match if the paragraph does not qualify for
                transformation.
        """
        inner: str | Any = match.group(1).strip()
        plain: str = _strip_html_to_plain(text=inner)
        lead: Match[str] | None = lead_re.match(plain)
        if not lead:
            return match.group(0)
        tokens: list[Match[str]] = list[Match[str]](
            step_token_re.finditer(inner)
        )
        if (
            len(tokens) < _MIN_STEPWISE_ITEMS
            or _step_token_number(token=tokens[0]) != _FIRST_STEP_NUMBER
        ):
            return match.group(0)
        header_src: str | Any = inner[: tokens[0].start()].strip()
        header_plain: str = _strip_html_to_plain(text=header_src)
        header_plain = re.sub(
            r"(?iu)^step-by-step\s*[:\-]?\s*", "", header_plain
        ).strip()
        intro_html: str = _build_stepwise_intro_html(header_plain)
        items: list[str] = _extract_stepwise_items(inner=inner, tokens=tokens)
        if len(items) < _MIN_STEPWISE_ITEMS:
            return match.group(0)
        steps_html: str = "".join(f"<li>{item}</li>" for item in items)
        return (
            '<div class="stepwise-block">'
            f'<div class="stepwise-title">{html.escape(lead.group(1).capitalize())}</div>'
            f"{intro_html}"
            f"<ol>{steps_html}</ol>"
            "</div>"
        )

    return paragraph_re.sub(repl, rendered_html)


def transform_labeled_callouts(rendered_html: str) -> str:
    """
    Detect and convert labeled callout paragraphs into styled callout
    blocks.

    This function upgrades simple labeled prose into semantic HTML
    containers that highlight important information.

    The function looks for paragraphs that begin with a label followed
    by a colon or dash, optionally wrapped in a <strong> tag, and maps
    known labels (such as "Note" or "Warning") to callout kinds. When a
    supported label and body are found without nested block elements,
    the paragraph is replaced by a callout wrapper that separates the
    title and body content while preserving the original text.

    Args:
        rendered_html (str):
            HTML document or fragment whose top-level paragraphs may
            contain simple labeled callout text like "Note: ..." or
            "<strong>Tip</strong> - ...".

    Returns:
        str:
            HTML string in which recognized labeled callout paragraphs
            have been transformed into <div class="callout
            callout-..."> structures, and all other paragraphs are left
            unchanged.
    """
    paragraph_re: Pattern[str] = re.compile(
        r"<p>([\s\S]*?)</p>", re.IGNORECASE
    )
    plain_label_re: Pattern[str] = re.compile(
        r"^\s*([A-Za-z]+)\s*[:\-]\s*(.+)$", re.DOTALL
    )
    strong_label_re: Pattern[str] = re.compile(
        r"^\s*<strong>\s*([A-Za-z]+)\s*[:\-]?\s*</strong>\s*(.+)$",
        re.IGNORECASE | re.DOTALL,
    )

    def repl(match: re.Match[str]) -> str:
        """
        Replace a labeled callout paragraph with a styled callout block.

        This helper detects and transforms qualifying labeled prose into
        semantic HTML containers that highlight important information.

        Args:
            match (re.Match[str]):
                Regular expression match object capturing the inner HTML
                of a labeled callout paragraph, which may contain a
                label and body content wrapped in optional HTML tags.

        Returns:
            str:
                HTML string where the original paragraph is replaced by
                a <div class="callout callout-..."> structure
                containing a title and body content, or the original
                match if the paragraph does not qualify for
                transformation.
        """
        inner: str | Any = match.group(1).strip()
        parsed: Match[str] | None = strong_label_re.match(
            inner
        ) or plain_label_re.match(inner)
        if not parsed:
            return match.group(0)
        label_raw: str | Any = (parsed.group(1) or "").strip()
        body: str | Any = (parsed.group(2) or "").strip()
        if not label_raw or not body:
            return match.group(0)
        if "<p" in body.lower() or "<div" in body.lower():
            return match.group(0)
        kind: str | None = _CALLOUT_LABELS.get(label_raw.lower())
        if not kind:
            return match.group(0)
        return (
            f'<div class="callout callout-{kind}">'
            f'<div class="callout-title">{html.escape(label_raw)}</div>'
            f'<div class="callout-body">{body}</div>'
            "</div>"
        )

    return paragraph_re.sub(repl, rendered_html)


def _inject_heading_level_classes(html: str) -> str:
    """
    Add semantic heading-level classes to HTML heading elements.

    This helper decorates raw <h1>-<h6> tags with consistent CSS classes
    that reflect their level while preserving any existing classes.

    Args:
        html (str):
            HTML document or fragment whose heading tags should be
            annotated with md-heading and level-specific classes.

    Returns:
        str:
            HTML string where each unclassified <h1>-<h6> element has
            been augmented with md-heading and a corresponding md-hN
            class, leaving headings that already declare a class
            attribute unchanged.
    """

    def repl(match: re.Match[str]) -> str:
        """
        Replace a heading tag with a semantically enhanced version.

        This helper adds consistent CSS classes to raw <h1>-<h6> tags
        based on their level, preserving any existing classes.

        Args:
            match (re.Match[str]):
                Regular expression match object capturing the
                heading tag, which may include a level number (1-6)
                and optional attributes.

        Returns:
            str:
                HTML string where the original heading tag has been
                augmented with md-heading and a corresponding md-hN
                class, leaving headings that already declare a class
                attribute unchanged.
        """
        level: str | Any = match.group(1)
        rest: str | Any = match.group(2) or ""
        if re.search(r"\bclass\s*=", rest, re.IGNORECASE):
            return match.group(0)
        cls: str = f' class="md-heading md-h{level}"'
        return f"<h{level}{cls}{rest}>" if rest.strip() else f"<h{level}{cls}>"

    return re.sub(r"<h([1-6])(\s[^>]*)?>", repl, html, flags=re.IGNORECASE)


def _mark_first_blockquote_lede(html: str) -> str:
    """
    Mark the first blockquote as a lead paragraph in a document.

    This helper identifies the first <blockquote> element in a document
    and adds a class attribute to it, ensuring it stands out as the
    main lead paragraph.

    Args:
        html (str):
            HTML document or fragment that may contain multiple
            blockquote elements.

    Returns:
        str:
            HTML string where the first <blockquote> element has been
            augmented with class="doc-lede", leaving all other
            blockquote elements unchanged.
    """
    replaced = False

    def repl(match: re.Match[str]) -> str:
        """
        Replace a blockquote tag with a semantically enhanced version.

        This helper adds a class attribute to the first <blockquote>
        element in a document, ensuring it stands out as the main lead
        paragraph.

        Args:
            match (re.Match[str]):
                Regular expression match object capturing the blockquote
                tag, which may include optional attributes.

        Returns:
            str:
                HTML string where the original blockquote tag has been
                augmented with class="doc-lede", leaving all other
                blockquote elements unchanged.
        """
        nonlocal replaced
        if replaced:
            return match.group(0)
        attrs: str | Any = match.group(1) or ""
        if re.search(r"\bclass\s*=", attrs, re.IGNORECASE):
            return match.group(0)
        replaced = True
        return f'<blockquote class="doc-lede"{attrs}>'

    return re.sub(
        r"<blockquote(\s[^>]*)?>", repl, html, count=1, flags=re.IGNORECASE
    )


def _wrap_tables_scroll(html: str) -> str:
    """
    Wrap tables in a scrollable container for better readability.

    This helper inspects HTML tables and wraps them in a <div
    class="table-scroll"> element when the table-scroll CSS class is
    not already present. This enhances readability by ensuring tables
    are properly scrollable on small screens.

    Args:
        html (str):
            HTML document or fragment that may contain tables.

    Returns:
        str:
            HTML string where tables have been wrapped in a <div
            class="table-scroll"> element, or the original HTML if no
            tables or the class is already present.
    """

    def repl(match: re.Match[str]) -> str:
        """
        Replace a table tag with a semantically enhanced version.

        This helper wraps tables in a <div class="table-scroll"> element
        when the table-scroll CSS class is not already present. This
        enhances readability by ensuring tables are properly scrollable
        on small screens.

        Args:
            match (re.Match[str]):
                Regular expression match object capturing the table tag,
                which may include optional attributes.

        Returns:
            str:
                HTML string where the original table tag has been
                wrapped in a <div class="table-scroll"> element, or the
                original HTML if no tables or the class is already
                present.
        """
        inner: str | Any = match.group(1)
        if "table-scroll" in inner.lower():
            return inner
        return f'<div class="table-scroll">{inner}</div>'

    return re.sub(
        r"(<table\b[\s\S]*?</table>)",
        repl,
        html,
        flags=re.IGNORECASE,
    )


def _add_details_collapsible_class(html: str) -> str:
    """
    Add a collapsible class to details blocks for enhanced semantic
    presentation.

    This helper inspects HTML <details> elements and adds a class
    attribute when the md-collapsible CSS class is not already present.
    This enhances the document's structural hierarchy by marking
    collapsible sections as semantically distinct from other content.

    Args:
        html (str):
            HTML document or fragment that may contain <details>
            elements.

    Returns:
        str:
            HTML string where each <details> element has been augmented
            with class="md-collapsible", leaving elements that already
            declare a class attribute unchanged.
    """

    def repl(match: re.Match[str]) -> str:
        """
        Replace a details tag with a semantically enhanced version.

        This helper adds a class attribute to the <details> element when
        the md-collapsible CSS class is not already present. This
        enhances the document's structural hierarchy by marking
        collapsible sections as semantically distinct from other
        content.

        Args:
            match (re.Match[str]):
                Regular expression match object capturing the details
                tag, which may include optional attributes.

        Returns:
            str:
                HTML string where the original details tag has been
                augmented with class="md-collapsible", leaving elements
                that already declare a class attribute unchanged.
        """
        attrs: str | Any = match.group(1) or ""
        if re.search(r"\bclass\s*=", attrs, re.IGNORECASE):
            return match.group(0)
        return f'<details class="md-collapsible"{attrs}>'

    return re.sub(r"<details(\s[^>]*)?>", repl, html, flags=re.IGNORECASE)


def enhance_markdown_document_semantics(html: str) -> str:
    """
    Enhance rendered Markdown HTML with additional semantic structure.

    This function decorates headings, blockquotes, tables, and details
    elements so the final document is easier to style and visually
    scan.

    The function first adds consistent heading-level classes to all
    heading tags, then marks the first blockquote as a document lead
    paragraph. It also wraps tables in a scrollable container for better
    responsiveness and annotates details blocks as collapsible sections,
    producing HTML that more clearly communicates document hierarchy and
    interactive regions.

    Args:
        html (str):
            HTML document or fragment generated from Markdown that
            should be augmented with semantic classes and wrappers.

    Returns:
        str:
            HTML string in which headings, the first blockquote, tables,
            and details elements have been updated with additional
            classes and containers to support richer presentation and
            layout.
    """
    html = _inject_heading_level_classes(html)
    html = _mark_first_blockquote_lede(html)
    html = _wrap_tables_scroll(html)
    html = _add_details_collapsible_class(html)
    return html


def normalize_mermaid_source(text: str) -> str:
    """
    Normalize Mermaid diagram source text for consistent client
    rendering.

    This function rewrites common Mermaid shorthand and edge cases so
    the resulting graph definitions are more robust across themes and
    browser environments.

    The function first converts legacy flowchart declarations to
    standard graph syntax while preserving indentation. It then
    stabilizes quoted subgraph headings, dotted edge labels, and node
    labels that contain raw HTML by injecting stable identifiers and
    quoting content as needed, producing a sanitized Mermaid definition
    that still reflects the original author intent.

    Args:
        text (str):
            Raw Mermaid diagram source, possibly containing flowchart
            prefixes, unquoted HTML labels, or loosely formatted edge
            definitions.

    Returns:
        str:
            A normalized Mermaid source string with canonical graph
            headers, safely quoted HTML labels, and standardized edge
            and subgraph syntax suitable for downstream encoding and
            rendering.
    """
    lines: list[str] = text.splitlines()
    for idx, line in enumerate[str](lines):
        stripped: str = line.lstrip()
        if not stripped:
            continue
        if stripped.lower().startswith("flowchart "):
            prefix_len: int = len(line) - len(stripped)
            indent: str = line[:prefix_len]
            rest: str = stripped[len("flowchart ") :]
            lines[idx] = f"{indent}graph {rest}"
        break
    normalized = "\n".join(lines)

    def fix_quoted_subgraph(m: re.Match[str]) -> str:
        """
        Fix a quoted subgraph title by injecting a stable identifier.

        This helper extracts the indentation and title from a quoted
        subgraph declaration and returns a sanitized version that
        includes a stable identifier to prevent duplicate rendering.

        Args:
            m (re.Match[str]):
                Regular expression match object capturing the subgraph
                declaration, which includes indentation and a quoted
                title.

        Returns:
            str:
                A string containing the fixed subgraph declaration with
                a stable identifier injected into the title.
        """
        indent: str | Any = m.group(1)
        title: str | Any = m.group(2).strip()
        return f'{indent}subgraph sg_auto_{abs(hash(title)) & 0xFFFF:x}["{title}"]'

    normalized = re.sub(
        r"^(\s*)subgraph\s+\"([^\n\"]+)\"\s*$",
        fix_quoted_subgraph,
        normalized,
        flags=re.MULTILINE,
    )

    def fix_dotted_edge_label(m: re.Match[str]) -> str:
        """
        Fix a dotted edge label by injecting a stable identifier.

        This helper extracts the indentation, left node, label, and
        right node from a dotted edge declaration and returns a
        sanitized version that includes a stable identifier to prevent
        duplicate rendering.

        Args:
            m (re.Match[str]):
                Regular expression match object capturing the dotted
                edge declaration, which includes indentation, left
                node, label, and right node.

        Returns:
            str:
                A string containing the fixed edge declaration with a
                stable identifier injected into the label.
        """
        indent, left, label, right = (
            m.group(1),
            m.group(2),
            m.group(3),
            m.group(4),
        )
        lbl: str | Any = label.strip().replace('"', '\\"')
        return f'{indent}{left} -.->|"{lbl}"| {right}'

    normalized = re.sub(
        r"^(\s*)([A-Za-z_][\w]*)\s+-\.\s+(.+?)\s+\.\->\s+([A-Za-z_][\w]*)\s*$",
        fix_dotted_edge_label,
        normalized,
        flags=re.MULTILINE,
    )

    def quote_square_brackets_with_html(m: re.Match[str]) -> str:
        """
        Quote Mermaid node labels that embed raw HTML inside square
        brackets.

        This helper detects node definitions whose labels contain
        angle-bracket HTML and wraps those labels in quotes while
        escaping special characters.

        Args:
            m (re.Match[str]):
                Regular expression match object capturing a Mermaid node
                identifier and the raw bracketed label text that may
                contain HTML markup.

        Returns:
            str:
                Either the original matched text when quoting is
                unnecessary, or a rewritten node definition where the
                label has been safely quoted and escaped to avoid
                Mermaid parsing issues.
        """
        nid, inner = m.group(1), m.group(2)
        st: str | Any = inner.strip()
        if (st.startswith('"') and st.endswith('"')) or (
            st.startswith("'") and st.endswith("'")
        ):
            return m.group(0)
        if "<" not in inner and ">" not in inner:
            return m.group(0)
        esc: str | Any = inner.replace("\\", "\\\\").replace('"', '\\"')
        return f'{nid}["{esc}"]'

    normalized: str = re.sub(
        r"\b([A-Za-z_][\w]*)\[([^\]\n]+)\]",
        quote_square_brackets_with_html,
        normalized,
    )
    return normalized


def _mermaid_code_inner_to_source(raw: str) -> str:
    """
    Extract Mermaid diagram source from a syntax-highlighted HTML block.

    This helper reverses basic HTML escaping and strips span wrappers so
    the underlying Mermaid code can be reused for encoding or
    re-rendering.

    Args:
        raw (str):
            Raw inner HTML of a highlighted Mermaid code block,
            potentially containing escaped characters and <span> tags
            from a highlighter.

    Returns:
        str:
            Clean Mermaid source string with HTML entities unescaped and
            <span> markup removed, suitable for normalization and
            downstream processing.
    """
    inner: str = html.unescape(raw)
    return re.sub(r"</?span[^>]*>", "", inner)


def transform_mermaid(rendered_html: str) -> str:
    """
    Transform Mermaid diagram blocks in rendered HTML into client-side
    pre-rendered blocks.

    This function scans rendered HTML for <pre class="mermaid"> wrappers
    and extracts the Mermaid source code. It then encodes the source in
    base64 for safe client-side rendering and wraps it in a <pre> tag
    with a data-mermaid-src-b64 attribute.

    Args:
        rendered_html (str):
            HTML produced from Markdown that may contain <pre
            class="mermaid"> wrappers wrapping Mermaid diagram source
            code.

    Returns:
        str:
            HTML string in which recognized Mermaid diagram blocks have
            been replaced by <pre> tags with base64-encoded source
            content, suitable for direct client-side rendering.
    """

    def repl(match: re.Match[str]) -> str:
        """
        Replace a Mermaid diagram block with a client-side pre-rendered
        block.

        This helper extracts the Mermaid source code from a <pre
        class="mermaid"> wrapper and encodes it in base64 for safe
        client-side rendering. It then wraps the encoded source in a
        <pre> tag with a data-mermaid-src-b64 attribute.

        Args:
            match (re.Match[str]):
                Regular expression match object capturing the inner HTML
                of a Mermaid diagram block, which may contain escaped
                characters and <span> tags from a highlighter.

        Returns:
            str:
                HTML string where the original <pre class="mermaid">
                wrapper has been replaced by a <pre> tag with
                base64-encoded source content, suitable for direct
                client-side rendering.
        """
        src: str = _mermaid_code_inner_to_source(raw=match.group(1))
        b64: str = base64.b64encode(src.encode(encoding="utf-8")).decode(
            encoding="ascii"
        )
        return f'<pre class="mermaid" data-mermaid-src-b64="{b64}">\n{html.escape(src)}\n</pre>'

    for pat in (_MERMAID_DATA_LANG_BLOCK, _MERMAID_LANGUAGE_CLASS_BLOCK):
        rendered_html = pat.sub(repl, rendered_html)
    return rendered_html


def _safe_transform_mermaid(rendered_html: str) -> str:
    """
    Safely transform Mermaid diagram blocks in rendered HTML into
    client-side pre-rendered blocks.

    This function attempts to transform Mermaid diagram blocks in
    rendered HTML into client-side pre-rendered blocks. If an exception
    occurs, it logs the error and returns the original HTML.

    Args:
        rendered_html (str):
            HTML produced from Markdown that may contain <pre
            class="mermaid"> wrappers wrapping Mermaid diagram source
            code.

    Returns:
        str:
            HTML string in which recognized Mermaid diagram blocks have
            been replaced by <pre> tags with base64-encoded source
            content, suitable for direct client-side rendering.
    """
    try:
        return transform_mermaid(rendered_html)
    except Exception as exc:
        print(f"transform_mermaid failed: {exc}")
        traceback.print_exc()
        return rendered_html


def preprocess_mermaid_fences(text: str) -> str:
    """
    Preprocess Mermaid diagram fences in Markdown source text.

    This function scans the input text for fenced Mermaid diagram blocks
    and replaces them with <pre class="mermaid"> wrappers that contain
    base64-encoded Mermaid source code.

    Args:
        text (str):
            Raw Markdown source that may contain fenced Mermaid diagram
            blocks.

    Returns:
        str:
            Markdown source text with Mermaid diagram fences replaced by
            <pre class="mermaid"> wrappers containing base64-encoded
            source code.
    """

    def repl(match: re.Match[str]) -> str:
        """
        Replace a Mermaid diagram fence with a client-side pre-rendered
        block.

        This helper extracts the Mermaid source code from a fenced
        Mermaid diagram block and encodes it in base64 for safe
        client-side rendering. It then wraps the encoded source in a
        <pre> tag with a data-mermaid-src-b64 attribute.

        Args:
            match (re.Match[str]):
                Regular expression match object capturing the inner text
                of a Mermaid diagram fence, which may contain escaped
                characters and <span> tags from a highlighter.

        Returns:
            str:
                HTML string where the original fenced Mermaid diagram
                block has been replaced by a <pre> tag with
                base64-encoded source content, suitable for direct
                client-side rendering.
        """
        inner: str = normalize_mermaid_source(match.group(1).strip("\n\r"))
        b64: str = base64.b64encode(inner.encode("utf-8")).decode("ascii")
        return (
            f'\n<pre class="mermaid" data-mermaid-src-b64="{b64}">\n'
            f"{html.escape(inner)}\n</pre>\n"
        )

    return _MERMAID_FENCE_PATTERN.sub(repl, text)


def _md_convert(
    text: str,
    extensions: list[str],
    ext_config: Mapping[str, Any],
    with_toc: bool,
) -> tuple[str, str]:
    """
    Convert Markdown text into HTML with optional table of contents.

    This function orchestrates the Markdown conversion process, handling
    extension configuration, table of contents generation, and
    post-processing steps to produce a fully rendered HTML document.

    Args:
        text (str):
            Raw Markdown source to be converted into HTML.
        extensions (list[str]):
            List of Markdown extension names to enable during
            conversion.
        ext_config (Mapping[str, Any]):
            Dictionary of extension-specific configuration settings.
        with_toc (bool):
            Flag indicating whether a table of contents should be
            generated and returned alongside the HTML output.

    Returns:
        tuple[str, str]:
            A pair where the first element is the fully rendered HTML
            document, and the second element is the corresponding table
            of contents HTML (or an empty string if disabled or not
            available).
    """
    import markdown

    md: Markdown = markdown.Markdown(
        extensions=extensions, extension_configs=dict(ext_config)
    )
    rendered_html: str = md.convert(text)
    toc: Any | str = getattr(md, "toc", "") if with_toc else ""
    return rendered_html, toc


def md_to_html(text: str, with_toc: bool = True) -> tuple[str, str]:
    """
    Convert Markdown text into HTML with optional table of contents.

    This function orchestrates the Markdown conversion process, handling
    extension configuration, table of contents generation, and
    post-processing steps to produce a fully rendered HTML document.

    Args:
        text (str):
            Raw Markdown source to be converted into HTML.
        with_toc (bool):
            Flag indicating whether a table of contents should be
            generated and returned alongside the HTML output.

    Returns:
        tuple[str, str]:
            A pair where the first element is the fully rendered HTML
            document, and the second element is the corresponding table
            of contents HTML (or an empty string if disabled or not
            available).
    """
    text = preprocess_mermaid_fences(text)
    text, details = protect_details(text)
    text = normalize_markdown_layout(text)
    text, math_tokens = protect_math(text)
    cfg_full: dict[str, Any] = {
        "codehilite": {
            "css_class": "highlight",
            "linenums": False,
            "guess_lang": False,
        },
        "toc": {"permalink": False, "title": ""},
    }
    cfg_toc: dict[str, Any] = {"toc": {"permalink": False, "title": ""}}

    def post(rendered_html: str, source: str) -> str:
        """
        Post-process the rendered HTML to add code labels, restore math,
        details, task lists, stepwise paragraphs, labeled callouts, and
        enhance semantic structure.

        This helper applies a series of transformations to the rendered
        HTML to improve its readability and accessibility.

        Args:
            rendered_html (str):
                The HTML to be post-processed.
            source (str):
                The original Markdown source text that was used to
                generate the HTML.

        Returns:
            str:
                The post-processed HTML.
        """
        rendered_html = _safe_add_code_labels(
            rendered_html, source_text=source
        )
        rendered_html = restore_math(rendered_html, placeholders=math_tokens)
        rendered_html = restore_details(rendered_html, placeholders=details)
        rendered_html = process_task_lists(rendered_html=rendered_html)
        rendered_html = transform_stepwise_paragraphs(
            rendered_html=rendered_html
        )
        rendered_html = transform_labeled_callouts(rendered_html=rendered_html)
        rendered_html = enhance_markdown_document_semantics(html=rendered_html)
        return _safe_transform_mermaid(rendered_html=rendered_html)

    extension_sets: tuple[tuple[list[str], dict[str, Any]], ...] = (
        (["extra", "nl2br", "sane_lists", "codehilite", "toc"], cfg_full),
        (["extra", "nl2br", "sane_lists", "toc"], cfg_toc),
        (["extra", "toc"], cfg_toc),
        (["extra"], {}),
    )
    for ext_set, config in extension_sets:
        try:
            rendered_html, toc = _md_convert(
                text=text,
                extensions=ext_set,
                ext_config=config,
                with_toc=with_toc,
            )
            return post(rendered_html=rendered_html, source=text), toc
        except Exception as exc:
            print(
                f"Markdown convert with extensions {ext_set!r} failed, trying next: {exc}"
            )
            continue
    try:
        import markdown

        rendered_html = markdown.markdown(text, extensions=["extra"])
        return post(rendered_html, text), ""
    except Exception as exc:
        print(f"Markdown rendering failed, showing escaped source: {exc}")
        traceback.print_exc()
        escaped: str = html.escape(text).replace("\n", "<br>\n")
        return (
            f"<pre style='white-space:pre-wrap;'>{escaped}</pre>"
            f"<p><em>Markdown rendering error, raw text is shown.</em></p>",
            "",
        )


MATHJAX_SCRIPT = r"""<script>
MathJax = {
  tex: {
    inlineMath: [["\\(", "\\)"]],
    displayMath: [],
    processEscapes: false
  },
  options: {
    skipHtmlTags: ["script", "noscript", "style", "textarea", "pre", "code"]
  }
};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js" id="MathJax-script" async></script>"""
