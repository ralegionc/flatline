"""Differential testing against the standard library's ``re``.

``re`` is a mature backtracking engine, so where flatline and ``re`` disagree,
flatline is the one that needs to justify itself.  The generator below builds
random patterns and random subjects and asserts the two agree on the span and
on every capture group.

One deliberate restriction: the generator never quantifies a body that can
match the empty string.  Patterns like ``(a*)*`` are where engines legitimately
diverge on *capture* semantics -- Perl runs a final empty iteration and sets
the group to ``""``, an NFA simulation stops when the loop stops making
progress and leaves the group at its last non-empty value.  RE2 and Go's
``regexp`` diverge from PCRE here for the same reason.  The overall span still
agrees, and :class:`TestKnownDivergences` pins the difference down explicitly
rather than leaving it undiscovered.
"""

from __future__ import annotations

import random
import re

import pytest

import flatline

ALPHABET = "abc"


# --- pattern generator ------------------------------------------------------


def gen_atom(rng: random.Random) -> tuple[str, bool]:
    """Return ``(pattern, nullable)`` for a leaf."""
    roll = rng.random()
    if roll < 0.55:
        return rng.choice(ALPHABET), False
    if roll < 0.75:
        return "[" + "".join(rng.sample(ALPHABET, rng.randint(1, 2))) + "]", False
    if roll < 0.85:
        return "[^" + rng.choice(ALPHABET) + "]", False
    return ".", False


def gen(rng: random.Random, depth: int) -> tuple[str, bool]:
    """Return ``(pattern, nullable)``.  ``nullable`` means it can match ""."""
    if depth <= 0:
        return gen_atom(rng)

    roll = rng.random()

    if roll < 0.30:  # concatenation
        parts = [gen(rng, depth - 1) for _ in range(rng.randint(2, 3))]
        return "".join(p for p, _ in parts), all(n for _, n in parts)

    if roll < 0.50:  # alternation, parenthesised so it composes safely
        left, ln = gen(rng, depth - 1)
        right, rn = gen(rng, depth - 1)
        capturing = rng.random() < 0.5
        open_ = "(" if capturing else "(?:"
        return "{}{}|{})".format(open_, left, right), ln or rn

    if roll < 0.65:  # group
        body, nullable = gen(rng, depth - 1)
        capturing = rng.random() < 0.6
        return ("({})" if capturing else "(?:{})").format(body), nullable

    # quantifier -- only ever applied to a body that must consume something,
    # which keeps capture semantics identical between the two engines
    body, nullable = gen(rng, depth - 1)
    if nullable:
        return body, nullable
    if len(body) > 1 and not (body.startswith("(") and body.endswith(")")):
        body = "(?:{})".format(body)
    quant = rng.choice(["*", "+", "?", "*?", "+?", "??", "{1,2}", "{2}", "{0,2}"])
    still_nullable = quant[0] in "*?" or quant.startswith("{0")
    return body + quant, still_nullable


def gen_subject(rng: random.Random) -> str:
    n = rng.randint(0, 8)
    return "".join(rng.choice(ALPHABET + "d") for _ in range(n))


# --- the tests --------------------------------------------------------------


def assert_agrees(pattern: str, text: str) -> None:
    mine = flatline.search(pattern, text)
    theirs = re.search(pattern, text)

    assert (mine is None) == (theirs is None), (
        "disagree on whether {!r} matches {!r}: flatline={} re={}".format(
            pattern, text, mine, theirs
        )
    )
    if mine is None or theirs is None:
        return

    assert mine.span() == theirs.span(), "span differs for {!r} on {!r}: {} vs {}".format(
        pattern, text, mine.span(), theirs.span()
    )
    assert mine.groups() == theirs.groups(), (
        "groups differ for {!r} on {!r}: {} vs {}".format(
            pattern, text, mine.groups(), theirs.groups()
        )
    )


