"""Thompson construction: AST -> a flat instruction list.

Every instruction is one NFA state.  ``split`` is the only branching state and
it has exactly two successors, so the compiled program is directly a graph the
visualiser can draw: an edge for each fall-through, each ``jmp`` target, and
both ``split`` arms.

The construction has one property worth stating explicitly, because the whole
linear-time guarantee rests on it: **the program size is proportional to the
pattern size**, and no input can make it grow.  Combined with the Pike VM's
rule that a state appears at most once in the thread list per input position,
that bounds total work at ``O(len(text) * len(program))``.
"""

from __future__ import annotations

from .parser import (
    Alternate,
    AnyNode,
    Assertion,
    ClassNode,
    Concat,
    Empty,
    Group,
    Literal,
    Node,
    Repeat,
    parse,
)
from .program import ANY, ASSERT, CHAR, CLASS, JMP, MATCH, SAVE, SPLIT, CharClass, Inst, Program


class Compiler:
    def __init__(self, flags: frozenset[str]) -> None:
        self.insts: list[Inst] = []
        self.flags = flags
        self.ignorecase = "i" in flags
        self.dotall = "s" in flags
        self.multiline = "m" in flags

    # -- emit helpers

    def emit(self, inst: Inst) -> int:
        self.insts.append(inst)
        return len(self.insts) - 1

    @property
    def here(self) -> int:
        return len(self.insts)

    # -- compilation

    def compile(self, node: Node) -> None:
        method = getattr(self, "_c_" + type(node).__name__.lower())
        method(node)

    def _c_empty(self, node: Empty) -> None:
        return

    def _c_literal(self, node: Literal) -> None:
        if self.ignorecase:
            folded = _fold_char(node.ch)
            if len(folded) > 1:
                self.emit(Inst(CLASS, cls=CharClass.from_ranges((ord(c), ord(c)) for c in folded)))
                return
        self.emit(Inst(CHAR, ch=node.ch))

    def _c_classnode(self, node: ClassNode) -> None:
        cls = _fold_class(node.cls) if self.ignorecase else node.cls
        self.emit(Inst(CLASS, cls=cls))

    def _c_anynode(self, node: AnyNode) -> None:
        self.emit(Inst(ANY, dotall=self.dotall))

    def _c_assertion(self, node: Assertion) -> None:
        kind = node.kind
        # Without the multiline flag, ^ anchors to the start of the whole text
        # and $ to its end (or just before a single trailing newline, which is
        # what Perl and Python both do).
        if not self.multiline:
            kind = {"bol": "bos", "eol": "eot"}.get(kind, kind)
        self.emit(Inst(ASSERT, kind=kind))

    def _c_concat(self, node: Concat) -> None:
        for part in node.parts:
            self.compile(part)

    def _c_alternate(self, node: Alternate) -> None:
        # split L_branch, L_next ; branch ; jmp END   (repeated), last branch bare
        jumps_to_end: list[int] = []
        for branch in node.branches[:-1]:
            split = self.emit(Inst(SPLIT))
            self.insts[split].x = self.here
            self.compile(branch)
            jumps_to_end.append(self.emit(Inst(JMP)))
            self.insts[split].y = self.here
        self.compile(node.branches[-1])
        for j in jumps_to_end:
            self.insts[j].x = self.here

    def _c_group(self, node: Group) -> None:
        if node.index is None:
            self.compile(node.body)
            return
        self.emit(Inst(SAVE, slot=2 * node.index))
        self.compile(node.body)
        self.emit(Inst(SAVE, slot=2 * node.index + 1))

    def _c_repeat(self, node: Repeat) -> None:
        lo, hi, body, greedy = node.lo, node.hi, node.body, node.greedy

        if hi is not None and hi == 0:
            return
        if lo == 0 and hi is None:
            self._star(body, greedy)
        elif lo == 1 and hi is None:
            self._plus(body, greedy)
        elif lo == 0 and hi == 1:
            self._opt(body, greedy)
        elif hi is None:
            for _ in range(lo - 1):
                self.compile(body)
            self._plus(body, greedy)
        else:
            for _ in range(lo):
                self.compile(body)
            self._nested_opt(body, hi - lo, greedy)

    def _star(self, body: Node, greedy: bool) -> None:
        #  L1: split L2, L3 ; L2: body ; jmp L1 ; L3:
        top = self.emit(Inst(SPLIT))
        body_start = self.here
        self.compile(body)
        self.emit(Inst(JMP, x=top))
        after = self.here
        self._arms(top, body_start, after, greedy)

    def _plus(self, body: Node, greedy: bool) -> None:
        #  L1: body ; split L1, L3 ; L3:
        body_start = self.here
        self.compile(body)
        split = self.emit(Inst(SPLIT))
        after = self.here
        self._arms(split, body_start, after, greedy)

    def _opt(self, body: Node, greedy: bool) -> None:
        #  split L2, L3 ; L2: body ; L3:
        split = self.emit(Inst(SPLIT))
        body_start = self.here
        self.compile(body)
        after = self.here
        self._arms(split, body_start, after, greedy)

    def _nested_opt(self, body: Node, count: int, greedy: bool) -> None:
        """Compile ``(body(body(body)?)?)?`` to ``count`` levels of nesting."""
        if count <= 0:
            return
        split = self.emit(Inst(SPLIT))
        body_start = self.here
        self.compile(body)
        self._nested_opt(body, count - 1, greedy)
        after = self.here
        self._arms(split, body_start, after, greedy)

    def _arms(self, split: int, body: int, after: int, greedy: bool) -> None:
        """Point a split's preferred arm at the body (greedy) or past it (lazy)."""
        if greedy:
            self.insts[split].x, self.insts[split].y = body, after
        else:
            self.insts[split].x, self.insts[split].y = after, body


def _fold_char(ch: str) -> set[str]:
    return {ch, ch.lower(), ch.upper(), ch.casefold()[:1] or ch}


def _fold_class(cls: CharClass) -> CharClass:
    """Add case variants to a class.  Wide ranges are left alone."""
    extra: list[tuple[int, int]] = list(cls.ranges)
    for lo, hi in cls.ranges:
        if hi - lo > 255:
            continue
        for cp in range(lo, hi + 1):
            for variant in _fold_char(chr(cp)):
                extra.append((ord(variant), ord(variant)))
    return CharClass.from_ranges(extra, cls.negated)


def compile_pattern(pattern: str, flags: str = "", full: bool = False) -> Program:
    """Parse and compile ``pattern``.

    ``flags`` is a string of single letters: ``i`` case-insensitive,
    ``s`` dot matches newline, ``m`` ``^``/``$`` match at line boundaries.

    With ``full=True`` an end-of-text assertion is compiled in before the
    final ``match``.  That is how ``fullmatch`` is implemented, and it has to
    happen at compile time: filtering afterwards would be wrong, because the
    VM reports the *preferred* match rather than every match, and for a
    pattern like ``a|ab`` the preferred one is the short one.
    """
    unknown = set(flags) - {"i", "s", "m"}
    if unknown:
        raise ValueError("unknown flags: {}".format("".join(sorted(unknown))))

    ast, ngroups, groupnames = parse(pattern)
    c = Compiler(frozenset(flags))
    c.emit(Inst(SAVE, slot=0))
    c.compile(ast)
    c.emit(Inst(SAVE, slot=1))
    if full:
        c.emit(Inst(ASSERT, kind="eos"))
    c.emit(Inst(MATCH))
    return Program(
        insts=c.insts,
        ngroups=ngroups,
        pattern=pattern,
        groupnames=groupnames,
        flags=frozenset(flags),
    )
