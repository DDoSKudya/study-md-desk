"""
The module implements a text-to-speech preprocessing and segmentation
pipeline for turning Markdown or plain text into speech-friendly
chunks.

It focuses on normalizing text, handling Markdown and code blocks, and
inserting symbolic pause tokens to guide TTS prosody.

It defines many constants and regex patterns for sentence splitting,
dash handling, Markdown markers, Latin letter names, operator
expansions, and symbolic pause tokens.

It uses these constants to transform symbols, skip certain headings or
sections, and identify phrase and sentence boundaries.

Helper functions like _normalize_text_before_split,
_paragraph_strings_from_text, _merge_paragraph_lines_to_par_text, and
_sentences_in_paragraph progressively normalize text, group it into
paragraphs, and split it into sentences.

_phrase_tokens_with_dashes and _append_chunks_for_speech_part further
break sentences into smaller phrase tokens and map punctuation and
operators to pause markers and content tokens.

Markdown-related helpers (_MdScan, _md_scan_fence_boundary,
_md_line_is_inside_skipped_details,
_md_line_handled_by_heading_or_skip, _md_append_line_to_out_lines,
_md_collect_out_lines, _md_strip_to_plain_text) scan Markdown
line-by-line, skip hidden or example sections, extract visible content,
and normalize inline and fenced code.

They remove most Markdown and HTML-like structure while preserving text
intended to be spoken.

The _RegexRulesCache dataclass and _regex_pairs_from_rules_payload
implement a small JSON-based regex-rules system for configurable text
replacements.

_default_split_pauses and the _TtsSplitPauses dataclass define and
bundle symbolic pause tokens (paragraph, short, comma, semicolon,
colon, dot, dash) used throughout the splitting logic.

The TtsTextPipeline class orchestrates this behavior by loading regex
rules from disk, applying them, spelling Latin letters and
abbreviations using Russian names, and exposing high-level methods.

Its normalize_code_text cleans and expands code snippets into readable
phrases, normalize_speech_text normalizes prose and HTML/Markdown
remnants, extract_text_from_markdown converts a Markdown document into
normalized speech text, and split_for_tts turns normalized text into an
ordered list of speech and pause tokens.

Within a broader system, this module sits between content (Markdown or
text) and the TTS engine, ensuring input is cleaned, structured, and
annotated with pauses so synthesized speech sounds natural and
understandable.
"""

from __future__ import annotations

import json
import re
from os import stat_result
from re import Match

import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Final, TypeAlias

NormalizeCodeTextFn: TypeAlias = Callable[[str], str]

_MIN_LINE_LEN_FOR_SENTENCE_PERIOD: Final[int] = 2
_REGEX_RULE_PAIR_ARITY: Final[int] = 2

_EM_DASH: Final[str] = "\u2014"
_DASH_SPLIT_PATTERN: Final[str] = r"\s+[\u2014\u2013]\s+"

_MD_HORIZONTAL_RULE_MARKERS: Final[frozenset[str]] = frozenset[str](
    ("---", "***")
)
_PHRASE_CLAUSE_SEPARATORS: Final[frozenset[str]] = frozenset[str](
    (",", ";", ":")
)
_SPEECH_CHUNK_PUNCT_TOKENS: Final[frozenset[str]] = frozenset[str](
    (",", ";", ":", _EM_DASH)
)
_LINE_ENDS_ALLOWING_NO_PERIOD: Final[frozenset[str]] = frozenset[str](
    (".", "!", "?", "…", ":", ";")
)

_EXAMPLE_LINE_PREFIXES: Final[tuple[str, ...]] = (
    "example",
    "mini-example",
    "request walkthrough",
    "how it works step by step",
    "suppose there are tables",
    "assume a table",
)

_TTS_SKIP_HEADING_MARKERS: Final[tuple[str, ...]] = (
    "self-check questions",
    "check yourself",
    "example",
    "examples",
    "mini-example",
    "request walkthrough",
)

_LATIN_LETTER_NAMES_RU: Final[dict[str, str]] = {
    "A": "ay",
    "B": "bee",
    "C": "see",
    "D": "dee",
    "E": "ee",
    "F": "ef",
    "G": "gee",
    "H": "aitch",
    "I": "eye",
    "J": "jay",
    "K": "kay",
    "L": "el",
    "M": "em",
    "N": "en",
    "O": "oh",
    "P": "pee",
    "Q": "cue",
    "R": "ar",
    "S": "ess",
    "T": "tee",
    "U": "you",
    "V": "vee",
    "W": "double u",
    "X": "ex",
    "Y": "why",
    "Z": "zed",
}

_LATIN_SPELL_WHITELIST: Final[frozenset[str]] = frozenset(
    {
        "api",
        "http",
        "https",
        "url",
        "ui",
        "sql",
        "jwt",
        "wal",
        "ttl",
        "cpu",
        "ram",
        "ssd",
        "lsm",
        "cdc",
        "rpo",
        "rto",
        "p99",
        "md",
        "id",
    }
)

_CODE_OPERATOR_REPLACEMENTS: Final[tuple[tuple[str, str], ...]] = (
    (r"===", " strictly equals "),
    (r"!==", " strictly not equals "),
    (r"==", " equals equals "),
    (r"!=", " not equals "),
    (r"<=", " less than or equal "),
    (r">=", " greater than or equal "),
    (r"->", " arrow "),
    (r"=>", " arrow "),
    (r"::", " double colon "),
    (r"&&", " and "),
    (r"\|\|", " or "),
    (r"\+\+", " plus plus "),
    (r"--", " minus minus "),
    (r"\+=", " plus equals "),
    (r"-=", " minus equals "),
    (r"\*=", " multiply equals "),
    (r"/=", " divide equals "),
    (r"%=", " modulo equals "),
    (r"=", " equals "),
    (r"\+", " plus "),
    (r"-", " minus "),
    (r"\*", " multiply "),
    (r"/", " divide "),
    (r"%", " modulo "),
    (r"\.", " dot "),
    (r",", " comma "),
    (r":", " colon "),
    (r";", " semicolon "),
    (r"\(", " open parenthesis "),
    (r"\)", " close parenthesis "),
    (r"\[", " open square bracket "),
    (r"\]", " close square bracket "),
    (r"\{", " open curly bracket "),
    (r"\}", " close curly bracket "),
    (r"#", " hash "),
    (r"@", " at "),
    (r"_", " underscore "),
)

