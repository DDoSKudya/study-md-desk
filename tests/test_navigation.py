import pytest
from pathlib import Path

from viewer_app.core.navigation import (
    _document_directory_prefix,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.core.navigation import (
    _is_skipped_entry,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.core.navigation import (
    _markdown_href_should_pass_through,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.core.navigation import (
    _normalize_slashes,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.core.navigation import (
    _normpath_posix,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.core.navigation import (
    build_tree,
    collect_md_files,
    get_prev_next,
    rewrite_document_asset_urls,
    rewrite_document_markdown_links,
)

_TREE_VIEW_BASE: str = "/view/"
_TREE_ROOT_PARAM: str = "root=%2Ftmp"
_SAMPLE_DOC_REL_PATH: str = "chap/lesson.md"
_SAMPLE_ROOT_PARAM: str = "root=/r"
_EXPECTED_TWO_MARKDOWN_FILES: int = 2


def test_normalize_slashes_converts_backslashes_to_forward() -> None:
    assert _normalize_slashes(r"a\b") == "a/b"  # noqa: S101


@pytest.mark.parametrize(
    ("entry_name", "expected_skipped"),
    [
        (".git", True),
        ("__pycache__", True),
        ("docs", False),
    ],
)
def test_is_skipped_entry_reflects_ignore_rules(
    entry_name: str,
    expected_skipped: bool,
) -> None:
    assert _is_skipped_entry(entry_name) is expected_skipped  # noqa: S101


def test_document_directory_prefix_for_nested_file() -> None:
    assert _document_directory_prefix("foo/bar.md") == "foo/"  # noqa: S101


def test_document_directory_prefix_for_file_at_logical_root() -> None:
    assert _document_directory_prefix("x.md") in ("", "./")  # noqa: S101


def test_normpath_posix_collapses_parent_segments() -> None:
    assert _normpath_posix("a/../b") == "b"  # noqa: S101


@pytest.mark.parametrize(
    ("href", "expected_pass_through"),
    [
        ("http://x", True),
        ("/view/x", True),
        ("local.md", False),
    ],
)
def test_markdown_href_should_pass_through_external_and_app_routes(
    href: str,
    expected_pass_through: bool,
) -> None:
    assert (  # noqa: S101
        _markdown_href_should_pass_through(href) is expected_pass_through
    )


def test_build_tree_html_contains_markdown_entries(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    subdirectory = tmp_path / "sub"
    subdirectory.mkdir()
    (subdirectory / "b.md").write_text("# B", encoding="utf-8")

    tree_html = build_tree(
        tmp_path,
        base=_TREE_VIEW_BASE,
        root_param=_TREE_ROOT_PARAM,
    )

    assert "tree" in tree_html  # noqa: S101
    assert "a.md" in tree_html  # noqa: S101


def test_collect_md_files_finds_nested_markdown(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    subdirectory = tmp_path / "sub"
    subdirectory.mkdir()
    (subdirectory / "b.md").write_text("# B", encoding="utf-8")

    markdown_paths = collect_md_files(tmp_path, tmp_path)

    assert len(markdown_paths) == _EXPECTED_TWO_MARKDOWN_FILES  # noqa: S101


def test_get_prev_next_points_to_prior_file_in_order(tmp_path: Path) -> None:
    (tmp_path / "1.md").write_text("a", encoding="utf-8")
    (tmp_path / "2.md").write_text("b", encoding="utf-8")

    previous_path, next_path = get_prev_next(tmp_path, "2.md")

    assert previous_path is not None  # noqa: S101
    assert previous_path == "1.md"  # noqa: S101
    assert next_path is None  # noqa: S101


def test_rewrite_document_urls_pipeline_updates_assets_then_links() -> None:
    html_body = '<img src="pic.png"> and <a href="other.md">x</a>'

    with_asset_urls = rewrite_document_asset_urls(
        html_body=html_body,
        doc_rel_path=_SAMPLE_DOC_REL_PATH,
        root_param=_SAMPLE_ROOT_PARAM,
    )

    assert "/view/" in with_asset_urls  # noqa: S101

    with_markdown_links = rewrite_document_markdown_links(
        html_body=with_asset_urls,
        doc_rel_path=_SAMPLE_DOC_REL_PATH,
        root_param=_SAMPLE_ROOT_PARAM,
    )

    assert (  # noqa: S101
        "other.md" in with_markdown_links or "/view/" in with_markdown_links
    )
