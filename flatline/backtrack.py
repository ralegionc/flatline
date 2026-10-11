"""A textbook backtracking matcher, for comparison.

This is not a straw man.  It runs the *same compiled program* as the Pike VM,
with the same priority order, and it returns the same answers on every pattern
either can finish.  The only difference is the strategy: where the Pike VM
advances all alternatives together, this one commits to the preferred branch
and unwinds to the next when that fails.

That single choice is what every backtracking engine in wide use makes --
PCRE, Python's ``re``, Java's ``java.util.regex``, JavaScript's ``RegExp`` --
and it is why all of them can be made to hang.  The state a backtracker
carries is a *path*, and the number of distinct paths through a pattern like
``(a+)+b`` grows exponentially with the input.  The Pike VM carries a *set of
states* instead, and there are only ever as many states as the program is
long.

``max_steps`` exists because without it this function does not reliably
terminate in the time anyone has.  Hitting the cap is the demonstration, not a
failure of the implementation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .pike import Saved, check_assert, consumes
from .program import ASSERT, JMP, MATCH, SAVE, SPLIT, Program

#: Default work budget.  A few hundred thousand path steps is far more than
#: any well-behaved pattern needs, and a small fraction of what a pathological
#: one wants.
DEFAULT_MAX_STEPS = 1_000_000


@dataclass
class BacktrackResult:
    #: capture slots, or ``None`` if the pattern did not match
    saved: Saved | None
    #: path steps taken -- the unit of work this engine spends
    steps: int
    #: ``True`` if the budget ran out before an answer was reached
    gave_up: bool

    @property
    def matched(self) -> bool:
        return self.saved is not None


def search(
    prog: Program,
    text: str,
    start: int = 0,
    anchored: bool = False,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> BacktrackResult:
    """Leftmost-first search, trying each start offset in turn."""
    blank: Saved = (None,) * prog.nslots
    steps = 0
    last_start = start if anchored else len(text)

    for sp0 in range(start, last_start + 1):
        stack: list[tuple[int, int, Saved]] = [(0, sp0, blank)]

        while stack:
            steps += 1
            if steps > max_steps:
                return BacktrackResult(None, steps - 1, gave_up=True)

            pc, sp, saved = stack.pop()
            inst = prog[pc]
            op = inst.op

            if op == MATCH:
                return BacktrackResult(saved, steps, gave_up=False)
            if op == JMP:
                stack.append((inst.x, sp, saved))
            elif op == SPLIT:
                # Push the fallback first so the preferred arm is popped first.
                stack.append((inst.y, sp, saved))
                stack.append((inst.x, sp, saved))
            elif op == SAVE:
                updated = list(saved)
                updated[inst.slot] = sp
                stack.append((pc + 1, sp, tuple(updated)))
            elif op == ASSERT:
                if check_assert(inst.kind, text, sp):
                    stack.append((pc + 1, sp, saved))
            else:
                if sp < len(text) and consumes(inst, text[sp]):
                    stack.append((pc + 1, sp + 1, saved))

    return BacktrackResult(None, steps, gave_up=False)