class TestFuzz:
    @pytest.mark.parametrize("seed", range(40))
    def test_random_patterns_agree_with_re(self, seed: int) -> None:
        """Each seed is a self-contained, reproducible batch of 100 cases."""
        rng = random.Random(seed)
        for _ in range(100):
            pattern, _ = gen(rng, depth=rng.randint(1, 3))
            try:
                re.compile(pattern)
            except re.error:  # pragma: no cover - generator should not emit these
                continue
            assert_agrees(pattern, gen_subject(rng))

    @pytest.mark.parametrize("seed", range(10))
    def test_finditer_agrees_with_re(self, seed: int) -> None:
        rng = random.Random(1000 + seed)
        for _ in range(60):
            pattern, nullable = gen(rng, depth=rng.randint(1, 2))
            if nullable:
                continue  # empty-match iteration rules are their own question
            text = gen_subject(rng)
            mine = [m.span() for m in flatline.finditer(pattern, text)]
            theirs = [m.span() for m in re.finditer(pattern, text)]
            assert mine == theirs, "finditer differs for {!r} on {!r}".format(pattern, text)


class TestHandPickedCorpus:
    """Patterns people actually write."""

    CASES = [
        (r"\w+@\w+\.\w+", "contact bob@example.com today"),
        (r"(\d{4})-(\d{2})-(\d{2})", "due 2024-03-19 sharp"),
        (r"^\s*#\s*(.*)$", "  # a heading"),
        (r"https?://[\w./-]+", "see http://example.com/x for more"),
        (r"(\w+)\s*=\s*(\w+)", "key = value"),
        (r"[A-Z][a-z]+(?: [A-Z][a-z]+)*", "Hello There World"),
        (r"\d+(\.\d+)?", "price 12.50 usd"),
        (r"<(\w+)>.*?</\1>", None),  # backreference: refused, see test below
        (r"(?:\d{1,3}\.){3}\d{1,3}", "ip 192.168.0.1 here"),
        (r"'[^']*'", "say 'hello there' now"),
        (r"\b\w{5}\b", "some short words appear"),
        (r"a{2,4}b{1,2}", "xxaaabbxx"),
        (r"^(?:GET|POST) (/\S*) HTTP/1\.[01]$", "GET /index.html HTTP/1.1"),
        (r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", "value -1.5e10 end"),
    ]

    @pytest.mark.parametrize("pattern,text", [c for c in CASES if c[1] is not None])
    def test_realistic_patterns(self, pattern: str, text: str) -> None:
        assert_agrees(pattern, text)

    def test_the_backreference_case_is_refused_not_wrong(self) -> None:
        with pytest.raises(flatline.RegexSyntaxError):
            flatline.compile(r"<(\w+)>.*?</\1>")


class TestKnownDivergences:
    """Places flatline deliberately differs from ``re``, pinned down.

    A divergence that is written down and tested is a design decision.  One
    that is not is a bug waiting to be reported by a user.
    """

    def test_empty_iteration_capture(self) -> None:
        # Perl runs one final empty iteration of the outer star and sets the
        # group to "".  flatline stops the loop when it stops consuming, so the
        # group keeps its last real value.  The overall match is identical.
        text = "aaab"
        mine = flatline.search(r"(a*)*b", text)
        theirs = re.search(r"(a*)*b", text)

        assert mine.span() == theirs.span() == (0, 4)
        assert mine.group(1) == "aaa"
        assert theirs.group(1) == ""

    def test_overall_span_still_agrees_on_nullable_loops(self) -> None:
        for pattern in [r"(a*)*b", r"(a?)*b", r"(?:a*)*b", r"(a*)+b"]:
            for text in ["b", "ab", "aaab", "c"]:
                mine = flatline.search(pattern, text)
                theirs = re.search(pattern, text)
                assert (mine is None) == (theirs is None)
                if mine is not None:
                    assert mine.span() == theirs.span(), (pattern, text)