_CODE_BLOCK_FENCE_LABEL: Final[str] = "Code example."
_CODE_BLOCK_LABEL_PLACEHOLDER: Final[str] = "\ufdd0\ufdd1\ufdd2\ufdd3"

_SPEECH_SYMBOL_REPLACEMENTS: Final[tuple[tuple[str, str], ...]] = (
    (r"\s*<->\s*", " connected to "),
    (r"\s*->\s*", " leads to "),
    (r"\s*=>\s*", " therefore "),
    (r"\s*<=\s*", " less or equal "),
    (r"\s*>=\s*", " greater or equal "),
    (r"\s*!=\s*", " not equal "),
    (r"\s*==\s*", " equals "),
    (r"\s*/\s*", " and "),
    (r"(?<=\d)\s*-\s*(?=\d)", " dash "),
    (r"\s*=\s*", " equals "),
    (r"\s*\+\s*", " plus "),
    (r"\s*\*\s*", " multiplied by "),
    (r"\s*%\s*", " percent "),
    (r"\s*&\s*", " and "),
    (r"\s*№\s*", " number "),
)


@dataclass
class _RegexRulesCache:
    """
    Caches compiled regular expression replacement rules for text
    normalization.

    This cache tracks file metadata so rules can be reloaded only when
    the source configuration changes.

    Attributes:
        mtime (float | None):
            Last modification time of the rules file that produced the
            cached rules, or None if no rules have been loaded.
        size (int | None):
            Size of the rules file in bytes corresponding to the cached
            rules, or None if no rules have been loaded.
        rules (list[tuple[str, str]]):
            List of regular expression pattern and replacement string
            pairs used to transform text during normalization.
    """

    mtime: float | None = None
    size: int | None = None
    rules: list[tuple[str, str]] = field(default_factory=list)


def _markdown_heading_level(line: str) -> int | None:
    """
    Determine the Markdown heading level for a line of text.

    This helper inspects leading hash characters and reports the heading
    depth or None when the line is not a heading.

    Args:
        line (str):
            Line of text that may contain a Markdown heading marker.

    Returns:
        int | None:
            The heading level from 1 to 6 when the line starts with hash
            characters followed by space and text, or None if the line
            does not represent a Markdown heading.
    """
    match: Match[str] | None = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
    return None if match is None else len(match.group(1))


def _looks_like_heading_to_skip(text: str) -> bool:
    """
    Identify whether a text line should be treated as a skippable
    heading.

    This helper checks the content against predefined heading markers
    used to omit certain sections from TTS processing.

    Args:
        text (str):
            Line of text that may represent a heading to skip, which
            will be normalized to lowercase before matching.

    Returns:
        bool:
            True if the text contains any of the configured skip-heading
            markers, otherwise False.
    """
    lowered: str = (text or "").strip().lower()
    return any(marker in lowered for marker in _TTS_SKIP_HEADING_MARKERS)


def _looks_like_example_line(text: str) -> bool:
    """
    Check whether a text line looks like an example-style heading.

    This helper normalizes the text and tests it against configured
    example prefixes that mark content to be skipped or treated
    specially.

    Args:
        text (str):
            Line of text that may begin with an example-related prefix,
            such as "example" or "mini-example".

    Returns:
        bool:
            True if the normalized text starts with any configured
            example prefix, otherwise False.
    """
    lowered: str = (text or "").strip().lower()
    return bool(lowered) and any(
        lowered.startswith(prefix) for prefix in _EXAMPLE_LINE_PREFIXES
    )


def _append_comma_after_token(match: re.Match[str]) -> str:
    """
    Append a trailing comma and space to a matched token.

    This helper preserves the original matched text and adds a comma
    delimiter, which can be used to separate terms for TTS
    pronunciation.

    Args:
        match (re.Match[str]):
                Regular expression match object whose full matched text
                will be suffixed with a comma and space.

    Returns:
        str:
            The original matched text followed by ", " for use as a
            delimited token.
    """
    return (match.group(0) or "") + ", "


def _pause_chunk_for_punctuation(
    part: str,
    *,
    pause_comma: str,
    pause_semi: str,
    pause_colon: str,
    pause_dash: str,
) -> str:
    """
    Map a punctuation token to its corresponding pause marker token.

    This helper selects the appropriate pause symbol based on the kind
    of clause or phrase delimiter encountered in the text.

    Args:
        part (str):
            Single-character punctuation token to evaluate, expected to
            be one of ",", ";", ":", or a dash-equivalent.
        pause_comma (str):
            Pause marker string to use when the punctuation token is a
            comma.
        pause_semi (str):
            Pause marker string to use when the punctuation token is a
            semicolon.
        pause_colon (str):
            Pause marker string to use when the punctuation token is a
            colon.
        pause_dash (str):
            Pause marker string to use for dash-style separators or any
            other non-comma, non-semicolon, non-colon token.

    Returns:
        str:
            The chosen pause marker token corresponding to the supplied
            punctuation character.
    """
    if part == ",":
        return pause_comma
    if part == ";":
        return pause_semi
    return pause_colon if part == ":" else pause_dash


