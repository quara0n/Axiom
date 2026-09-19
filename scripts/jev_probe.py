"""Ask JEV a set of questions whose answers are already known.

This is a measurement, not a feature. It takes one finished Axiom workspace, asks
the questions from the task's own brief, and compares the answers with what the
record says actually happened. Ground truth for the sample workspace is
``research/VARG-I-HAGEN-HANDOFF.md``.

The same file probes any JEV-shaped endpoint, which is how the wiring is tested
before a TypeSafe key exists:

  free SimpleJev demo   --base-url https://simple-jev-demo-api.featherless.ai/v1
                        --model featherless-ai/Qwen3.8-27B-classifier
  TypeSafe              TYPESAFE_API_KEY in the environment; defaults apply
  a local server        --base-url http://127.0.0.1:8000/v1 --model Qwen/Qwen3.5-0.8B

Nothing here writes to the workspace and no answer is acted on.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import Settings  # noqa: E402
from backend.jev import Jev, JevError, choice, noul  # noqa: E402

# Read in this order. Whatever does not fit the budget is recorded as omitted, and
# questions that depend on it are reported as unscored instead of guessed at.
PRIORITY = ["styles.css", "index.html", "package.json", "AGENTS.md",
            "tests/game.test.js", "src/world.js", "src/movement.js",
            "src/hud.js", "src/main.js", "src/render3d.js"]

BRIEF = [
    "A 3D game in the browser. The player is Varg, a four-year-old boy, who walks "
    "around his garden and finds five toys that belong in the toy box.",
    "A four-year-old plays it alone. Big controls, no losing state, and the "
    "character must always be visible on screen.",
    "No dependencies, no CDN, no network calls. The page opens from file://.",
    "Classic script tags in order. No import or export statements.",
]

# id, question, expected answer, and the file that actually answers it.
QUESTIONS = [
    ("control_row_place",
     choice("Where does the on-screen control row end up on a screen of normal height?",
            {"pinned": "Held against the bottom edge, with the character drawn above it.",
             "distributed": "Placed by the flex layout between the top bar and the help "
                            "text, so it sits near the middle of the screen.",
             "offscreen": "Pushed out of the visible area."}),
     "distributed", "styles.css"),
    ("buttons_can_cover_character",
     noul("Do the layout rules leave the game character and the control buttons "
          "competing for the lower half of the screen, so the buttons can cover the character?"),
     True, "styles.css"),
    ("margin_top_auto_would_pin",
     noul("Would adding margin-top: auto to the control row move it to the bottom "
          "of the screen?"),
     True, "styles.css"),
    ("scripts_are_local",
     noul("Are all five scripts loaded from files inside the project folder rather "
          "than from a network address?"),
     True, "index.html"),
    ("has_package_manifest",
     noul("Does the project folder contain a Node package manifest (package.json)?"),
     True, "package.json"),
    ("manifest_has_dependencies",
     noul("Does that manifest declare any third-party dependency?"),
     False, "package.json"),
    ("test_covers_visibility",
     noul("Does the project's test file check that the character stays fully visible "
          "above the control row?"),
     False, "tests/game.test.js"),
    ("player_can_lose",
     noul("Is there any way for the player to lose the game?"),
     False, "src/main.js"),
]


def build_state(workspace, budget):
    """Pack the brief, a file inventory and the highest-priority files into a state."""
    files, omitted, used = {}, [], 0
    for name in PRIORITY:
        path = workspace / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if used + len(text) > budget:
            omitted.append(name)
            continue
        files[name] = text
        used += len(text)
    inventory = sorted(
        ({"path": str(p.relative_to(workspace)).replace("\\", "/"), "bytes": p.stat().st_size}
         for p in workspace.rglob("*") if p.is_file()),
        key=lambda item: item["path"],
    )
    state = {"task_brief": BRIEF, "files": files, "file_inventory": inventory}
    if omitted:
        state["files_not_included"] = omitted
    return state, set(files)


def verdict(answer, expected):
    """A noul is scored on which side of 0.5 it falls; a choice on the option."""
    if answer.get("type") == "noul":
        value = answer.get("noul")
        return (value >= 0.5) == expected, value
    return answer.get("choice") == expected, answer.get("choice")


async def main():
    parser = argparse.ArgumentParser(description="Probe JEV with known answers")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--budget", type=int, default=8000,
                        help="characters of workspace content to send as state")
    parser.add_argument("--ask-unscored", action="store_true",
                        help="also ask questions whose evidence was not included, to see "
                             "whether the model abstains or invents an answer")
    args = parser.parse_args()

    config = Settings.from_env()
    jev = Jev(api_key=args.api_key if args.api_key is not None else config.typesafe_api_key,
              base_url=args.base_url or config.jev_base_url,
              model=args.model or config.jev_model,
              request_timeout=config.jev_request_timeout,
              max_attempts=config.jev_max_attempts)
    state, included = build_state(args.workspace, args.budget)
    questions = {name: question for name, question, _, _ in QUESTIONS}
    print(f"endpoint {jev.base_url}  model {jev.model}")
    print(f"state: {len(included)} files, {len(str(state))} characters, "
          f"omitted: {state.get('files_not_included') or 'none'}\n")
    try:
        result = await jev.system_one(state, questions)
    except JevError as exc:
        print(f"FAILED: {exc}")
        return 1
    finally:
        await jev.close()

    scored = correct = 0
    for name, _, expected, evidence in QUESTIONS:
        answer = result["answers"].get(name)
        missing_evidence = evidence not in included
        if missing_evidence and not args.ask_unscored:
            print(f"  skip    {name:<28} evidence {evidence} was not in the state")
            continue
        if answer is None:
            print(f"  MISSING {name:<28} no answer returned")
            continue
        hit, shown = verdict(answer, expected)
        if missing_evidence:
            # The expected answer is known, but no evidence for it was sent, so this
            # measures abstention rather than accuracy and is never counted as a hit.
            print(f"  no evidence  {name:<24} nothing sent; answered {shown!r}")
            continue
        scored += 1
        correct += hit
        print(f"  {'hit ' if hit else 'MISS'}    {name:<28} expected {expected!r:<15} got {shown!r}")
    print(f"\nscored {correct}/{scored}; usage {result['usage']}; "
          f"{result['latency_ms']} ms; answered by {result['resolved_model']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
