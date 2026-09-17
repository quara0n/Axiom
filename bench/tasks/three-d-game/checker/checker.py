#!/usr/bin/env python3
"""Hidden checker for three-d-game.

Grades the deterministic simulation contract only. Visual and design quality is not
machine-graded anywhere in this project, and this checker does not pretend to.
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: checker.py <workspace>")
        return 2
    node = shutil.which("node")
    if node is None:
        print("node is not on PATH; the checker cannot run.")
        return 2
    completed = subprocess.run(
        [node, str(HERE / "hidden.test.mjs"), str(Path(sys.argv[1]).resolve())],
        capture_output=True, text=True, timeout=120)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
