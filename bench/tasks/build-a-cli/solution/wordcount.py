#!/usr/bin/env python3
"""Count lines, words and characters in a UTF-8 text file."""

import json
import sys
from pathlib import Path


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: wordcount.py <path>", file=sys.stderr)
        return 2
    try:
        text = Path(argv[1]).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"could not read {argv[1]}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "characters": len(text),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
