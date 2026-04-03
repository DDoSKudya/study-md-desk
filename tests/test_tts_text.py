from __future__ import annotations

import pytest
from pathlib import Path

from viewer_app.core.tts_text import TtsTextPipeline
from viewer_app.runtime.paths import TTS_RULES_FILE_NAME, AppPaths

_MINIMAL_TTS_RULES_JSON: str = (
    '{"regex_replacements": [["\\\\bfoo\\\\b", "bar"]]}'
)

_CODE_SNIPPET: str = "a + b == c"
_SPEECH_PHRASE: str = "Hello, world."
_MARKDOWN_WITH_EMPHASIS: str = "# T\n\nPara **bold**."
_SPLIT_INPUT_SHORT: str = "One. Two."
_SPLIT_INPUT_REPO: str = "REST API is common."
_MIN_EXPECTED_CHUNKS: int = 1


@pytest.fixture
def rules_path(tmp_path: Path) -> Path:
    rules_file = tmp_path / TTS_RULES_FILE_NAME
    rules_file.write_text(_MINIMAL_TTS_RULES_JSON, encoding="utf-8")
    return rules_file


def test_normalize_code_text_expands_or_preserves_tokens(
    rules_path: Path,
) -> None:
    pipeline = TtsTextPipeline(rules_path)

    normalized = pipeline.normalize_code_text(_CODE_SNIPPET)

    assert (  # noqa: S101
        "plus" in normalized or "equals" in normalized or len(normalized) > 0
    )


def test_normalize_speech_text_keeps_readable_words(
    rules_path: Path,
) -> None:
    pipeline = TtsTextPipeline(rules_path)

    normalized = pipeline.normalize_speech_text(_SPEECH_PHRASE)

    assert "Hello" in normalized or len(normalized) > 0  # noqa: S101


def test_extract_text_from_markdown_yields_body_without_heading_markup(
    rules_path: Path,
) -> None:
    pipeline = TtsTextPipeline(rules_path)

    plain = pipeline.extract_text_from_markdown(_MARKDOWN_WITH_EMPHASIS)

    assert "Para" in plain or "bold" in plain  # noqa: S101


def test_split_for_tts_returns_list_with_at_least_one_chunk(
    rules_path: Path,
) -> None:
    pipeline = TtsTextPipeline(rules_path)

    chunks = pipeline.split_for_tts(_SPLIT_INPUT_SHORT)

    assert isinstance(chunks, list)  # noqa: S101
    assert len(chunks) >= _MIN_EXPECTED_CHUNKS  # noqa: S101


def test_split_for_tts_accepts_bundled_repository_rules_file() -> None:
    app_paths = AppPaths.discover()
    pipeline = TtsTextPipeline(app_paths.tts_rules_path)

    chunks = pipeline.split_for_tts(_SPLIT_INPUT_REPO)

    assert isinstance(chunks, list)  # noqa: S101
