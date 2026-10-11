"""Instrumented runs, serialisable to JSON.

Everything the visualiser draws comes from here, so the picture on the web
page and the numbers in the terminal are produced by the same execution rather
than by two implementations that might drift apart.
"""

from __future__ import annotations

from typing import Any

from .backtrack import DEFAULT_MAX_STEPS
from .backtrack import search as bt_search
from .compiler import compile_pattern
from .pike import Run, Saved
from .program import CONSUMING, JMP, MATCH, SPLIT, Program


def program_to_json(prog: Program) -> list[dict[str, Any]]:
    out = []
    for pc, inst in enumerate(prog.insts):
        entry: dict[str, Any] = {"pc": pc, "op": inst.op, "text": inst.render()}
        if inst.op in (JMP, SPLIT):
            entry["x"] = inst.x
        if inst.op == SPLIT:
            entry["y"] = inst.y
        if inst.op == "save":
            entry["slot"] = inst.slot
        if inst.op == "assert":
            entry["kind"] = inst.kind
        out.append(entry)
    return out


def edges(prog: Program) -> list[dict[str, Any]]:
    """Graph edges of the NFA, one entry per possible transition.

    ``kind`` is ``consume`` for an edge that requires an input character and
    ``epsilon`` for one taken for free; ``preferred`` marks the arm of a split
    that priority order visits first.
    """
    out: list[dict[str, Any]] = []
    for pc, inst in enumerate(prog.insts):
        if inst.op == JMP:
            out.append({"from": pc, "to": inst.x, "kind": "epsilon", "preferred": True})
        elif inst.op == SPLIT:
            out.append({"from": pc, "to": inst.x, "kind": "epsilon", "preferred": True})
            out.append({"from": pc, "to": inst.y, "kind": "epsilon", "preferred": False})
        elif inst.op == MATCH:
            pass
        elif inst.op in CONSUMING:
            out.append({"from": pc, "to": pc + 1, "kind": "consume", "preferred": True})
        else:  # save / assert -- free, single successor
            out.append({"from": pc, "to": pc + 1, "kind": "epsilon", "preferred": True})
    return out


def spans_from_saved(prog: Program, saved: Saved | None) -> list[list[int] | None]:
    if saved is None:
        return []
    out: list[list[int] | None] = []
    for i in range(prog.ngroups + 1):
        s, e = saved[2 * i], saved[2 * i + 1]
        out.append(None if s is None or e is None else [s, e])
    return out


def trace(pattern: str, text: str, flags: str = "") -> dict[str, Any]:
    """Run ``pattern`` over ``text``, recording the thread list at every offset."""
    prog = compile_pattern(pattern, flags)
    run = Run(prog, text)
    steps: list[dict[str, Any]] = []

    def observe(sp: int, threads, matched: bool) -> None:
        steps.append(
            {
                "sp": sp,
                "char": text[sp] if sp < len(text) else None,
                "matched": matched,
                "threads": [
                    {"pc": pc, "saved": [None if v is None else v for v in saved]}
                    for pc, saved in threads
                ],
            }
        )

    saved = run.search_traced(observe)

    return {
        "pattern": pattern,
        "flags": flags,
        "text": text,
        "program": program_to_json(prog),
        "edges": edges(prog),
        "ngroups": prog.ngroups,
        "groupnames": prog.groupnames,
        "steps": steps,
        "matched": saved is not None,
        "spans": spans_from_saved(prog, saved),
        "max_threads": run.max_threads,
        "total_steps": run.steps,
        "program_length": len(prog),
    }


def race(
    pattern: str,
    unit: str,
    suffix: str = "",
    max_n: int = 30,
    flags: str = "",
    max_steps: int = DEFAULT_MAX_STEPS,
) -> dict[str, Any]:
    """Measure both engines on ``unit * n + suffix`` for n in ``1..max_n``.

    Reported in steps rather than seconds: a step is one thread advance for the
    Pike VM and one path step for the backtracker, both of which are constant
    work.  Counting them makes the comparison independent of the machine, the
    interpreter and whatever else the CPU was doing at the time.
    """
    prog = compile_pattern(pattern, flags)
    rows: list[dict[str, Any]] = []

    for n in range(1, max_n + 1):
        text = unit * n + suffix
        run = Run(prog, text)
        pike_saved = run.search()
        bt = bt_search(prog, text, max_steps=max_steps)

        rows.append(
            {
                "n": n,
                "length": len(text),
                "pike_steps": run.steps,
                "pike_matched": pike_saved is not None,
                "pike_max_threads": run.max_threads,
                "backtrack_steps": bt.steps,
                "backtrack_matched": bt.matched,
                "backtrack_gave_up": bt.gave_up,
            }
        )

    return {
        "pattern": pattern,
        "unit": unit,
        "suffix": suffix,
        "max_steps": max_steps,
        "program_length": len(prog),
        "rows": rows,
    }