def _inter_sentence_pause_token(
    end_char: str,
    *,
    pause_dot: str,
    pause_semi: str,
    pause_colon: str,
    pause_short: str,
) -> str:
    """
    Choose an appropriate pause token for the end of a sentence.

    This helper inspects the terminating character and maps it to a
    pause length that reflects how strong the boundary between
    sentences should sound.

    Args:
        end_char (str):
            Single-character string representing the final punctuation
            mark in a sentence, such as ".", "!", "?", "…", ";", or
            ":".
        pause_dot (str):
            Pause token to use after strong sentence-ending punctuation
            like a period, exclamation mark, question mark, or ellipsis.
        pause_semi (str):
            Pause token to use when the sentence ends with a semicolon.
        pause_colon (str):
            Pause token to use when the sentence ends with a colon.
        pause_short (str):
            Short pause token to use when no specific end punctuation is
            recognized.

    Returns:
        str:
            The pause token corresponding to the supplied
            end-of-sentence character, or the short pause token when no
            special mapping applies.
    """
    if end_char in ".!?…":
        return pause_dot
    if end_char == ";":
        return pause_semi
    return pause_colon if end_char == ":" else pause_short


@dataclass(frozen=True)
class _TtsSplitPauses:
    """
    Define symbolic pause tokens used when splitting text for TTS.

    This dataclass groups all pause markers so they can be passed around
    as a single configuration object and applied consistently across
    the splitting pipeline.

    Attributes:
        par (str):
            Pause token inserted between paragraphs to signal a long
            break in speech.
        short (str):
            Short pause token used for lightweight breaks, such as
            around parentheses or minor separators.
        comma (str):
            Pause token used when a comma is encountered to mark a brief
            clause boundary.
        semi (str):
            Pause token used when a semicolon is encountered to mark a
            medium-strength break.
        colon (str):
            Pause token used when a colon is encountered to mark an
            explanatory or list-style break.
        dot (str):
            Pause token used for strong sentence endings such as
            periods, exclamation marks, or question marks.
        dash (str):
            Pause token used for dash-style separators or other emphatic
            mid-sentence breaks.
    """

    par: str
    short: str
    comma: str
    semi: str
    colon: str
    dot: str
    dash: str


def _default_split_pauses() -> _TtsSplitPauses:
    """
    Create a default configuration of pause tokens for TTS splitting.

    This factory bundles the standard symbolic pause markers into a
    single dataclass instance so callers can reuse a consistent pause
    scheme.

    Returns:
        _TtsSplitPauses:
            Dataclass instance whose fields contain the default
            paragraph, short, comma, semicolon, colon, dot, and dash
            pause tokens used by the TTS splitting pipeline.
    """
    return _TtsSplitPauses(
        par="__TTS_PAUSE_PAR__",
        short="__TTS_PAUSE_SHORT__",
        comma="__TTS_PAUSE_COMMA__",
        semi="__TTS_PAUSE_SEMI__",
        colon="__TTS_PAUSE_COLON__",
        dot="__TTS_PAUSE_DOT__",
        dash="__TTS_PAUSE_DASH__",
    )


def _normalize_text_before_split(text: str) -> str:
    """
    Normalize raw text before splitting it into TTS chunks.

    This helper trims surrounding whitespace and collapses excess spaces
    and blank lines so downstream splitting logic sees a clean,
    predictable layout.

    Args:
        text (str):
            Input text that may contain mixed indentation, trailing
            spaces, or multiple consecutive blank lines.

    Returns:
        str:
            A normalized text string with leading and trailing
            whitespace removed, single newlines kept, runs of three or
            more newlines collapsed to two, and repeated spaces or tabs
            within lines collapsed to a single space.
    """
    text = (text or "").strip()
    if not text:
        return ""
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _paragraph_strings_from_text(text: str) -> list[str]:
    """
    Split normalized text into paragraph-sized string segments.

    This helper uses blank-line boundaries to detect paragraphs, trims
    extraneous whitespace around each block, and discards empty
    sections.

    Args:
        text (str):
            Input text that may contain single newlines within
            paragraphs and two or more consecutive newlines between
            paragraphs.

    Returns:
        list[str]:
            A list of non-empty paragraph strings, each stripped of
            leading and trailing whitespace, in the original document
            order.
    """
    return [
        part.strip()
        for part in re.split(r"\n{2,}", text)
        if (part or "").strip()
    ]


def _merge_paragraph_lines_to_par_text(paragraph: str) -> str | None:
    """
    Merge multi-line paragraph text into a single sentence string.

    This helper strips empty lines, appends missing periods to likely
    sentence ends, and joins the resulting lines into normalized
    paragraph text.

    Args:
        paragraph (str):
            Raw paragraph content that may contain multiple lines,
            missing sentence-ending punctuation, and varying
            whitespace.

    Returns:
        str | None:
            A single-line paragraph string with inferred periods
            inserted where appropriate, or None if the input contains
            no non-empty text after normalization.
    """
    lines: list[str] = [
        line.strip() for line in paragraph.splitlines() if line.strip()
    ]
    normalized_lines: list[str] = [
        (
            raw_line + "."
            if (
                bool(raw_line)
                and raw_line[-1] not in _LINE_ENDS_ALLOWING_NO_PERIOD
                and len(raw_line) > _MIN_LINE_LEN_FOR_SENTENCE_PERIOD
            )
            else raw_line
        )
        for raw_line in lines
    ]
    par_text: str = " ".join(normalized_lines).strip()
    return par_text or None


def _sentences_in_paragraph(par_text: str) -> list[str]:
    """
    Split a paragraph string into individual sentence fragments.

    This helper uses terminal punctuation boundaries to detect sentence
    ends and filters out any empty or whitespace-only pieces.

    Args:
        par_text (str):
            Paragraph text in which sentences are separated by
            punctuation such as ".", "!", "?", or "…" followed by
            whitespace.

    Returns:
        list[str]:
            A list of non-empty sentence strings, each preserving its
            original punctuation, in the order they appear in the
            paragraph.
    """
    return [
        part
        for part in re.split(r"(?<=[.!?…])\s+", par_text)
        if (part or "").strip()
    ]


