#!/usr/bin/env python3
"""Fail when a Markdown fragment works on only one of GitHub and VitePress.

GitHub's heading slug drops symbols such as slashes, the multiplication
sign and runs of hyphens differently from VitePress. An explicit
``<a id="..."></a>`` is the fragment
both renderers keep. Relative links must target that id, or a heading whose
two slugs already agree.

https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#section-links
https://vitepress.dev/guide/markdown#header-anchors
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_ANCHOR = re.compile(r"""<a\b[^>]*\b(?:id|name)=["']([^"']+)["']""", re.IGNORECASE)
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS = re.compile(r"[*_`]+")
# Curly quotes and dashes are spelled with escapes so the source stays ASCII.
_VP_SPECIAL = re.compile(
    r"""[\s~`!@#$%^&*()\-_+=[\]{}|\\;:"'<>,.?/""" + "\u201c\u201d\u2018\u2019\u2013\u2014" + "]+"
)
_CONTROL = re.compile(r"[\u0000-\u001f]")


def vitepress_slug(text: str) -> str:
    """Return the slug markdown-it-anchor builds for a heading."""
    normalized = unicodedata.normalize("NFKD", text)
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    slug = _VP_SPECIAL.sub("-", _CONTROL.sub("", without_marks))
    slug = re.sub(r"-{2,}", "-", slug).strip("-").lower()
    if slug[:1].isdigit():
        return f"_{slug}"
    return slug


def github_slug(text: str) -> str:
    """Return the slug GitHub uses for a heading.

    Letters, marks, numbers, connector punctuation, hyphens and spaces stay.
    Everything else, including slashes and the multiplication sign, is
    removed, then spaces become hyphens. This matches github-slugger.
    """
    kept: list[str] = []
    for char in text.lower().strip():
        category = unicodedata.category(char)
        if char in "- " or category[0] in {"L", "M", "N"} or category == "Pc":
            kept.append(char)
    return "".join(kept).replace(" ", "-")


def _heading_text(raw: str) -> str:
    """Return the visible heading text, without Markdown emphasis or links."""
    without_suffix = re.sub(r"\s+\{#[^}]+\}\s*$", "", raw.strip())
    linked = _MD_LINK.sub(r"\1", without_suffix)
    return _EMPHASIS.sub("", linked).strip()


def _visible_markdown(source: str) -> str:
    """Return Markdown with fenced and inline code removed."""
    return _INLINE_CODE.sub("", _FENCE.sub("", source))


def _pages(root: Path) -> list[Path]:
    """Return the Markdown pages this check covers."""
    pages: list[Path] = []
    readme = root / "README.md"
    if readme.is_file():
        pages.append(readme)
    docs = root / "docs"
    if docs.is_dir():
        pages.extend(path for path in sorted(docs.rglob("*.md")) if path.is_file())
    return pages


def _targets(page: Path, visible: str) -> set[str]:
    """Return fragments that resolve the same way on GitHub and VitePress."""
    explicit = set(_ANCHOR.findall(visible))
    stable: set[str] = set()
    for _marks, raw in _HEADING.findall(visible):
        text = _heading_text(raw)
        github = github_slug(text)
        vitepress = vitepress_slug(text)
        if github == vitepress and github:
            stable.add(github)
    return explicit | stable


def _resolve(page: Path, href: str, root: Path) -> Path | None:
    """Return the local Markdown file an href points at, ignoring fragments."""
    path = href.split("#", 1)[0]
    if not path:
        return page
    if re.match(r"^[a-z][a-z0-9+.-]*:", path, re.IGNORECASE):
        return None
    candidate = (page.parent / path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.suffix == ".md" else None


def check_root(root: Path) -> list[str]:
    """Return one message per fragment that is not shared by both renderers."""
    pages = _pages(root)
    targets = {
        page.resolve(): _targets(page, _visible_markdown(page.read_text())) for page in pages
    }
    problems: list[str] = []
    for page in pages:
        visible = _visible_markdown(page.read_text())
        for href in _LINK.findall(visible):
            if href.startswith("!"):
                continue
            if "#" not in href:
                continue
            fragment = href.split("#", 1)[1]
            if not fragment:
                continue
            target = _resolve(page, href, root)
            if target is None or target not in targets:
                continue
            if fragment not in targets[target]:
                relative = page.relative_to(root)
                problems.append(f"{relative}: fragment #{fragment} in ({href}) is not stable")
    return problems


def main() -> int:
    """Check the repository Markdown and exit non-zero when a fragment breaks."""
    root = Path(__file__).resolve().parents[1]
    problems = check_root(root)
    if not problems:
        print(f"check_markdown_anchors: stable fragments in {len(_pages(root))} pages")
        return 0
    print("\n".join(problems), file=sys.stderr)
    print(
        f"\ncheck_markdown_anchors: {len(problems)} fragment(s) differ between "
        'GitHub and VitePress. Add <a id="..."></a> and link to that id.',
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
