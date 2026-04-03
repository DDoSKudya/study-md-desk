"""
This module implements navigation and URL rewriting utilities for a
Markdown-based document viewer.

It handles directory traversal, previous/next document lookup, and
rewriting of asset and Markdown links into internal viewer routes.

It defines constants for system directories to hide, patterns for remote
image sources, prefixes for links that should not be rewritten, and
compiled regexes for <img> and <a> tags.

Helper functions like _normalize_slashes, _path_tree_sort_key,
_is_skipped_entry, _sorted_directory_entries,
_document_directory_prefix, and _normpath_posix normalize and classify
filesystem paths for consistent cross-platform behavior.

The _img_src_replacement and _a_href_replacement functions use regex
matches to selectively rewrite local image and Markdown link URLs into
/view/... routes, while _markdown_href_should_pass_through keeps
external or special links unchanged.

build_tree constructs an HTML <ul class='tree'> navigation structure
from a folder hierarchy, skipping hidden/system entries and linking .md
files to their internal viewer URLs.

collect_md_files walks the directory tree to gather all Markdown files
as paths relative to a root, and get_prev_next derives the previous and
next document paths in that ordered list, skipping index pages when
moving forward.

rewrite_document_asset_urls and rewrite_document_markdown_links operate
on rendered HTML fragments, applying the regex-based replacers to turn
relative asset and Markdown links into internal viewer routes based on
the current documents relative path and a project/root parameter.

In the broader system, this module acts as the navigation and
link-routing backbone for the viewer, ensuring that filesystem content
is presented as a structured tree and that intra-project links resolve
correctly inside the web-based Markdown viewer.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from functools import partial

import html
from pathlib import Path
from typing import Any, Final, TypeAlias

PrevNext: TypeAlias = tuple[str | None, str | None]

SYSTEM_DIRS: Final[frozenset[str]] = frozenset[str](
    {
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".git",
        ".idea",
        ".vscode",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
    }
)

_IMG_REMOTE_PREFIXES: Final[tuple[str, ...]] = ("http://", "https://", "data:")

_LINK_SKIP_PREFIXES: Final[tuple[str, ...]] = (
    "http://",
    "https://",
    "mailto:",
    "javascript:",
    "data:",
    "#",
    "/view/",
)

_IMG_SRC_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'<img\s+([^>]*?)src=["\']([^"\']*)["\']',
    flags=re.IGNORECASE,
)

_A_HREF_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'<a\s+([^>]*?)href=["\']([^"\']*)["\']',
    flags=re.IGNORECASE,
)


def _normalize_slashes(path: str) -> str:
    """
    Normalize path separators to use forward slashes.

    This helper converts any backslashes in a path-like string into
    forward slashes so paths are treated consistently across platforms.

    Args:
        path (str):
            Raw path string that may contain backslash separators, as
            commonly produced on Windows systems.

    Returns:
        str:
            The same path string with every backslash character replaced
            by a forward slash.
    """
    return path.replace("\\", "/")


def _path_tree_sort_key(path: Path) -> tuple[bool, str]:
    """
    Provide a sort key that lists directories before files by name.

    This helper returns a tuple used for ordering paths so that folders
    appear first and all entries are sorted case-insensitively by their
    basename.

    Args:
        path (Path):
            Filesystem path object representing a directory or file to
            be ordered within a listing.

    Returns:
        tuple[bool, str]:
            A two-element tuple where the first value is False for
            directories and True for files, and the second value is the
            lowercase name of the path, suitable for use as a sort key.
    """
    return (not path.is_dir(), path.name.lower())


def _is_skipped_entry(name: str) -> bool:
    """
    Decide whether a filesystem entry should be hidden from navigation.

    This helper treats dot-prefixed names and known system or tooling
    directories as internal, excluding them from trees and file
    listings.

    Args:
        name (str):
            Base name of a directory or file to test, without any parent
            path components.

    Returns:
        bool:
            True if the entry is considered a hidden or system item that
            should be skipped in navigation views, otherwise False.
    """
    return name.startswith(".") or name in SYSTEM_DIRS


def _sorted_directory_entries(folder: Path) -> list[Path]:
    """
    Return a directory listing sorted for navigation-friendly display.

    This helper yields the immediate children of a folder with
    directories first and all entries ordered case-insensitively by
    name.

    Args:
        folder (Path):
            Filesystem directory whose direct contents should be
            enumerated and sorted for use in trees, pickers, or other
            navigational views.

    Returns:
        list[Path]:
            A list of child paths contained in the folder, sorted using
            the directory-before-files key and lowercase basename
            ordering.
    """
    return sorted(folder.iterdir(), key=_path_tree_sort_key)


def _document_directory_prefix(doc_rel_path: str) -> str:
    """
    Compute the directory prefix for a document-relative path.

    This helper normalizes slashes, drops the filename, and returns a
    trailing-slash directory prefix suitable for resolving sibling
    assets or links.

    Args:
        doc_rel_path (str):
            Document path expressed relative to some root, which may use
            either backslashes or forward slashes as separators.

    Returns:
        str:
            A directory prefix ending with "/" when the document has a
            parent folder, or an empty string if the document resides
            at the root.
    """
    parent: Path = Path(_normalize_slashes(path=doc_rel_path)).parent
    doc_dir: str = parent.as_posix()
    return f"{doc_dir}/" if doc_dir else ""


def _normpath_posix(relative_path: str) -> str:
    """
    Normalize a relative path to a POSIX-style form.

    This helper collapses redundant segments using the OS path rules and
    then converts any backslashes to forward slashes for consistent
    internal handling.

    Args:
        relative_path (str):
            Path string expressed relative to some root, which may
            contain dot segments, mixed separators, or
            platform-specific slashes.

    Returns:
        str:
            A normalized path with redundant components removed and all
            separators represented as forward slashes.
    """
    return os.path.normpath(relative_path).replace("\\", "/")


def _replace_match_second_group(match: re.Match[str], new_value: str) -> str:
    full: str = match.group(0)
    rel_start: int = match.start(2) - match.start(0)
    rel_end: int = match.end(2) - match.start(0)
    return full[:rel_start] + new_value + full[rel_end:]


def _img_src_replacement(
    match: re.Match[str],
    *,
    doc_prefix: str,
    root_param: str,
) -> str:
    """
    Replace the second capturing group of a regex match with new text.

    This helper rebuilds the full matched string by splicing in a
    supplied replacement value only where the second group originally
    appeared.

    Args:
        match (re.Match[str]):
            Regular expression match object whose full text and second
            capturing group boundaries will be used to construct the
            replacement.
        doc_prefix (str):
            Directory prefix to prepend to the source path.
        root_param (str):
            Query parameter to append to the source path.

    Returns:
        str:
            The original full match string with its second capturing
            group segment replaced by the provided new value.
    """
    full: str = match.group(0)
    src: str | Any = (match.group(2) or "").strip()
    if not src or src.startswith(_IMG_REMOTE_PREFIXES):
        return full
    resolved: str = _normpath_posix(relative_path=doc_prefix + src)
    if resolved.startswith(".."):
        return full
    quoted: str = urllib.parse.quote(resolved, safe="/")
    new_src: str = f"/view/{quoted}?{root_param}"
    return _replace_match_second_group(match, new_value=new_src)


def _markdown_href_should_pass_through(href_lower: str) -> bool:
    """
    Determine whether a Markdown hyperlink should bypass rewriting.

    This helper checks the lowercased href value against a set of known
    prefixes (external URLs, mail links, in-page anchors, and internal
    viewer routes) that must be left untouched.

    Args:
        href_lower (str):
            Hyperlink target string already converted to lowercase, such
            as the contents of an href attribute.

    Returns:
        bool:
            True if the href starts with any configured skip prefix and
            should not be rewritten, otherwise False.
    """
    return href_lower.startswith(_LINK_SKIP_PREFIXES)


def _a_href_replacement(
    match: re.Match[str],
    *,
    doc_prefix: str,
    root_param: str,
) -> str:
    """
    Rewrite an HTML anchor href to point at an internal Markdown viewer
    route.

    This helper inspects the original href, skips external or
    non-Markdown targets, and, when appropriate, converts a relative
    Markdown link into a normalized /view/ URL with query and fragment
    preserved.

    Args:
        match (re.Match[str]):
            Regular expression match object for an <a> tag whose href
            attribute may need to be rewritten.
        doc_prefix (str):
            Directory prefix derived from the current document path that
            is prepended to the href target before normalization.
        root_param (str):
            Query parameter string to append to rewritten viewer URLs,
            used to identify the current project or root.

    Returns:
        str:
            The original anchor tag when the href should pass through
            unchanged, or the same tag with its href attribute replaced
            by a computed internal viewer URL.
    """
    full: str = match.group(0)
    href: str | Any = (match.group(2) or "").strip()
    if not href:
        return full
    if _markdown_href_should_pass_through(href_lower=href.lower()):
        return full
    target, hash_part = href, ""
    if "#" in href:
        target, fragment = href.split("#", 1)
        hash_part: str = f"#{fragment}"
    if not target.lower().endswith(".md"):
        return full
    resolved: str = _normpath_posix(relative_path=doc_prefix + target)
    if resolved.startswith(".."):
        return full
    quoted: str = urllib.parse.quote(resolved, safe="/")
    new_href: str = f"/view/{quoted}?{root_param}{hash_part}"
    return _replace_match_second_group(match, new_value=new_href)


def build_tree(folder: Path, base: str, root_param: str = "") -> str:
    """
    Render an HTML navigation tree for a folder hierarchy of Markdown
    documents.

    This function walks the directory structure, skipping system and
    hidden entries, and produces a nested <ul> list where folders and
    .md files are linked to the internal viewer route.

    Args:
        folder (Path):
            Root filesystem directory whose Markdown contents and
            subdirectories should be rendered into a tree.
        base (str):
            Base relative path prefix that is combined with each item's
            name to form the document-relative URL path.
        root_param (str):
            Optional query parameter string appended to generated
            view URLs to identify the current project or root context.

    Returns:
        str:
            An HTML string containing a <ul class='tree'> element with
            nested <li> entries for folders and Markdown files, or an
            empty string if the folder is not a directory.
    """
    if not folder.is_dir():
        return ""
    items: list[Path] = _sorted_directory_entries(folder)
    query_suffix: str = f"?{root_param}" if root_param else ""
    lines: list[str] = ["<ul class='tree'>"]
    for item in items:
        if _is_skipped_entry(item.name):
            continue
        rel: str = (base + "/" + item.name).lstrip("/")
        if item.is_dir():
            subtree: str = build_tree(
                folder=item, base=rel, root_param=root_param
            )
            lines.append(
                f'<li class="folder-item"><span class="folder collapsed" data-toggle>'
                f'<span class="icon">▸</span>{html.escape(item.name)}</span>{subtree}</li>'
            )
        elif item.suffix.lower() == ".md":
            url: str = (
                "/view/" + urllib.parse.quote(rel, safe="/") + query_suffix
            )
            lines.append(
                f'<li><a href="{url}" target="content">{html.escape(item.name)}</a></li>'
            )
    lines.append("</ul>")
    return "\n".join(lines)


def collect_md_files(folder: Path, root: Path) -> list[Path]:
    """
    Collect Markdown files under a folder as paths relative to a root.

    This helper walks the directory tree depth-first, skipping hidden or
    system entries, and returns only .md files normalized relative to
    the given root directory.

    Args:
        folder (Path):
            Filesystem directory that serves as the starting point for
            the recursive search for Markdown files.
        root (Path):
            Root directory whose path will be stripped from discovered
            Markdown file paths so results are returned as relative
            paths.

    Returns:
        list[Path]:
            A list of relative paths for all .md files found beneath the
            folder and its subdirectories, ordered according to the
            navigation-friendly sort key.
    """
    result: list[Path] = []
    items: list[Path] = _sorted_directory_entries(folder)
    for item in items:
        if _is_skipped_entry(item.name):
            continue
        if item.is_dir():
            result.extend(collect_md_files(folder=item, root=root))
        elif item.suffix.lower() == ".md":
            result.append(item.relative_to(root))
    return result


def get_prev_next(root: Path, rel_path: str) -> PrevNext:
    """
    Compute previous and next Markdown documents relative to a given
    file.

    This helper orders all Markdown files under a root, locates the
    current document, and returns the neighboring paths while skipping
    index pages when moving forward.

    Args:
        root (Path):
            Root directory of the Markdown collection whose files define
            the global navigation order.
        rel_path (str):
            Path to the current document expressed relative to the root,
            which may contain platform-specific separators.

    Returns:
        PrevNext:
            A tuple of two strings (prev, next) where each element is
            the POSIX-style relative path to the previous or next
            document, or None if there is no neighbor in that direction
            or the current document is not found.
    """
    ordered: list[Path] = collect_md_files(folder=root, root=root)
    rel: Path = Path(_normalize_slashes(path=rel_path))
    idx: int | None = next(
        (i for i, p in enumerate[Path](ordered) if p == rel), None
    )
    if idx is None:
        return None, None
    prev_rel: Path | None = ordered[idx - 1] if idx > 0 else None
    next_rel: Path | None = (
        ordered[idx + 1] if idx + 1 < len(ordered) else None
    )
    if next_rel is not None and next_rel.name.lower() == "index.md":
        next_rel = ordered[idx + 2] if idx + 2 < len(ordered) else None
    return (
        prev_rel.as_posix() if prev_rel is not None else None,
        next_rel.as_posix() if next_rel is not None else None,
    )


def rewrite_document_asset_urls(
    html_body: str, doc_rel_path: str, root_param: str
) -> str:
    """
    Rewrite image source URLs in an HTML document to internal viewer
    routes.

    This helper resolves relative image paths against the current
    document location and converts local assets into /view/ URLs tagged
    with a project or root identifier.

    Args:
        html_body (str):
            HTML fragment or full document whose <img> tags should be
            scanned for source URLs to rewrite.
        doc_rel_path (str):
            Path to the current document relative to the project root,
            used to compute a directory prefix for resolving image
            paths.
        root_param (str):
            Query parameter string appended to rewritten image URLs to
            identify the active project or root context.

    Returns:
        str:
            The HTML body with eligible <img src="..."> attributes
            rewritten to internal viewer routes, leaving remote or
            out-of-scope sources unchanged.
    """
    doc_prefix: str = _document_directory_prefix(doc_rel_path)
    replacer: partial[str] = partial[str](
        _img_src_replacement,
        doc_prefix=doc_prefix,
        root_param=root_param,
    )
    return _IMG_SRC_PATTERN.sub(replacer, html_body)


def rewrite_document_markdown_links(
    html_body: str, doc_rel_path: str, root_param: str
) -> str:
    """
    Rewrite Markdown document links in an HTML fragment to internal
    viewer routes.

    This helper resolves relative href targets against the current
    document location and converts local .md links into /view/ URLs
    tagged with a project or root identifier.

    Args:
        html_body (str):
            HTML fragment or full document whose <a href="..."> links
            should be scanned for Markdown targets to rewrite.
        doc_rel_path (str):
            Path to the current document relative to the project root,
            used to compute a directory prefix for resolving link
            targets.
        root_param (str):
            Query parameter string appended to rewritten viewer URLs to
            identify the active project or root context.

    Returns:
        str:
            The HTML body with eligible Markdown href attributes
            rewritten to internal viewer routes, leaving external or
            non-Markdown links unchanged.
    """
    doc_prefix: str = _document_directory_prefix(doc_rel_path)
    replacer: partial[str] = partial[str](
        _a_href_replacement,
        doc_prefix=doc_prefix,
        root_param=root_param,
    )
    return _A_HREF_PATTERN.sub(replacer, html_body)