def _phrase_tokens_with_dashes(sentence: str) -> list[str]:
    """
    Split a sentence into phrase tokens, preserving comma, semicolon,
    colon, and dash-style separators.

    This helper separates clauses around punctuation and normalizes
    dash-based breaks into explicit em dash tokens for downstream TTS
    chunking.

    Args:
        sentence (str):
            Sentence text that may contain commas, semicolons, colons,
            or dash-style separators joining phrases.

    Returns:
        list[str]:
            A list of tokens where plain text segments and individual
            punctuation markers (",", ";", ":", and em dashes) appear
            as separate elements in their original order.
    """
    phrase: list[str | Any] = [
        item
        for item in re.split(r"([,;:])\s*", sentence)
        if item is not None and item != ""
    ]
    phrase2: list[str] = []
    for item in phrase:
        if item and item not in _PHRASE_CLAUSE_SEPARATORS:
            parts: list[str | Any] = [
                part
                for part in re.split(_DASH_SPLIT_PATTERN, item)
                if part is not None and part != ""
            ]
            for part_index, part in enumerate[str | Any](parts):
                if part:
                    phrase2.append(part)
                if part_index != len(parts) - 1:
                    phrase2.append(_EM_DASH)
        else:
            phrase2.append(item)
    return phrase2


def _append_chunks_for_speech_part(
    part: str,
    chunks: list[str],
    *,
    pauses: _TtsSplitPauses,
) -> None:
    """
    Append pause and content tokens for a single speech fragment.

    This helper breaks a fragment into subsegments, inserts short pause
    markers around punctuation-like symbols, and extends the chunk list
    in playback order.

    Args:
        part (str):
            Raw fragment of text to process, which may contain only
            punctuation, parentheses, or inline operator-like symbols.
        chunks (list[str]):
            Mutable list of accumulated output tokens that will be
            extended with content and pause markers derived from the
            fragment.
        pauses (_TtsSplitPauses):
            Configuration object providing the symbolic pause tokens to
            use for short pauses and clause separators.
    """
    part = part.strip()
    if not part:
        return
    if part in _SPEECH_CHUNK_PUNCT_TOKENS:
        chunks.append(
            _pause_chunk_for_punctuation(
                part,
                pause_comma=pauses.comma,
                pause_semi=pauses.semi,
                pause_colon=pauses.colon,
                pause_dash=pauses.dash,
            )
        )
        return
    segments: list[str | Any] = [
        seg
        for seg in re.split(r"([()])", part)
        if seg is not None and seg != ""
    ]
    for segment in segments:
        if segment in ("(", ")"):
            chunks.append(pauses.short)
            continue
        sub: list[str | Any] = [
            item
            for item in re.split(r"\s+([=+*<>-])\s+", segment)
            if item is not None and item != ""
        ]
        for item in sub:
            chunks.append(item.strip())
            if re.fullmatch(r"[=+*<>-]", item.strip() or ""):
                chunks.append(pauses.short)


def _append_sentence_chunks(
    sentence: str,
    chunks: list[str],
    *,
    sentence_index: int,
    sentence_count: int,
    pauses: _TtsSplitPauses,
) -> None:
    sentence = sentence.strip()
    if not sentence:
        return
    for token in _phrase_tokens_with_dashes(sentence):
        _append_chunks_for_speech_part(token, chunks, pauses=pauses)
    if sentence_index != sentence_count - 1:
        end_char: str = sentence[-1] if sentence else ""
        chunks.append(
            _inter_sentence_pause_token(
                end_char,
                pause_dot=pauses.dot,
                pause_semi=pauses.semi,
                pause_colon=pauses.colon,
                pause_short=pauses.short,
            )
        )


def _tts_append_paragraph_chunks(
    chunks: list[str],
    paragraph: str,
    pauses: _TtsSplitPauses,
    *,
    append_paragraph_pause: bool,
) -> None:
    """
    Convert a paragraph string into ordered TTS chunk tokens.

    This helper merges paragraph lines into sentences, expands each
    sentence into speech and pause tokens, and optionally appends a
    paragraph-level pause marker.

    Args:
        chunks (list[str]):
            Mutable list that accumulates output tokens and will be
            extended with chunks derived from the paragraph sentences.
        paragraph (str):
            Raw paragraph text that may contain multiple lines and
            varying whitespace, which will be normalized before
            splitting into sentences.
        pauses (_TtsSplitPauses):
            Configuration object providing the symbolic pause tokens
            used between sentences and at paragraph boundaries.
        append_paragraph_pause (bool):
            Flag indicating whether a paragraph pause token should be
            appended after processing all sentences in this paragraph.
    """
    par_text: str | None = _merge_paragraph_lines_to_par_text(paragraph)
    if par_text is None:
        return
    sentences: list[str] = _sentences_in_paragraph(par_text)
    n_sent: int = len(sentences)
    for sent_index, sentence in enumerate[str](sentences):
        _append_sentence_chunks(
            sentence,
            chunks,
            sentence_index=sent_index,
            sentence_count=n_sent,
            pauses=pauses,
        )
    if append_paragraph_pause:
        chunks.append(pauses.par)


@dataclass
class _MdScan:
    """
    Track state while scanning Markdown lines for TTS extraction.

    This dataclass records whether the scanner is inside code fences,
    collapsible details blocks, or sections that should be skipped so
    that each input line can be classified correctly.

    Attributes:
        in_fence (bool):
            Flag indicating that the current scan position is inside a
            fenced code block, so lines should be buffered as code
            instead of emitted as prose.
        fence_buf (list[str]):
            Accumulated raw lines for the current fenced code block,
            which are flushed and normalized once the closing fence is
            found.
        in_details (bool):
            Flag indicating that the scanner is inside an HTML details
            section whose contents should be ignored for speech text.
        skip_section_level (int | None):
            Heading level of a section whose body should be skipped, or
            None when all subsequent headings and content are eligible
            for inclusion.
    """

    in_fence: bool = False
    fence_buf: list[str] = field(default_factory=list)
    in_details: bool = False
    skip_section_level: int | None = None


