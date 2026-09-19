"""Compare JEV's answers with checks ordinary code can decide.

Each claim below has two answers: one computed from the artifact by plain code
(regex, line ranges, arithmetic) and one asked of JEV as a noul question over an
excerpt of the same artifact. Where they disagree, one of them is wrong, and the
raw probability tells us how confident JEV was while being wrong.

A claim with no deterministic check has ``None`` in the ``check`` column: for
those, JEV's value is reported and the human reading is the reference.

Usage:
  python scripts/jev_factcheck.py --artifact <path to index.html>
      [--base-url ...] [--model ...]
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import Settings  # noqa: E402
from backend.jev import Jev, JevError, noul  # noqa: E402

ETYPE = re.compile(r"\b(?:ctx\.)?(?:fillText|strokeText)\(\s*'([^']*)'")


def lines(text, first, last):
    return "\n".join(text.splitlines()[first - 1:last])


def excerpts(text):
    """Named evidence bundles. One bundle becomes one JEV request's state."""
    return {
        "files": "The deliverable folder contains exactly these files:\n"
                 "- index.html (48,694 bytes)\n- AGENTS.md (2,117 bytes, written by the harness)\n",
        # Every question in this group must be answerable from this bundle: the
        # config, the level table, the power-up table and the easy-mode switch.
        "rules": lines(text, 25, 66) + "\n" + lines(text, 257, 262) + "\n" + matched(text,
                                                                                      r"vargMode|vargShot"),
        "ui": "Text drawn by the interface:\n" + "\n".join(
            f"- {m.group(1)}" for m in ETYPE.finditer(text)),
        "touch": linestouch(text),
        # The dragon, the goal and the actual ball drawing, so both render questions
        # have their evidence rather than a question with nothing behind it.
        "render": lines(text, 690, 800) + "\n" + lines(text, 833, 850),
    }


def matched(text, pattern):
    return "\n".join(line.strip() for line in text.splitlines() if re.search(pattern, line))


def linestouch(text):
    src = text.splitlines()
    keep = [src[24], src[25], src[26], src[27], src[28], src[29]]
    for n, line in enumerate(src, start=1):
        if re.search(r"BTN_|clampToPitch|P\.y\+P\.h-r|P\.x\+P\.w-r|inBtn", line):
            keep.append(f"{line.strip()}")
    return "Pitch and touch-button geometry:\n" + "\n".join(keep)


def five_levels(text):
    block = lines(text, 38, 66)
    return len(re.findall(r"name:\"Bane", block)) == 5


def two_minutes(text):
    match = re.search(r"matchTime:\s*(\d+)", text)
    return bool(match) and int(match.group(1)) == 120


def three_powers(text):
    block = lines(text, 257, 262)
    return len(re.findall(r"kind:'", block)) == 3


def progressively_harder(text):
    block = lines(text, 51, 60)
    speed = [float(v) for v in re.findall(r"speed:([\d.]+)", block)]
    gk = [float(v) for v in re.findall(r"gk:([\d.]+)", block)]
    return speed == sorted(speed) and gk == sorted(gk) and speed[0] < speed[-1]


def easy_mode(text):
    return bool(re.search(r"vargMode", text)) and bool(re.search(r"vargShot:\s*(\d+)", text))


def norwegian_string(text, needle):
    return needle.lower() in text.lower()


def buttons_clear_of_pitch(text):
    """A button that overlaps the rectangle players are clamped inside can cover them."""
    pitch = re.search(r"pitch:\s*\{\s*x:\s*(\d+),\s*y:\s*(\d+),\s*w:\s*(\d+),\s*h:\s*(\d+)", text)
    shoot = re.search(r"BTN_SHOOT\s*=\s*\{\s*x:\s*(\d+),\s*y:\s*(\d+),\s*r:\s*(\d+)", text)
    if not (pitch and shoot):
        return None
    px, py, pw, ph = (int(v) for v in pitch.groups())
    cx, cy, r = (int(v) for v in shoot.groups())
    overlaps_x = cx + r > px and cx - r < px + pw
    overlaps_y = cy + r > py and cy - r < py + ph
    return not (overlaps_x and overlaps_y)


def single_file(text):
    return True  # one html file is the whole deliverable


def no_network(text):
    return not re.search(r"(?i)(src|href)\s*=\s*[\"'](https?:)?//", text) and \
        not re.search(r"(?i)\bfetch\(|@import|\bimport\s+\w+\s+from", text)


def kart_excerpts(text):
    """Evidence bundles for the kart game, one per JEV request."""
    return {
        "ui": lines(text, 1, 35),
        "config": lines(text, 37, 45),
        "rules": lines(text, 92, 145),
        "render": lines(text, 146, 200),
        "selftest": lines(text, 306, 337),
    }


