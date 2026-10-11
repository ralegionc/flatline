"""Command line interface.

    flatline match   PATTERN TEXT     what matched, and where
    flatline explain PATTERN          the compiled program and its NFA
    flatline trace   PATTERN TEXT     the thread list at every input position
    flatline race    PATTERN          both engines on growing input
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import __version__
from .api import Pattern
from .backtrack import DEFAULT_MAX_STEPS
from .compiler import compile_pattern
from .parser import RegexSyntaxError
from .program import CONSUMING, JMP, MATCH, SPLIT
from .trace import race, trace

# --- output helpers ---------------------------------------------------------

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
RESET = "\033[0m"

_use_colour = True


def c(text: str, colour: str) -> str:
    return "{}{}{}".format(colour, text, RESET) if _use_colour else text


def visible(text: str) -> str:
    """Render a subject string with control characters made visible."""
    return text.replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")


# --- match ------------------------------------------------------------------


def cmd_match(args: argparse.Namespace) -> int:
    p = Pattern(args.pattern, args.flags)
    matches = list(p.finditer(args.text)) if args.all else [m for m in [p.search(args.text)] if m]

    if not matches:
        print("{}  {}".format(c("no match", RED), c(repr(args.pattern), DIM)))
        return 1

    for m in matches:
        s, e = m.span()
        print()
        print("  " + visible(args.text))
        print("  " + c(" " * len(visible(args.text[:s])) + "^" * max(len(visible(args.text[s:e])), 1), GREEN))
        print("  {} {}".format(c("match", GREEN), c("[{}:{}]".format(s, e), DIM)), repr(m.group()))

        names = {v: k for k, v in p.groupindex.items()}
        for i in range(1, p.groups + 1):
            gs, ge = m.span(i)
            label = "{}".format(i) + (" ({})".format(names[i]) if i in names else "")
            if gs < 0:
                print("    {}  {}".format(c(label, DIM), c("unset", DIM)))
            else:
                print("    {}  {} {}".format(label, repr(m.group(i)), c("[{}:{}]".format(gs, ge), DIM)))

    # stats() measures a single search, so with --all it would describe only the
    # first of the matches printed above.  Say which one the numbers are for.
    stats = p.stats(args.text)
    scope = "the first search took" if args.all and len(matches) > 1 else "took"
    print()
    print(
        c(
            "  {} {} thread-steps, at most {} live threads, program is {} instructions".format(
                scope, stats["steps"], stats["max_threads"], stats["program_length"]
            ),
            DIM,
        )
    )
    return 0


# --- explain ----------------------------------------------------------------


def cmd_explain(args: argparse.Namespace) -> int:
    prog = compile_pattern(args.pattern, args.flags)

    if args.json:
        from .trace import edges, program_to_json

        print(json.dumps({"program": program_to_json(prog), "edges": edges(prog)}, indent=2))
        return 0

    print()
    print("  {}   {}".format(c(args.pattern, BOLD), c("({} instructions)".format(len(prog)), DIM)))
    print()

    targets: dict[int, list[str]] = {}
    for pc, inst in enumerate(prog.insts):
        if inst.op == JMP:
            targets.setdefault(inst.x, []).append(str(pc))
        elif inst.op == SPLIT:
            targets.setdefault(inst.x, []).append(str(pc))
            targets.setdefault(inst.y, []).append(str(pc))

    for pc, inst in enumerate(prog.insts):
        label = c("<- {}".format(",".join(targets[pc])), DIM) if pc in targets else ""
        colour = BLUE if inst.op == SPLIT else (GREEN if inst.op == MATCH else "")
        rendered = c(inst.render(), colour) if colour else inst.render()
        print("  {:>3}  {:<32} {}".format(pc, rendered, label))

    print()
    consuming = sum(1 for i in prog.insts if i.op in CONSUMING)
    print(
        c(
            "  {} of these consume input, so no search can take more than\n"
            "  {} x len(text) thread-steps -- whatever the text is.".format(
                consuming, len(prog)
            ),
            DIM,
        )
    )
    return 0


# --- trace ------------------------------------------------------------------


def cmd_trace(args: argparse.Namespace) -> int:
    data = trace(args.pattern, args.text, args.flags)

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    prog = data["program"]
    print()
    print("  {}  over  {}".format(c(args.pattern, BOLD), c(repr(args.text), BOLD)))
    print()
    print("  {:<4} {:<6} {}".format("pos", "char", "live threads (pc: instruction)"))
    print("  " + c("-" * 66, DIM))

    for step in data["steps"]:
        ch = "eof" if step["char"] is None else repr(step["char"])
        if not step["threads"]:
            print("  {:<4} {:<6} {}".format(step["sp"], ch, c("none", DIM)))
            continue
        for i, th in enumerate(step["threads"]):
            head = "  {:<4} {:<6} ".format(step["sp"], ch) if i == 0 else "  " + " " * 11
            inst = prog[th["pc"]]
            mark = c(" <- match", GREEN) if inst["op"] == MATCH else ""
            print("{}{:>3}: {}{}".format(head, th["pc"], inst["text"], mark))

    print()
    if data["matched"]:
        s, e = data["spans"][0]
        print("  {} {} at [{}:{}]".format(c("matched", GREEN), repr(args.text[s:e]), s, e))
    else:
        print("  " + c("no match", RED))
    print(
        c(
            "  peak {} live threads out of {} possible -- the bound that makes this linear".format(
                data["max_threads"], data["program_length"]
            ),
            DIM,
        )
    )
    return 0


# --- race -------------------------------------------------------------------


def bar(value: int, largest: int, width: int = 34) -> str:
    """A log-scaled bar.  Linear scaling would render the Pike VM invisible."""
    import math

    if value <= 0 or largest <= 0:
        return ""
    scaled = math.log10(value + 1) / math.log10(largest + 1)
    return "#" * max(1, int(scaled * width))


def cmd_race(args: argparse.Namespace) -> int:
    data = race(
        args.pattern,
        args.unit,
        args.suffix,
        max_n=args.max_n,
        flags=args.flags,
        max_steps=args.max_steps,
    )

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    rows = data["rows"]
    largest = max(r["backtrack_steps"] for r in rows)

    print()
    print("  {}   on   {}".format(c(args.pattern, BOLD), c("{!r} * n + {!r}".format(args.unit, args.suffix), BOLD)))
    print(c("  both engines running the same {}-instruction program".format(data["program_length"]), DIM))
    print()
    print("  {:>3}  {:>10}  {:>14}   {}".format("n", "flatline", "backtracking", "backtracking steps (log scale)"))
    print("  " + c("-" * 76, DIM))

    gave_up_at = None
    for r in rows:
        if r["backtrack_gave_up"]:
            bt_cell = c("gave up", RED)
            if gave_up_at is None:
                gave_up_at = r["n"]
            graph = c("#" * 34 + " >", RED)
        else:
            bt_cell = "{:,}".format(r["backtrack_steps"])
            graph = c(bar(r["backtrack_steps"], largest), YELLOW)
        print("  {:>3}  {:>10}  {:>14}   {}".format(r["n"], "{:,}".format(r["pike_steps"]), bt_cell, graph))

    print()
    finished = [r for r in rows if not r["backtrack_gave_up"]]
    if finished:
        last = finished[-1]
        ratio = last["backtrack_steps"] / max(last["pike_steps"], 1)
        print(
            "  at n={}: flatline {:,} steps, backtracking {:,} steps  ({}x)".format(
                last["n"], last["pike_steps"], last["backtrack_steps"], "{:,.0f}".format(ratio)
            )
        )
    if gave_up_at is not None:
        print(
            "  the backtracker exceeded {:,} steps at n={}; flatline needed {:,}".format(
                args.max_steps, gave_up_at, rows[gave_up_at - 1]["pike_steps"]
            )
        )
    print()
    return 0


# --- entry point ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    # Shared options live on a parent parser so they are accepted both before
    # and after the subcommand name, which is where people actually type them.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-colour", "--no-color", action="store_true", dest="no_colour", help="disable ANSI colour")
    common.add_argument(
        "-f",
        "--flags",
        default="",
        help="any of i (ignore case), s (dot matches newline), m (multiline)",
    )

    ap = argparse.ArgumentParser(
        prog="flatline",
        parents=[common],
        description="A regular expression engine that cannot be made to blow up.",
    )
    ap.add_argument("--version", action="version", version="flatline {}".format(__version__))
    sub = ap.add_subparsers(dest="command", required=True)

    m = sub.add_parser("match", parents=[common], help="show what a pattern matches")
    m.add_argument("pattern")
    m.add_argument("text")
    m.add_argument("-a", "--all", action="store_true", help="report every match, not just the first")
    m.set_defaults(func=cmd_match)

    e = sub.add_parser("explain", parents=[common], help="disassemble a pattern into its NFA program")
    e.add_argument("pattern")
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_explain)

    t = sub.add_parser("trace", parents=[common], help="show the live thread list at every input position")
    t.add_argument("pattern")
    t.add_argument("text")
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=cmd_trace)

    r = sub.add_parser("race", parents=[common], help="compare against a backtracking engine on growing input")
    r.add_argument("pattern")
    r.add_argument("--unit", default="a", help="the string repeated n times (default: a)")
    r.add_argument("--suffix", default="!", help="appended once, to force the match to fail (default: !)")
    r.add_argument("--max-n", type=int, default=30, dest="max_n")
    r.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, dest="max_steps")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_race)

    return ap


def main(argv: list[str] | None = None) -> int:
    global _use_colour
    ap = build_parser()
    args = ap.parse_args(argv)

    _use_colour = not args.no_colour and sys.stdout.isatty()

    try:
        return args.func(args)
    except RegexSyntaxError as exc:
        print("{}: {}".format(c("pattern error", RED), exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
