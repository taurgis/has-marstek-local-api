#!/usr/bin/env python3
"""Enforce per-file and per-function size limits on the Python sources.

Ruff has no rule for module length, and its closest function-size rule
(``PLR0915``) counts statements rather than lines, so neither limit can be
expressed in ``pyproject.toml``'s ruff section. This script fills that gap
with the stdlib ``ast`` module so it keeps working on whatever Python the
integration targets next.

Limits live under ``[tool.code-limits]`` in ``pyproject.toml``::

    [tool.code-limits]
    max-file-lines = 1000
    max-function-lines = 120
    paths = ["custom_components", "tests", "tools", "scripts"]
    exclude = ["tools/firmware/blobs"]

Run it from the repository root::

    python scripts/check_code_limits.py

Exits non-zero and prints one ``path:line: message`` per violation.
"""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_FILE_LINES = 1000
DEFAULT_MAX_FUNCTION_LINES = 120
DEFAULT_PATHS = ("custom_components", "tests", "tools", "scripts")

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, slots=True)
class Limits:
    """Resolved configuration for one run."""

    max_file_lines: int
    max_function_lines: int
    paths: tuple[str, ...]
    exclude: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Violation:
    """A single limit breach, rendered as a ``path:line: message`` line."""

    path: Path
    line: int
    message: str

    def render(self, root: Path) -> str:
        """Return the violation as a repo-relative compiler-style line."""
        try:
            relative = self.path.relative_to(root)
        except ValueError:
            relative = self.path
        return f"{relative}:{self.line}: {self.message}"


def load_limits(root: Path, overrides: argparse.Namespace) -> Limits:
    """Read ``[tool.code-limits]`` from ``pyproject.toml``, applying overrides."""
    config: dict[str, object] = {}
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        with pyproject.open("rb") as handle:
            parsed = tomllib.load(handle)
        tool = parsed.get("tool")
        if isinstance(tool, dict):
            section = tool.get("code-limits")
            if isinstance(section, dict):
                config = section

    paths = overrides.paths or _str_tuple(config.get("paths"), DEFAULT_PATHS)
    return Limits(
        max_file_lines=overrides.max_file_lines
        or _positive_int(config.get("max-file-lines"), DEFAULT_MAX_FILE_LINES),
        max_function_lines=overrides.max_function_lines
        or _positive_int(
            config.get("max-function-lines"), DEFAULT_MAX_FUNCTION_LINES
        ),
        paths=tuple(paths),
        exclude=_str_tuple(config.get("exclude"), ()),
    )


def _positive_int(value: object, fallback: int) -> int:
    """Return ``value`` when it is a positive int, else ``fallback``."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return fallback


def _str_tuple(value: object, fallback: Sequence[str]) -> tuple[str, ...]:
    """Return ``value`` as a tuple of strings, else ``fallback``."""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(str(item) for item in value)
    return tuple(fallback)


def iter_python_files(root: Path, limits: Limits) -> Iterator[Path]:
    """Yield every Python file under the configured paths, sorted and deduped."""
    excluded = tuple((root / item).resolve() for item in limits.exclude)
    seen: set[Path] = set()
    for entry in limits.paths:
        target = (root / entry).resolve()
        if not target.exists():
            continue
        candidates = [target] if target.is_file() else sorted(target.rglob("*.py"))
        for candidate in candidates:
            if candidate.suffix != ".py" or candidate in seen:
                continue
            if any(_is_within(candidate, parent) for parent in excluded):
                continue
            seen.add(candidate)
            yield candidate


def _is_within(candidate: Path, parent: Path) -> bool:
    """Return True when ``candidate`` is ``parent`` or sits below it."""
    return candidate == parent or parent in candidate.parents


def check_file(path: Path, limits: Limits) -> list[Violation]:
    """Return every limit breach in one file."""
    source = path.read_text(encoding="utf-8")
    violations: list[Violation] = []

    line_count = len(source.splitlines())
    if line_count > limits.max_file_lines:
        violations.append(
            Violation(
                path,
                line_count,
                f"file has {line_count} lines, limit is {limits.max_file_lines}; "
                "split it into focused modules",
            )
        )

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as err:
        violations.append(Violation(path, err.lineno or 1, f"syntax error: {err.msg}"))
        return violations

    violations.extend(_check_functions(path, tree, limits))
    return violations


def _check_functions(
    path: Path, tree: ast.Module, limits: Limits
) -> Iterator[Violation]:
    """Yield a violation for every over-long function body in ``tree``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        length = _function_length(node)
        if length > limits.max_function_lines:
            yield Violation(
                path,
                node.lineno,
                f"function '{node.name}' spans {length} lines, limit is "
                f"{limits.max_function_lines}; extract a helper",
            )


def _function_length(node: FunctionNode) -> int:
    """Return the line span of a function, decorators excluded."""
    end = node.end_lineno or node.lineno
    return end - node.lineno + 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths",
        nargs="*",
        help="files or directories to check (defaults to the configured paths)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root holding pyproject.toml (default: current directory)",
    )
    parser.add_argument(
        "--max-file-lines",
        type=int,
        default=0,
        help="override the configured per-file line limit",
    )
    parser.add_argument(
        "--max-function-lines",
        type=int,
        default=0,
        help="override the configured per-function line limit",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Check every selected file and report the violations."""
    args = parse_args(argv)
    root = args.root.resolve()
    limits = load_limits(root, args)

    violations: list[Violation] = []
    checked = 0
    for path in iter_python_files(root, limits):
        checked += 1
        violations.extend(check_file(path, limits))

    if not violations:
        print(
            f"check_code_limits: {checked} files within "
            f"{limits.max_file_lines} lines/file and "
            f"{limits.max_function_lines} lines/function"
        )
        return 0

    for violation in sorted(violations, key=lambda item: (str(item.path), item.line)):
        print(violation.render(root))
    print(
        f"\ncheck_code_limits: {len(violations)} violation(s) across {checked} files",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
