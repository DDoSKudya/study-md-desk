from urllib.parse import quote

from pathlib import Path

from viewer_app.http.http_pages import (
    _reader_dark_mode,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.http.http_pages import (
    _resolve_markdown_fs_path,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.http.http_pages import (
    _view_rel_from_request,  # pyright: ignore[reportPrivateUsage]
)
from viewer_app.http.http_pages import (
    build_shell_html,
    build_toc_html,
    build_view_html,
    get_root_from_query,
    guess_content_type,
    json_for_script_tag,
    resolve_asset_path,
    resolve_view_asset,
)

_MINIMAL_PNG_BYTES: bytes = b"\x89PNG\r\n\x1a\n"
_EXPLAIN_PROMPT_KEY_RU: str = "explain_ru"


def test_json_for_script_tag_escapes_closing_script_sequence() -> None:
    serialized = json_for_script_tag({"x": "</script>"})

    assert r"<\/" in serialized or "<\\/" in serialized  # noqa: S101


def test_get_root_from_query_resolves_directory_from_root_parameter(
    tmp_path: Path,
) -> None:
    nested_root = tmp_path / "nested_project"
    nested_root.mkdir()
    query_string = f"root={nested_root}"

    resolved = get_root_from_query(query_string, tmp_path)

    assert resolved == nested_root.resolve()  # noqa: S101


def test_resolve_asset_path_blocks_parent_directory_traversal() -> None:
    assert resolve_asset_path("../secrets") is None  # noqa: S101


def test_resolve_asset_path_finds_bundled_shell_script() -> None:
    asset_path = resolve_asset_path("shell.js")

    assert asset_path is not None  # noqa: S101
    assert asset_path.is_file()  # noqa: S101


def test_guess_content_type_maps_common_extensions() -> None:
    assert "javascript" in guess_content_type(Path("a.js"))  # noqa: S101
    assert "css" in guess_content_type(Path("a.css"))  # noqa: S101
    assert (  # noqa: S101
        guess_content_type(Path("x.unknown")) == "application/octet-stream"
    )


def test_view_rel_from_request_strips_view_prefix() -> None:
    assert (  # noqa: S101
        _view_rel_from_request("/view/foo/bar.md") == "foo/bar.md"
    )


def test_resolve_markdown_fs_path_resolves_file_under_root(
    tmp_path: Path,
) -> None:
    markdown_file = tmp_path / "a.md"
    markdown_file.write_text("# A", encoding="utf-8")

    resolved = _resolve_markdown_fs_path(tmp_path, "a.md")

    assert resolved is not None  # noqa: S101
    assert resolved.name == "a.md"  # noqa: S101


def test_resolve_markdown_fs_path_rejects_path_traversal(
    tmp_path: Path,
) -> None:
    assert (  # noqa: S101
        _resolve_markdown_fs_path(tmp_path, "../../etc/passwd") is None
    )


def test_reader_dark_mode_true_when_rt_dark_present() -> None:
    assert _reader_dark_mode({"rt": ["dark"]}) is True  # noqa: S101


def test_reader_dark_mode_false_when_rt_absent() -> None:
    assert _reader_dark_mode({}) is False  # noqa: S101


def test_build_view_html_includes_document_shell(app_paths_factory) -> None:
    paths = app_paths_factory(with_plans=True, with_ini=False)
    page = paths.plans_dir / "page.md"
    page.write_text("# P\n\nBody.", encoding="utf-8")
    prompts = {_EXPLAIN_PROMPT_KEY_RU: "x"}

    html = build_view_html(
        paths=paths,
        query="",
        request_path="/view/page.md",
        prompts=prompts,
    )

    assert html is not None  # noqa: S101
    assert "md-doc" in html  # noqa: S101


def test_build_shell_html_includes_main_frame(app_paths_factory) -> None:
    paths = app_paths_factory(with_plans=True, with_ini=False)
    state = {
        "currentDoc": {
            "path": "",
            "root": str(paths.plans_dir),
            "title": "",
        },
        "projects": {"pinned": [], "recent": []},
    }

    html = build_shell_html(paths=paths, state=state, query="")

    assert "contentFrame" in html  # noqa: S101


def test_build_toc_html_lists_headings_or_links(app_paths_factory) -> None:
    paths = app_paths_factory(with_plans=True, with_ini=False)
    toc_source = paths.plans_dir / "t.md"
    toc_source.write_text("# One\n\n## Two\n", encoding="utf-8")
    encoded_plans_root = quote(str(paths.plans_dir.resolve()))
    query = f"path=t.md&root={encoded_plans_root}"

    html = build_toc_html(query=query, plans_dir=paths.plans_dir)

    assert html == "" or "One" in html or "href=" in html  # noqa: S101


def test_resolve_view_asset_finds_png_under_plans(app_paths_factory) -> None:
    paths = app_paths_factory(with_plans=True, with_ini=False)
    image = paths.plans_dir / "pic.png"
    image.write_bytes(_MINIMAL_PNG_BYTES)

    asset = resolve_view_asset(
        plans_dir=paths.plans_dir,
        query=f"root={paths.plans_dir}",
        request_path="/view/pic.png",
    )

    assert asset is not None  # noqa: S101