def _md_scan_fence_boundary(
    scan: _MdScan,
    out_lines: list[str],
    normalize_code: NormalizeCodeTextFn,
) -> None:
    """
    Toggle fenced-code scanning state and flush completed code blocks.

    This helper starts buffering lines when an opening fence is seen
    and, upon the closing fence, normalizes the captured code and
    appends it to the output stream with special markers.

    Args:
        scan (_MdScan):
            Mutable scan state object that tracks whether the parser is
            currently inside a fenced code block and holds the buffered
            lines.
        out_lines (list[str]):
            List of accumulated output lines that will receive the
            normalized code block contents once a closing fence is
            processed.
        normalize_code (NormalizeCodeTextFn):
            Callback used to convert raw code text into a normalized
            form before it is added to the output lines.
    """
    if not scan.in_fence:
        scan.in_fence = True
        scan.fence_buf = []
        return
    scan.in_fence = False
    code: str = "\n".join(scan.fence_buf).strip("\n")
    scan.fence_buf = []
    if code:
        out_lines.extend((_CODE_BLOCK_FENCE_LABEL, normalize_code(code), ""))


def _md_line_is_inside_skipped_details(scan: _MdScan, stripped: str) -> bool:
    """
    Detect whether a Markdown line lies inside an HTML details block
    that should be skipped.

    This helper updates the scan state when opening or closing details
    tags are encountered and reports if the current line belongs to a
    hidden details section.

    Args:
        scan (_MdScan):
            Mutable scan state object whose in_details flag is toggled
            when opening or closing HTML details tags are seen.
        stripped (str):
            Current line content stripped of leading and trailing
            trailing whitespace that will be inspected for details tags.

    Returns:
        bool:
            True if the line is within an active details block whose
            content should be skipped for TTS extraction, otherwise
            False.
    """
    if re.search(r"<\s*details\b", stripped, flags=re.IGNORECASE):
        scan.in_details = True
    if not scan.in_details:
        return False
    if re.search(r"</\s*details\s*>", stripped, flags=re.IGNORECASE):
        scan.in_details = False
    return True


def _md_line_handled_by_heading_or_skip(
    scan: _MdScan, stripped: str, out_lines: list[str]
) -> bool:
    """
    Handle Markdown headings and skip-logic for a single stripped line.

    This helper updates the current skip-section level based on heading
    content, forwards visible headings to the output, and reports
    whether the line has been fully processed.

    Args:
        scan (_MdScan):
            Mutable scan state object whose skip_section_level may be
            updated when heading lines that should be skipped or
            resumed are encountered.
        stripped (str):
            Current line content with leading and trailing whitespace
            removed, which will be inspected for heading markers and
            skip-heading patterns.
        out_lines (list[str]):
            List of accumulated output lines that receives heading
            lines and separating blank lines when a visible heading is
            processed.
            content with leading and trailing whitespace removed, which
            will be inspected for heading markers and skip-heading
            patterns.

    Returns:
        bool:
            True if the line has been handled as a heading, lies inside
            a skipped section, or matches a horizontal rule or example
            line that should be ignored; False if the caller should
            treat the line as regular content.
    """
    level: int | None = _markdown_heading_level(stripped)
    if level is not None:
        if (
            scan.skip_section_level is not None
            and level <= scan.skip_section_level
            and not _looks_like_heading_to_skip(text=stripped)
        ):
            scan.skip_section_level = None
        if _looks_like_heading_to_skip(text=stripped):
            scan.skip_section_level = level
            return True
    if scan.skip_section_level is not None:
        return True
    if level is not None:
        out_lines.extend((stripped, ""))
        return True
    return stripped in _MD_HORIZONTAL_RULE_MARKERS or _looks_like_example_line(
        text=stripped
    )


def _md_append_line_to_out_lines(
    scan: _MdScan,
    raw: str,
    out_lines: list[str],
    normalize_code: NormalizeCodeTextFn,
) -> None:
    """
    Process a raw Markdown line and decide how it contributes to TTS
    output.

    This helper routes the line through fence, details, and heading
    handlers, appending it to the accumulated output only when it
    should be spoken as visible content.

    Args:
        scan (_MdScan):
            Mutable scan state object that tracks whether the parser is
            inside fenced code blocks, details sections, or skipped
            headings.
        raw (str):
            Raw Markdown line including any trailing newline characters
            that will be inspected and possibly normalized.
        out_lines (list[str]):
            List of accumulated output lines that receives visible
            content lines and normalized code or heading markers.
        normalize_code (NormalizeCodeTextFn):
            Callback used to normalize fenced code blocks when fence
            boundaries are encountered.
    """
    line_content: str = raw.rstrip("\n")
    stripped: str = line_content.strip()
    if stripped.startswith("```"):
        _md_scan_fence_boundary(scan, out_lines, normalize_code)
        return
    if scan.in_fence:
        scan.fence_buf.append(line_content)
        return
    if _md_line_is_inside_skipped_details(scan, stripped):
        return
    if _md_line_handled_by_heading_or_skip(scan, stripped, out_lines):
        return
    out_lines.append(line_content)


