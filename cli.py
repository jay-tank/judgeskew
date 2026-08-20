"""judgeskew CLI — static gate for biased LLM-as-judge evaluation setups.

Flags an eval harness whose methodology is biased: the same model both
generates and judges its own output (self-preference), or a judge call scores
with no rubric/reference (unanchored) or a nonzero temperature (nondeterministic).
The core is AST-based and never imports or runs the target code, so it is safe
to run over untrusted source in CI.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from typing import List, Optional

__version__ = "0.1.0"

from models import ScanResult
from render import render_result, to_json
from scanner import CODE_EXTS, scan_source

# Directories never worth scanning (vendored / build output / caches).
_SKIP_DIRS = {
    "node_modules", "vendor", "build", "dist", ".git", "__pycache__",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", "site-packages",
    "target", ".gradle",
}
_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB cap per file
_IGNORE_FILE = ".judgeskewignore"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="judgeskew",
        description="Static gate for biased LLM-as-judge evaluation setups. "
        "Flags self-preference bias — the same model (or family) used as both "
        "the generator and the judge of its own output (JS001) — and weak judge "
        "hygiene: no rubric/reference in the prompt, or a nonzero temperature "
        "(JS002). AST-based; no target code is ever executed.",
    )
    parser.add_argument(
        "paths", nargs="*",
        help="Files or directories to scan (directories are walked recursively).",
    )
    parser.add_argument("--strict", action="store_true",
                        help="Also fail (exit 1) on warnings (JS002), not just blockers.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Output machine-readable JSON.")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output.")
    parser.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                        help="Exclude paths matching GLOB (repeatable).")
    parser.add_argument("--version", action="version", version=f"judgeskew {__version__}")
    return parser


def _load_ignore_patterns(root: str) -> List[str]:
    """Read glob patterns from a .judgeskewignore in the current directory."""
    path = os.path.join(root, _IGNORE_FILE)
    patterns: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    except OSError:
        pass
    return patterns


def _is_ignored(path: str, patterns: List[str]) -> bool:
    norm = path.replace(os.sep, "/")
    base = os.path.basename(norm)
    for pat in patterns:
        if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(base, pat):
            return True
    return False


def _collect_files(paths: List[str], patterns: List[str]) -> List[str]:
    files: List[str] = []
    for path in paths:
        if os.path.isdir(path):
            for root, dirs, names in os.walk(path):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for name in names:
                    if os.path.splitext(name)[1].lower() in CODE_EXTS:
                        files.append(os.path.join(root, name))
        else:
            files.append(path)
    files = [f for f in files if not _is_ignored(f, patterns)]
    return sorted(dict.fromkeys(files))


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.paths:
        print("Error: no paths given. Pass one or more files or directories.", file=sys.stderr)
        return 2

    patterns = _load_ignore_patterns(os.getcwd()) + list(args.exclude)
    files = _collect_files(args.paths, patterns)
    if not files:
        print("Error: no scannable source files (.py) found.", file=sys.stderr)
        return 2

    result = ScanResult()
    for path in files:
        try:
            if os.path.getsize(path) > _MAX_BYTES:
                print(f"Warning: skipping {path}: larger than 2 MiB.", file=sys.stderr)
                continue
            with open(path, "r", encoding="utf-8") as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Warning: skipping {path}: {exc}", file=sys.stderr)
            continue
        try:
            findings = scan_source(source, path)
        except SyntaxError as exc:
            print(f"Warning: skipping {path}: could not parse ({exc.msg}).", file=sys.stderr)
            continue
        result.files_scanned += 1
        result.findings.extend(findings)

    if result.files_scanned == 0:
        print("Error: no readable source files could be scanned.", file=sys.stderr)
        return 2

    if args.as_json:
        print(to_json(result))
    else:
        print(render_result(result, color=not args.no_color))

    fail = bool(result.blockers) or (args.strict and bool(result.warnings))
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
