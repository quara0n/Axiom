#!/usr/bin/env python3
"""Hidden checker for build-a-cli.

Runs the artefact the run produced against fixtures the run never saw. Reading the
source would only prove that a description exists.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run(workspace: Path, arguments):
    return subprocess.run([sys.executable, "wordcount.py", *arguments],
                          cwd=str(workspace), capture_output=True, text=True, timeout=60)


def counted(workspace: Path, payload: str):
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "sample.txt"
        target.write_text(payload, encoding="utf-8")
        completed = run(workspace, [str(target)])
    if completed.returncode != 0:
        raise AssertionError(f"exit {completed.returncode}: {completed.stderr.strip()[:200]}")
    return json.loads(completed.stdout)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: checker.py <workspace>")
        return 2
    workspace = Path(sys.argv[1]).resolve()
    if not (workspace / "wordcount.py").is_file():
        print("wordcount.py was not written")
        print("SCORE: 0/8")
        return 1

    results = []

    def check(name, fn):
        try:
            results.append((name, fn() is True, ""))
        except Exception as exc:  # a failing assertion is a score, not a crash
            results.append((name, False, f"{type(exc).__name__}: {exc}"[:160]))

    def missing_file_is_an_error():
        completed = run(workspace, [str(workspace / "definitely-not-here.txt")])
        return completed.returncode != 0 and bool(completed.stderr.strip())

    check("a missing file exits non-zero", missing_file_is_an_error)
    check("basic counts", lambda: counted(workspace, "hello world\n")
          == {"lines": 1, "words": 2, "characters": 12})
    check("two lines", lambda: counted(workspace, "a\nb\n")
          == {"lines": 2, "words": 2, "characters": 4})
    check("a final line without a newline still counts",
          lambda: counted(workspace, "a\nb")["lines"] == 2)
    check("an empty file is all zeros", lambda: counted(workspace, "")
          == {"lines": 0, "words": 0, "characters": 0})
    check("runs of whitespace do not add words",
          lambda: counted(workspace, "  a   b  ")["words"] == 2)
    check("characters are decoded, not bytes",
          lambda: counted(workspace, "p\u00e5\n")["characters"] == 3)
    check("stdout is exactly one JSON object", lambda: set(
        counted(workspace, "x\n").keys()) == {"lines", "words", "characters"})

    passed = 0
    for name, ok, detail in results:
        print(f"{'pass' if ok else 'FAIL'}  {name}{f' ({detail})' if detail else ''}")
        passed += 1 if ok else 0
    print(f"SCORE: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
