#!/usr/bin/env python3
"""Stop private details from being committed to this public repository.

The list of private terms lives outside the repository. It is a text file of
case-insensitive extended regular expressions, one per line, where blank lines
and lines starting with ``#`` are ignored. The script reads it from
``--terms FILE`` or from the ``ACP_PRIVATE_TERMS`` environment variable, which
holds the list's text (this is how CI gets it, from a repository secret).

Modes:

    --diff [BASE..HEAD]   added lines in a range (default: origin/main...HEAD,
                          which diffs against the merge base)
    --staged              added lines in the staged changes (pre-commit hook)
    --messages BASE..HEAD commit messages, authors and committers in a range
    --message-file FILE   one commit message (commit-msg hook)
    --all                 every tracked file in the working tree

A hit prints ``file:line: private term (rule N)`` and nothing else: never the
term and never the matched text, so CI logs stay clean. Rule N counts the
rules in the list from 1, skipping blanks and comments.

Exit codes: 0 clean, 1 at least one hit, 2 usage or git error. With no list
set the script warns and exits 0, so forks and Dependabot runs are not blocked.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ENV_VAR = "ACP_PRIVATE_TERMS"
DEFAULT_RANGE = "origin/main...HEAD"
SCISSORS = "# ------------------------ >8 ------------------------"


def parse_terms(text: str) -> list[tuple[int, re.Pattern[str]]]:
    rules: list[tuple[int, re.Pattern[str]]] = []
    n = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        n += 1
        try:
            rules.append((n, re.compile(line, re.IGNORECASE)))
        except re.error:
            # Name the rule number only; the pattern itself is private.
            print(f"check_private_terms: rule {n} is not a valid regular expression", file=sys.stderr)
            raise SystemExit(2)
    return rules


def load_terms(terms_file: str | None) -> list[tuple[int, re.Pattern[str]]] | None:
    if terms_file:
        path = Path(terms_file).expanduser()
        if not path.is_file():
            print(f"check_private_terms: term list {terms_file} not found", file=sys.stderr)
            raise SystemExit(2)
        text = path.read_text(encoding="utf-8")
    else:
        text = os.environ.get(ENV_VAR, "")
    rules = parse_terms(text)
    return rules or None


def first_hit(rules, text: str) -> int | None:
    for n, pattern in rules:
        if pattern.search(text):
            return n
    return None


def git(*args: str, cwd: str | None = None) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=cwd,
        capture_output=True,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        print(f"check_private_terms: git {args[0]} failed: {err}", file=sys.stderr)
        raise SystemExit(2)
    return proc.stdout.decode("utf-8", "replace")


def _diff_path(header: str) -> str:
    path = header[len("+++ "):].rstrip("\n")
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    return path[2:] if path.startswith("b/") else path


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def scan_diff(rules, diff_text: str) -> list[str]:
    """Check only the added lines of a unified diff, plus new file names."""
    hits: list[str] = []
    path = None
    new_path = False
    lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            path, new_path, lineno = None, False, 0
        elif line.startswith("new file mode") or line.startswith("rename to "):
            new_path = True
        elif line.startswith("+++ "):
            if line.startswith("+++ /dev/null"):
                path = None
                continue
            path = _diff_path(line)
            if new_path:
                n = first_hit(rules, path)
                if n is not None:
                    hits.append(f"{path}:0: private term in file name (rule {n})")
        elif line.startswith("@@"):
            m = _HUNK.match(line)
            lineno = int(m.group(1)) if m else 0
        elif path is not None and line.startswith("+"):
            n = first_hit(rules, line[1:])
            if n is not None:
                hits.append(f"{path}:{lineno}: private term (rule {n})")
            lineno += 1
        elif path is not None and line.startswith(" "):
            lineno += 1
    return hits


DIFF_ARGS = ("diff", "--no-color", "--no-ext-diff", "--unified=0", "--find-renames")


def scan_range(rules, rng: str) -> list[str]:
    return scan_diff(rules, git(*DIFF_ARGS, rng))


def scan_staged(rules) -> list[str]:
    return scan_diff(rules, git(*DIFF_ARGS, "--cached"))


def _message_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        if line.startswith(SCISSORS):
            break
        if line.startswith("#"):
            continue
        out.append(line)
    return out


def scan_message_text(rules, text: str, label: str) -> list[str]:
    hits = []
    for i, line in enumerate(_message_lines(text), start=1):
        n = first_hit(rules, line)
        if n is not None:
            hits.append(f"{label}:{i}: private term (rule {n})")
    return hits


def scan_messages(rules, rng: str) -> list[str]:
    log = git("log", "--format=%H%x00%an <%ae>%x00%cn <%ce>%x00%B%x1e", rng)
    hits = []
    for record in log.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, author, committer, body = record.split("\x00", 3)
        label = f"commit {sha[:12]}"
        for name, value in (("author", author), ("committer", committer)):
            n = first_hit(rules, value)
            if n is not None:
                hits.append(f"{label}:{name}: private term (rule {n})")
        hits.extend(scan_message_text(rules, body, label))
    return hits


def scan_all(rules) -> list[str]:
    root = git("rev-parse", "--show-toplevel").strip()
    hits = []
    for rel in git("ls-files", "-z", cwd=root).split("\x00"):
        if not rel:
            continue
        n = first_hit(rules, rel)
        if n is not None:
            hits.append(f"{rel}:0: private term in file name (rule {n})")
        try:
            data = (Path(root) / rel).read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue  # binary
        for i, line in enumerate(data.decode("utf-8", "replace").splitlines(), start=1):
            n = first_hit(rules, line)
            if n is not None:
                hits.append(f"{rel}:{i}: private term (rule {n})")
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--terms", metavar="FILE", help=f"term list file (default: ${ENV_VAR} text)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--diff", nargs="?", const=DEFAULT_RANGE, metavar="BASE..HEAD")
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--messages", metavar="BASE..HEAD")
    mode.add_argument("--message-file", metavar="FILE")
    mode.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)

    rules = load_terms(args.terms)
    if rules is None:
        print(
            f"check_private_terms: warning: no private term list set (--terms or ${ENV_VAR}); skipping check",
            file=sys.stderr,
        )
        return 0

    if args.staged:
        hits = scan_staged(rules)
    elif args.messages:
        hits = scan_messages(rules, args.messages)
    elif args.message_file:
        text = Path(args.message_file).read_text(encoding="utf-8", errors="replace")
        hits = scan_message_text(rules, text, "commit message")
    elif args.all:
        hits = scan_all(rules)
    else:
        hits = scan_range(rules, args.diff or DEFAULT_RANGE)

    for hit in hits:
        print(hit)
    if hits:
        print(
            f"check_private_terms: {len(hits)} hit(s). Replace them with a generic placeholder; see CLAUDE.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