def _md_collect_out_lines(
    md_text: str, normalize_code: NormalizeCodeTextFn
) -> list[str]:
    """
    Scan Markdown text into a linear sequence of TTS-ready lines.

    This helper walks the input line by line, applies fence, details,
    and heading rules, and returns only the content that should be
    spoken or normalized as code.

    Args:
        md_text (str):
            Full Markdown document text, which may contain headings,
            fenced code blocks, HTML details sections, and other
            Markdown constructs.
        normalize_code (NormalizeCodeTextFn):
            Callback used to normalize the contents of fenced code
            blocks and inline code before they are added to the output
            lines.

    Returns:
        list[str]:
            A list of plain text lines representing the visible document
            content and normalized code, in the order they should be
            voiced by TTS.
    """
    scan: _MdScan = _MdScan()
    out_lines: list[str] = []
    for raw in md_text.splitlines():
        _md_append_line_to_out_lines(scan, raw, out_lines, normalize_code)
    return out_lines


def _md_strip_to_plain_text(
    text: str, normalize_code: NormalizeCodeTextFn
) -> str:
    """
    Strip Markdown formatting and links to produce plain speech text.

    This helper removes inline code, links, emphasis markers, list
    bullets, and excessive whitespace so the remaining content is
    suitable for TTS processing.

    Args:
        text (str):
            Raw Markdown text that may contain inline code spans, links,
            emphasis, headings, lists, URLs, and email addresses that
            should not be spoken verbatim.
        normalize_code (NormalizeCodeTextFn):
            Callback used to normalize the
            contents of inline code spans before they are substituted
            into the plain-text output.

    Returns:
        str:
            A plain-text version of the input with Markdown syntax,
            URLs, and email addresses removed, inline code normalized,
            and whitespace collapsed while preserving basic line
            structure.
    """
    text = re.sub(
        r"`([^`]+)`",
        lambda match: normalize_code(match.group(1) or ""),
        text,
    )
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\s*\[[^\]]*\]", r"\1", text)
    text = re.sub(r"(?m)^\s*\[[^\]]+\]:\s+\S+.*$", "", text)
    text = re.sub(r"<\s*(https?://[^>]+)\s*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*www\.[^>]+\s*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bwww\.\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b[\w.\-+]+@[\w.\-]+\.\w+\b", "", text, flags=re.IGNORECASE
    )
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(?<!\w)\*(?!\*)([^*\n]+)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_(?!_)([^_\n]+)_(?!\w)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _regex_pairs_from_rules_payload(payload: object) -> list[tuple[str, str]]:
    """
    Extract validated regex replacement pairs from a rules payload
    object.

    This helper safely inspects a loosely-typed payload, discards
    malformed entries, and returns only well-formed pattern-replacement
    pairs for downstream text normalization.

    Args:
        payload (object):
            Arbitrary object expected to be a dictionary containing a
            "regex_replacements" key whose value is a list of
            two-element string lists representing (pattern, replacement)
            pairs.

    Returns:
        list[tuple[str, str]]:
            A list of (pattern, replacement) tuples built from the
            payload, or an empty list if the structure is missing, not
            a dictionary, or contains no valid string pairs.
    """
    raw_dict: dict[str, Any] | None = (
        payload if isinstance(payload, dict) else None
    )
    if raw_dict is None:
        return []
    replacements = raw_dict.get("regex_replacements", [])
    if not isinstance(replacements, list):
        return []
    arity = _REGEX_RULE_PAIR_ARITY
    return [
        (pair[0], pair[1])
        for pair in replacements
        if (
            isinstance(pair, list)
            and len(pair) == arity
            and all(isinstance(part, str) for part in pair)
        )
    ]


