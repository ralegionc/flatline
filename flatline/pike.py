"""The Pike VM: an NFA simulation with submatch capture, in linear time.

The idea, due to Thompson (1968) for matching and Pike (1987) for capture, is
to run *all* alternatives at once instead of trying them one after another.
The VM keeps a list of threads -- each a program counter plus its capture
slots -- and advances every thread by one input character per step.

The single rule that buys the guarantee is in ``_add``:

    a program counter appears at most once in a thread list

Two threads sitting on the same instruction at the same input position will
behave identically from here on, so keeping both is pure waste.  Discarding
the duplicate caps the thread list at ``len(program)`` regardless of input, and
the outer loop runs once per character, so the total work is bounded by
``O(len(text) * len(program))``.  That bound holds for *every* input; there is
no pathological case, because there is no case where the work per character
can exceed the size of the program.

Threads are kept in priority order, highest first.  That ordering is what
makes ``a|ab`` prefer the shorter branch and ``a*`` prefer the longer one, and
it is why a match found by a high-priority thread cuts every thread below it.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

from .program import ANY, ASSERT, CHAR, CLASS, JMP, MATCH, SAVE, SPLIT, Inst, Program

Saved = tuple[int | None, ...]
#: A snapshot handed to an observer: the input offset and the live thread list.
Observer = Callable[[int, Sequence[tuple[int, Saved]], bool], None]

_WORD_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _is_word(text: str, i: int) -> bool:
    return 0 <= i < len(text) and text[i] in _WORD_CHARS


def check_assert(kind: str, text: str, sp: int) -> bool:
    if kind == "bos":
        return sp == 0
    if kind == "eos":
        return sp == len(text)
    if kind == "eot":
        # Perl/Python '$' without the multiline flag: end of text, or just
        # before a single trailing newline.
        return sp == len(text) or (sp == len(text) - 1 and text[sp] == "\n")
    if kind == "bol":
        return sp == 0 or text[sp - 1] == "\n"
    if kind == "eol":
        return sp == len(text) or text[sp] == "\n"
    if kind == "wordb":
        return _is_word(text, sp - 1) != _is_word(text, sp)
    if kind == "nwordb":
        return _is_word(text, sp - 1) == _is_word(text, sp)
    raise AssertionError("unknown assertion {!r}".format(kind))


def consumes(inst: Inst, ch: str) -> bool:
    if inst.op == CHAR:
        return ch == inst.ch
    if inst.op == CLASS:
        assert inst.cls is not None
        return inst.cls.matches(ch)
    if inst.op == ANY:
        return inst.dotall or ch != "\n"
    return False


class ThreadList:
    """Threads for one input position, in priority order, deduplicated by pc."""

    __slots__ = ("threads", "seen")

    def __init__(self) -> None:
        self.threads: list[tuple[int, Saved]] = []
        self.seen: set[int] = set()

    def clear(self) -> None:
        self.threads.clear()
        self.seen.clear()

    def __len__(self) -> int:
        return len(self.threads)

    def __iter__(self) -> Iterable[tuple[int, Saved]]:
        return iter(self.threads)


class Run:
    """One execution of a program over one subject string."""

    def __init__(self, prog: Program, text: str) -> None:
        self.prog = prog
        self.text = text
        #: largest number of simultaneously live threads observed
        self.max_threads = 0
        #: number of (thread, character) advances -- the work actually done
        self.steps = 0

    def _add(self, lst: ThreadList, pc: int, sp: int, saved: Saved) -> None:
        """Epsilon-closure from ``pc``, appending reachable non-epsilon states.

        Iterative rather than recursive: the closure can be as deep as the
        program is long, and a 1000-instruction program would blow the Python
        stack.  Pushing the fallback arm before the preferred one makes the
        explicit stack reproduce depth-first preorder, which is exactly the
        priority order the recursive form would produce.
        """
        stack: list[tuple[int, Saved]] = [(pc, saved)]
        while stack:
            pc, saved = stack.pop()
            if pc in lst.seen:
                continue
            lst.seen.add(pc)
            inst = self.prog[pc]
            op = inst.op

            if op == JMP:
                stack.append((inst.x, saved))
            elif op == SPLIT:
                stack.append((inst.y, saved))  # fallback, explored second
                stack.append((inst.x, saved))  # preferred, explored first
            elif op == SAVE:
                updated = list(saved)
                updated[inst.slot] = sp
                stack.append((pc + 1, tuple(updated)))
            elif op == ASSERT:
                if check_assert(inst.kind, self.text, sp):
                    stack.append((pc + 1, saved))
            else:
                # char / class / any / match -- these wait for input
                lst.threads.append((pc, saved))

    def search(self, start: int = 0, anchored: bool = False) -> Saved | None:
        text, prog = self.text, self.prog
        clist, nlist = ThreadList(), ThreadList()
        matched: Saved | None = None
        blank: Saved = (None,) * prog.nslots

        sp = start
        while True:
            # Seed a new attempt at this offset, at lowest priority, so that a
            # match starting earlier always wins.  Once anything has matched we
            # stop seeding: a later start could only produce a worse match.
            if matched is None and (not anchored or sp == start):
                self._add(clist, 0, sp, blank)

            # Running out of threads only ends the search if no later start
            # offset could still produce one: either something already matched
            # (a later start could only be worse) or the search is anchored.
            if not clist.threads and (matched is not None or anchored):
                break

            self.max_threads = max(self.max_threads, len(clist))
            nlist.clear()
            ch = text[sp] if sp < len(text) else ""

            i = 0
            while i < len(clist.threads):
                pc, saved = clist.threads[i]
                self.steps += 1
                inst = prog[pc]

                if inst.op == MATCH:
                    matched = saved
                    # Every thread after this one is lower priority, so its
                    # match (if any) would lose anyway.  Drop them.
                    del clist.threads[i + 1 :]
                    break
                if ch and consumes(inst, ch):
                    self._add(nlist, pc + 1, sp + 1, saved)
                i += 1

            if sp >= len(text):
                break
            sp += 1
            clist, nlist = nlist, clist

        return matched

    def search_traced(self, observer: Observer, start: int = 0, anchored: bool = False) -> Saved | None:
        """Like :meth:`search`, but reports the thread list at every offset."""
        text, prog = self.text, self.prog
        clist, nlist = ThreadList(), ThreadList()
        matched: Saved | None = None
        blank: Saved = (None,) * prog.nslots

        sp = start
        while True:
            if matched is None and (not anchored or sp == start):
                self._add(clist, 0, sp, blank)

            observer(sp, list(clist.threads), matched is not None)
            if not clist.threads and (matched is not None or anchored):
                break

            self.max_threads = max(self.max_threads, len(clist))
            nlist.clear()
            ch = text[sp] if sp < len(text) else ""

            i = 0
            while i < len(clist.threads):
                pc, saved = clist.threads[i]
                self.steps += 1
                inst = prog[pc]
                if inst.op == MATCH:
                    matched = saved
                    del clist.threads[i + 1 :]
                    break
                if ch and consumes(inst, ch):
                    self._add(nlist, pc + 1, sp + 1, saved)
                i += 1

            if sp >= len(text):
                break
            sp += 1
            clist, nlist = nlist, clist

        return matched


def search(prog: Program, text: str, start: int = 0, anchored: bool = False) -> Saved | None:
    """Find the leftmost match at or after ``start``; ``None`` if there is none."""
    return Run(prog, text).search(start, anchored)
