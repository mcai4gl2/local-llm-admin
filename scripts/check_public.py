#!/usr/bin/env python3
"""Fail when a denylisted pattern appears in the tree or its git history.

The patterns (one regex per line, case-insensitive; blank and ``#`` lines skipped) are
private, so none are embedded here. They come from a file, an environment variable (a CI
secret) or stdin. Hits are reported as location plus pattern number only: the matched
text is never printed, so CI logs stay clean.

Usage:
  check_public.py --patterns-file F [--optional] PATH...      files and directories
  check_public.py --patterns-env VAR --git REPO [--commits RANGE]
      tracked files of REPO; with --commits, also every commit message, author and
      committer identity and added line in RANGE (a git rev-list range, e.g. HEAD)

Exit status: 0 clean, 1 hits, 2 no patterns or a usage error.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path

SKIP_DIRS = frozenset(
    {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "node_modules"}
)


class PatternError(ValueError):
    """A pattern line does not compile (the message names the line, never the text)."""


def load_patterns(text: str) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line, re.IGNORECASE))
        except re.error:
            raise PatternError(f"pattern on line {n} does not compile") from None
    return patterns


def _hits(text: str, patterns: Sequence[re.Pattern[str]]) -> Iterator[tuple[int, int]]:
    """(line number, pattern number), both 1-based."""
    for n, line in enumerate(text.splitlines(), 1):
        for k, p in enumerate(patterns, 1):
            if p.search(line):
                yield n, k


def _mask(text: str, patterns: Sequence[re.Pattern[str]]) -> str:
    """A path for the report, with every match blanked: paths can be hits too."""
    for p in patterns:
        text = p.sub("***", text)
    return text


def _read(path: Path) -> str:
    # latin-1 maps every byte, so binary files are scanned too
    return path.read_bytes().decode("latin-1")


def _walk(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_file():
            yield path
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                yield Path(dirpath) / name


def scan_files(
    files: Iterable[Path], patterns: Sequence[re.Pattern[str]], base: Path | None
) -> list[str]:
    report: list[str] = []
    for f in files:
        name = str(f.relative_to(base)) if base and f.is_relative_to(base) else str(f)
        shown = _mask(name, patterns)
        for k, p in enumerate(patterns, 1):
            if p.search(name):
                report.append(f"{shown} <path>: pattern #{k}")
        report.extend(f"{shown}:{n}: pattern #{k}" for n, k in _hits(_read(f), patterns))
    return report


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout
    return out.decode("utf-8", "replace")


def git_tracked(repo: Path) -> list[Path]:
    names = _git(repo, "ls-files", "-z").split("\0")
    return [repo / n for n in names if n and (repo / n).is_file()]


def scan_commits(repo: Path, rev_range: str, patterns: Sequence[re.Pattern[str]]) -> list[str]:
    report: list[str] = []
    for sha in _git(repo, "rev-list", rev_range).split():
        short = sha[:7]
        who = _git(repo, "show", "-s", "--format=%an <%ae>%n%cn <%ce>", sha)
        report.extend(
            f"commit {short} author/committer: pattern #{k}" for _, k in _hits(who, patterns)
        )
        message = _git(repo, "show", "-s", "--format=%B", sha)
        report.extend(
            f"commit {short} message line {n}: pattern #{k}" for n, k in _hits(message, patterns)
        )
        diff = _git(repo, "show", "--format=", "--no-color", "--unified=0", "--no-renames", sha)
        current = "?"
        for line in diff.splitlines():
            if line.startswith("+++ "):
                path = line[6:] if line.startswith("+++ b/") else line[4:]
                current = _mask(path, patterns)
                for k, p in enumerate(patterns, 1):
                    if p.search(path):
                        report.append(f"commit {short} <path>: pattern #{k}")
            elif line.startswith("+"):
                report.extend(
                    f"commit {short} {current} (added line): pattern #{k}"
                    for _, k in _hits(line[1:], patterns)
                )
    return report


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--patterns-file", type=Path)
    src.add_argument("--patterns-env", metavar="VAR")
    src.add_argument("--patterns-stdin", action="store_true")
    ap.add_argument(
        "--optional", action="store_true", help="pass (with a notice) if the file is absent"
    )
    ap.add_argument("--git", type=Path, metavar="REPO", help="scan the tracked files of REPO")
    ap.add_argument("--commits", metavar="RANGE", help="with --git: also scan this history")
    ap.add_argument("paths", nargs="*", type=Path)
    return ap


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin_text: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    env = os.environ if environ is None else environ
    if args.patterns_file is not None:
        if not args.patterns_file.is_file():
            if args.optional:
                print("check_public: no patterns file; skipped")
                return 0
            print("check_public: patterns file not found", file=sys.stderr)
            return 2
        text = args.patterns_file.read_text()
    elif args.patterns_env is not None:
        text = env.get(args.patterns_env, "")
    else:
        text = sys.stdin.read() if stdin_text is None else stdin_text
    try:
        patterns = load_patterns(text)
    except PatternError as e:
        print(f"check_public: {e}", file=sys.stderr)
        return 2
    if not patterns:
        print("check_public: no patterns to check against", file=sys.stderr)
        return 2
    if args.commits and not args.git:
        print("check_public: --commits needs --git REPO", file=sys.stderr)
        return 2

    report: list[str] = []
    if args.git:
        repo = args.git.resolve()
        report += scan_files(git_tracked(repo), patterns, repo)
        if args.commits:
            report += scan_commits(repo, args.commits, patterns)
    if args.paths:
        roots = [p.resolve() for p in args.paths]
        base = roots[0] if len(roots) == 1 and roots[0].is_dir() else None
        report += scan_files(_walk(roots), patterns, base)

    for line in report:
        print(line)
    print(f"check_public: {len(report)} hits ({len(patterns)} patterns)")
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main())