class TtsTextPipeline:
    """
    Encapsulate text-to-speech normalization and chunking logic.

    This class loads optional regex rules, normalizes code and prose for
    speech, extracts text from Markdown, and segments content into
    pause-aware TTS chunks.

    Attributes:
        rules_path (Path):
            Path to the JSON rules file containing regex replacement
            pairs.
        rules_cache (_RegexRulesCache):
            Cached rules object that tracks the file metadata and
            contents to avoid unnecessary reloads.

    # Methods:

        _load_regex_replacements(
            rules_path: Path
        ) -> list[tuple[str, str]]:
            Load and cache regex replacement rules from a JSON rules
            file, returning only validated (pattern, replacement)
            pairs.

        _apply_dictionary_rules(
            text: str
        ) -> str:
            Apply the loaded regex replacement rules to the given text
            and return the normalized result.

        _spell_latin_single_letters(
            text: str
        ) -> str:
            Replace isolated Latin letters with their Russian letter
            names to improve pronunciation of single-character tokens.

        _spell_latin_abbrev(
            text: str
        ) -> str:
            Expand selected Latin abbreviations into sequences of
            Russian letter names when they appear to be acronyms or
            whitelisted terms.

        normalize_code_text(
            text: str
        ) -> str:
            Normalize source-code text into a TTS-friendly form by
            cleaning spacing, expanding operators, and applying
            dictionary rules.

        normalize_speech_text(
            text: str
        ) -> str:
            Normalize prose-like or HTML/Markdown-derived text into a
            speech-ready form, inserting lightweight pauses and
            expanding symbols.

        extract_text_from_markdown(
            md_text: str
        ) -> str:
            Extract visible, speech-oriented text from a Markdown
            document, stripping markup and normalizing the remaining
            prose.

        split_for_tts(
            text: str
        ) -> list[str]:
            Split normalized text into an ordered sequence of speech and
            pause tokens suitable for TTS playback.
    """

    def __init__(self, rules_path: Path) -> None:
        self._rules_path: Path = rules_path
        self._rules_cache: _RegexRulesCache = _RegexRulesCache()

    def _load_regex_replacements(self) -> list[tuple[str, str]]:
        """
        Load and cache regex replacement rules from the rules file on
        disk.

        This helper uses file metadata to avoid unnecessary reloads and
        falls back gracefully to an empty ruleset when the file is
        missing, unreadable, or malformed.

        Returns:
            list[tuple[str, str]]:
                A list of (pattern, replacement) tuples representing the
                currently active regex rules, or an empty list if the
                rules file cannot be read or contains no valid
                replacements.
        """
        try:
            stat: stat_result = self._rules_path.stat()
        except OSError:
            self._rules_cache = _RegexRulesCache()
            return []
        mtime: float = stat.st_mtime
        size: int = stat.st_size
        if (
            self._rules_cache.mtime == mtime
            and self._rules_cache.size == size
            and self._rules_cache.rules
        ):
            return list[tuple[str, str]](self._rules_cache.rules)
        try:
            raw_text: str = self._rules_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self._rules_cache = _RegexRulesCache(mtime=mtime, size=size)
            return []
        try:
            payload: object = json.loads(raw_text)
        except json.JSONDecodeError:
            self._rules_cache = _RegexRulesCache(mtime=mtime, size=size)
            return []
        loaded: list[tuple[str, str]] = _regex_pairs_from_rules_payload(
            payload
        )
        self._rules_cache = _RegexRulesCache(
            mtime=mtime, size=size, rules=loaded
        )
        return loaded

    def _apply_dictionary_rules(self, text: str) -> str:
        """
        Apply configured dictionary-style regex rules to a text string.

        This helper runs each loaded pattern-replacement pair over the
        input, silently ignoring invalid patterns, and returns the
        transformed text.

        Args:
            text (str):
                Input text to normalize using the currently loaded
                dictionary regex replacements.

        Returns:
            str:
                The text after sequentially applying all valid regex
                replacements from the rules file.
        """
        for pattern, repl in self._load_regex_replacements():
            try:
                text = re.sub(pattern, repl, text)
            except re.error:
                continue
        return text

    def _spell_latin_single_letters(self, text: str) -> str:
        """
        Spell isolated Latin letters using Russian letter names.

        This helper finds single Latin characters that stand alone as
        tokens and replaces them with their corresponding spoken
        Russian names to improve TTS clarity.

        Args:
            text (str):
                Input text that may contain standalone Latin letters
                surrounded by non-alphanumeric boundaries.

        Returns:
            str:
                The text where eligible single-letter Latin tokens have
                been replaced with their Russian letter-name
                equivalents, leaving all other content unchanged.
        """
        if not text:
            return ""

        def repl(match: re.Match[str]) -> str:
            ch: str = (match.group(0) or "").strip()
            return _LATIN_LETTER_NAMES_RU.get(ch.upper(), ch) if ch else ch

        return re.sub(r"(?<![A-Za-z0-9_])[A-Za-z](?![A-Za-z0-9_])", repl, text)

    def _spell_latin_abbrev(self, text: str) -> str:
        """
        Spell Latin abbreviations using Russian letter names where
        appropriate.

        This helper expands short Latin tokens into sequences of Russian
        letter names when they look like abbreviations or belong to a
        dedicated whitelist, improving TTS pronunciation of technical
        acronyms.

        Args:
            text (str):
                Input text that may contain short Latin word-like tokens
                and uppercase abbreviations to be considered for
                spelling out.

        Returns:
            str:
                The text where eligible 2-6 letter Latin tokens have
                been replaced with space-separated Russian letter-name
                sequences, while non-matching words are left unchanged.
        """
        if not text:
            return ""

        def repl_upper_token(match: re.Match[str]) -> str:
            """
            Normalize source-code text for TTS-friendly pronunciation.

            This method cleans up whitespace, expands operator symbols,
            applies dictionary regex rules, and spells selected Latin
            abbreviations to produce readable code snippets for speech.

            Args:
                text (str):
                    Raw code text that may contain tabs, mixed newlines,
                    dense operator symbols, and technical abbreviations
                    requiring normalization.

            Returns:
                str:
                    A normalized code string with consistent spacing and
                    newlines, operator phrases substituted, dictionary
                    rules applied, chosen Latin abbreviations spelled
                    out, and leading/trailing whitespace removed.
            """
            token: str = (match.group(0) or "").strip()
            letters: list[str] = [
                _LATIN_LETTER_NAMES_RU.get(ch.upper(), ch.lower())
                for ch in token
            ]
            return " ".join(letters) if token else token

        text = re.sub(r"\b[A-Z]{2,6}\b", repl_upper_token, text)

        def repl_whitelist(match: re.Match[str]) -> str:
            """
            Normalize source-code text for TTS-friendly pronunciation.

            This method cleans up whitespace, expands operator symbols,
            applies dictionary regex rules, and spells selected Latin
            abbreviations to produce readable code snippets for speech.

            Args:
                text (str):
                    Raw code text that may contain tabs, mixed newlines,
                    dense operator symbols, and technical abbreviations
                    requiring normalization.

            Returns:
                str:
                    A normalized code string with consistent spacing and
                    newlines, operator phrases substituted, dictionary
                    rules applied, chosen Latin abbreviations spelled
                    out, and leading or trailing whitespace removed.
            """
            token: str = (match.group(0) or "").strip()
            if not token or token.lower() not in _LATIN_SPELL_WHITELIST:
                return token
            letters: list[str] = [
                _LATIN_LETTER_NAMES_RU.get(ch.upper(), ch.lower())
                for ch in token
            ]
            return " ".join(letters)

        return re.sub(r"\b[A-Za-z]{2,6}\b", repl_whitelist, text)

    def normalize_code_text(self, text: str) -> str:
        """
        Normalize source-code text for TTS-friendly pronunciation.

        This method cleans and simplifies code snippets so they are
        easier to read aloud, while preserving their logical structure
        and operator relationships.

        Args:
            text (str):
                Raw code text that may contain tabs, mixed newline
                styles, densely packed operators, and irregular spacing.

        Returns:
            str:
                A normalized code string with consistent spacing and
                newlines, expanded operator phrases, dictionary-based
                replacements applied, and leading or trailing whitespace
                removed.
        """
        if not text.strip():
            return ""
        text = text.replace("\t", "  ")
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"[ \t]{3,}", "  ", text)
        for pattern, repl in _CODE_OPERATOR_REPLACEMENTS:
            text = re.sub(pattern, repl, text)
        text = self._apply_dictionary_rules(text)
        text = self._spell_latin_abbrev(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def normalize_speech_text(self, text: str) -> str:
        """
        Normalize prose-like text for TTS-friendly pronunciation.

        This method cleans and reshapes mixed Markdown or HTML-derived
        text so it reads naturally when spoken, while preserving
        sentence boundaries and lightweight structural cues.

        Args:
            text (str):
                Raw input text that may contain HTML remnants, Markdown
                code fences, images, tables, abbreviations, camelCase
                identifiers, and irregular whitespace.

        Returns:
            str:
                A normalized speech string with non-speech markup
                removed, dictionary rules and abbreviation spelling
                applied, symbol sequences expanded, lightweight pauses
                inserted, and excessive whitespace stripped from the
                ends.
        """
        if not text:
            return ""
        pause_short = "__TTS_PAUSE_SHORT__"
        text = text.replace(
            _CODE_BLOCK_FENCE_LABEL, _CODE_BLOCK_LABEL_PLACEHOLDER
        )
        text = html.unescape(text).replace("\xa0", " ")
        text = re.sub(r"(?is)</?\s*(details|summary)\b[^>]*>", " ", text)
        text = re.sub(r"(?is)</?\s*br\s*/?\s*>", "\n", text)
        text = re.sub(r"(?is)</?\s*hr\s*/?\s*>", " ", text)
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
        text = re.sub(r"\[\^[^\]]+\]", " ", text)
        text = re.sub(r"(?m)^\s*\|(?:\s*:?-+:?\s*\|)+\s*$", " ", text)
        text = self._apply_dictionary_rules(text)
        text = self._spell_latin_abbrev(text)
        text = self._spell_latin_single_letters(text)
        text = re.sub(
            r"(?<=[A-Za-z])(?=(?:[A-Z]{2,8}\b|[A-Za-z]*[A-Z][A-Za-z0-9_-]*))",
            ", ",
            text,
        )
        text = re.sub(
            r"(?:(?:\b[A-Z]{2,8})|(?:\b[A-Za-z]*[A-Z][A-Za-z0-9_-]*))(?=[A-Za-z])",
            _append_comma_after_token,
            text,
        )
        for pattern, repl in _SPEECH_SYMBOL_REPLACEMENTS:
            text = re.sub(pattern, repl, text)
        text = re.sub(r"\(\s*", ", ", text)
        text = re.sub(r"\s*\)", ", ", text)
        text = re.sub(r"(?<=\w)_(?=\w)", " ", text)
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
        text = re.sub(r"(?<=\w)\.(?=\w)", ", ", text)
        if re.search(r"[A-Za-z]", text):
            text = re.sub(
                r"\b([A-Za-z][A-Za-z0-9_-]{1,})\b(?=(?:\s|[.,;:!?\)])+[A-Za-z])",
                rf"\1 {pause_short}",
                text,
            )
        text = re.sub(
            r"(?i)\bi\s+s\b(?=\s|[.,;:!?\)])", f"i s {pause_short}", text
        )
        text = re.sub(
            r"(?i)\bi\s+d\b(?=\s|[.,;:!?\)])", f"i d {pause_short}", text
        )
        text = re.sub(_DASH_SPLIT_PATTERN, ", ", text)
        text = re.sub(r"\s+-\s+", ", ", text)
        text = re.sub(r"\s*;\s*", "; ", text)
        text = re.sub(r"\s*:\s*", ": ", text)
        text = re.sub(r"\s*,\s*", ", ", text)
        text = re.sub(r",\s*,+", ", ", text)
        text = re.sub(r"\.\s*\.\s*\.+", "...", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.replace(
            _CODE_BLOCK_LABEL_PLACEHOLDER, _CODE_BLOCK_FENCE_LABEL
        )
        return text.strip(" \n,;:")

    def extract_text_from_markdown(self, md_text: str) -> str:
        """
        Extract speech-oriented text from a Markdown document.

        This method filters out non-speech elements such as code fences
        and hidden sections, strips Markdown formatting, and normalizes
        the remaining prose for TTS consumption.

        Args:
            md_text (str):
                Raw Markdown document text that may include headings,
                code blocks, details sections, links, and other markup
                constructs.

        Returns:
            str:
                A normalized plain-text string containing only the
                visible content intended to be read aloud, with code
                and formatting converted into speech-friendly form.
        """
        if not md_text.strip():
            return ""
        out_lines: list[str] = _md_collect_out_lines(
            md_text, normalize_code=self.normalize_code_text
        )
        blob: str = "\n".join(out_lines)
        blob = _md_strip_to_plain_text(
            text=blob, normalize_code=self.normalize_code_text
        )
        return self.normalize_speech_text(text=blob)

    def split_for_tts(self, text: str) -> list[str]:
        """
        Split normalized text into ordered TTS playback chunks.

        This method breaks prose into paragraphs and sentences, inserts
        pause markers between logical units, and returns a linear
        sequence of tokens ready for audio synthesis.

        Args:
            text (str):
                Raw input text that may contain multiple paragraphs and
                sentences, which will be normalized and segmented into
                TTS chunks.

        Returns:
            list[str]:
                A list of strings representing speech fragments and
                pause tokens in the order they should be spoken by the
                TTS engine.
        """
        normalized: str = _normalize_text_before_split(text)
        if not normalized:
            return []
        pauses: _TtsSplitPauses = _default_split_pauses()
        chunks: list[str] = []
        paragraphs: list[str] = _paragraph_strings_from_text(text=normalized)
        last_par_index: int = len(paragraphs) - 1
        for par_index, paragraph in enumerate[str](paragraphs):
            _tts_append_paragraph_chunks(
                chunks,
                paragraph,
                pauses,
                append_paragraph_pause=par_index != last_par_index,
            )
        return chunks