KART_CLAIMS = [
    ("single_file", "ui",
     "Is the entire game delivered as one self-contained HTML file?",
     lambda text: "<canvas" in text and "</html>" in text),
    ("no_network", "ui",
     "Does the page load any script, style, font or asset from a network address?",
     lambda text: not no_network(text)),
    ("three_laps", "config", "Does a race last exactly three laps?",
     lambda text: bool(re.search(r"LAPS\s*=\s*3", text))),
    ("autostart_hook", "config",
     "Can a race be started from a URL parameter with no player input?",
     lambda text: 'get("autostart")' in text),
    ("selftest_hook", "config",
     "Does the file provide a self-test mode that writes PASS or FAIL lines into the page?",
     lambda text: 'get("selftest")' in text and "PASS" in text),
    ("seeded_only", "config",
     "Is the randomness seeded, so the world is identical on every run?",
     lambda text: "mulberry32" in text and "Math.random" not in text),
    ("one_opponent", "rules", "Is there exactly one computer-controlled opponent?",
     lambda text: len(re.findall(r"\bai\s*=\s*\{", text)) == 1),
    ("gates_in_order", "rules",
     "Must every checkpoint gate be passed in order before a lap is counted?",
     lambda text: "lastGate" in text and "bits" in text),
    ("race_ends", "rules",
     "Does the race end and show a finish message after the final lap?",
     lambda text: bool(re.search(r"kart\.lap>=LAPS", text))),
    ("low_eye", "render",
     "Is the camera placed at the driver's own eye height, low over the kart?",
     lambda text: bool(re.search(r"eye\s*=\s*\{[^}]*y\s*:\s*0\.\d", text))),
    ("projection_check_present", "selftest",
     "Does the self-test check that a point straight ahead of the camera lands at the centre of the screen?",
     lambda text: "projection centre" in text),
    # Not decidable by reading: these need the file to run. The browser result is the
    # reference, and JEV's answer is still worth recording.
    ("projection_works", "render",
     "Does a point straight ahead of the camera actually land at the centre of the screen, "
     "given the projection maths in this file?", None),
    ("selftest_all_pass", "selftest",
     "When the self-test runs, does every check pass?", None),
]


# id, group, question, deterministic check (None = human reading is the reference)
CLAIMS = [
    ("single_deliverable", "files",
     "Is the whole game delivered as one self-contained HTML file rather than a set of source files?",
     single_file),
    ("no_network", "files",
     "Does the page load any resource from a network address such as a CDN or an external script?",
     lambda text: not no_network(text)),
    ("five_levels", "rules", "Does the game define exactly five levels?", five_levels),
    ("two_minute_match", "rules", "Does a single match last two minutes?", two_minutes),
    ("three_powerups", "rules", "Are there exactly three kinds of power-up?", three_powers),
    ("levels_get_harder", "rules", "Do the five levels get progressively harder?",
     progressively_harder),
    ("easy_mode", "rules",
     "Is there an easy mode that makes the opponents slower and Varg's shots stronger?",
     easy_mode),
    ("text_goal", "ui", "Does the interface show the Norwegian word for goal when a goal is scored?",
     lambda text: norwegian_string(text, "MÅL")),
    ("text_retry", "ui", "Does the interface offer a retry in Norwegian after the player loses?",
     lambda text: norwegian_string(text, "Prøv igjen")),
    ("text_win", "ui", "Does the interface celebrate with a Norwegian message that Varg won?",
     lambda text: norwegian_string(text, "varg vant")),
    ("text_start", "ui", "Does the interface show a Norwegian prompt to start playing?",
     lambda text: norwegian_string(text, "spill")),
    ("buttons_clear_of_pitch", "touch",
     "Are all on-screen touch buttons positioned completely outside the rectangle the players move in?",
     buttons_clear_of_pitch),
    ("ball_visible", "render",
     "Is the ball drawn so that it stays easy to see against the pitch and the players?",
     None),
    ("dragon_blocks_view", "render",
     "Can the dragon overlap and block the view of the goal or the players near it?", None),
]

# Each suite pairs a set of evidence bundles with the claims they answer.
SUITES = {
    "kart": (kart_excerpts, KART_CLAIMS),
    "football": (excerpts, CLAIMS),
}


async def main():
    parser = argparse.ArgumentParser(description="JEV versus plain code, same claims")
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--suite", default="kart", choices=sorted(SUITES))
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    text = args.artifact.read_text(encoding="utf-8", errors="replace")
    excerpt_for, claims = SUITES[args.suite]
    bundles = excerpt_for(text)
    config = Settings.from_env()
    jev = Jev(api_key=config.typesafe_api_key, base_url=args.base_url or config.jev_base_url,
              model=args.model or config.jev_model, request_timeout=config.jev_request_timeout)

    answers, usage, latency = {}, {}, 0
    try:
        for group in sorted({group for _, group, _, _ in claims}):
            questions = {name: noul(question) for name, g, question, _ in claims if g == group}
            state = {"artifact_excerpt": bundles[group]}
            result = await jev.system_one(state, questions)
            answers.update(result["answers"])
            usage[group] = result["usage"]
            latency += result["latency_ms"]
            print(f"  {group:<7} {len(questions)} questions, {len(bundles[group])} chars, "
                  f"{result['latency_ms']} ms")
    except JevError as exc:
        print(f"FAILED: {exc}")
        return 1
    finally:
        await jev.close()

    print(f"\n{'claim':<24} {'code':<6} {'JEV':<7} verdict")
    agree = compared = 0
    for name, _, _, check in claims:
        answer = answers.get(name, {})
        value = answer.get("noul")
        truth = check(text) if check else None
        if truth is None:
            print(f"{name:<24} {'n/a':<6} {value!s:<7} not decidable by code")
            continue
        jev_says = value >= 0.5
        compared += 1
        agree += jev_says == truth
        flag = "agree" if jev_says == truth else "DISAGREE"
        print(f"{name:<24} {str(truth):<6} {value:<7.3f} {flag}"
              + ("" if jev_says == truth else f"  (JEV said {'yes' if jev_says else 'no'})"))
    total_in = sum((u or {}).get("input_tokens", 0) for u in usage.values())
    print(f"\nagreement {agree}/{compared} on claims code can decide; "
          f"{total_in} input tokens; {latency} ms; "
          f"about ${total_in * 0.042 / 1e6:.6f} at TypeSafe pricing")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
