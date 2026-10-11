"""The guarantee, as an assertion rather than a claim in a README.

Three things are checked here:

1. The thread list never exceeds the program length.  This is the invariant
   every other bound follows from.
2. Work grows linearly in the length of the input, including on the patterns
   that are known to destroy backtracking engines.
3. The backtracker -- running the *same compiled program* -- does blow up on
   exactly those patterns, which is what makes the comparison meaningful
   rather than rhetorical.
"""

from __future__ import annotations

import time

import pytest

import flatline

#: Patterns with catastrophic backtracking behaviour, each with a repeated
#: unit and a suffix.  The suffix matters: a backtracker only does the
#: exponential work when the match ultimately *fails*, because that is what
#: forces it to enumerate every way of splitting the input it already consumed.
#: A ReDoS payload is always a long promising prefix followed by one character
#: that dashes it.
EVIL = [
    ("(a+)+b", "a", "!"),          # the textbook case
    ("(a|a)*b", "a", "!"),         # ambiguous alternation under a star
    ("(a*)*b", "a", "!"),          # nullable body under a star
    ("(x+x+)+y", "x", "!"),        # the Java/StackExchange shape
    ("(a|aa)+c", "a", "!"),        # overlapping alternatives
    # The shape behind several real ReDoS advisories.  The payload is an
    # unbroken run of word characters on purpose: inserting the spaces this
    # pattern expects would force a unique split and cost the backtracker
    # nothing.  It is ambiguity, not length, that does the damage.
    (r"^(\w+\s?)*$", "a", "!"),
]


def evil_input(unit: str, suffix: str, n: int) -> str:
    return unit * n + suffix


class TestThreadBound:
    """No input can make the thread list wider than the program."""

    @pytest.mark.parametrize("pattern,unit,suffix", EVIL)
    def test_threads_bounded_by_program_size(self, pattern: str, unit: str, suffix: str) -> None:
        p = flatline.compile(pattern)
        for n in (10, 50, 200):
            stats = p.stats(evil_input(unit, suffix, n))
            assert stats["max_threads"] <= stats["program_length"], (
                "thread list outgrew the program on {!r} at n={}".format(pattern, n)
            )

    def test_bound_holds_for_ordinary_patterns_too(self) -> None:
        p = flatline.compile(r"(\w+)@(\w+)\.(com|org|net)")
        stats = p.stats("x" * 500 + " bob@example.com")
        assert stats["max_threads"] <= stats["program_length"]


class TestLinearWork:
    @pytest.mark.parametrize("pattern,unit,suffix", EVIL)
    def test_work_grows_linearly(self, pattern: str, unit: str, suffix: str) -> None:
        """Doubling the input roughly doubles the work -- never squares it."""
        p = flatline.compile(pattern)
        small = p.stats(evil_input(unit, suffix, 100))["steps"]
        large = p.stats(evil_input(unit, suffix, 200))["steps"]

        # Exactly linear would be 2.0.  Allow generous slack for the constant
        # factor while still failing loudly on anything super-linear.
        ratio = large / max(small, 1)
        assert ratio < 2.6, "work grew {:.2f}x for a 2x input on {!r}".format(ratio, pattern)

    @pytest.mark.parametrize("pattern,unit,suffix", EVIL)
    def test_work_respects_the_formal_bound(self, pattern: str, unit: str, suffix: str) -> None:
        p = flatline.compile(pattern)
        stats = p.stats(evil_input(unit, suffix, 300))
        assert stats["steps"] <= stats["text_length"] * stats["program_length"], (
            "exceeded the O(n*m) bound on {!r}".format(pattern)
        )

    @pytest.mark.parametrize("pattern,unit,suffix", EVIL)
    def test_finishes_fast_in_wall_clock_terms(self, pattern: str, unit: str, suffix: str) -> None:
        """The bound is not just theoretical -- it finishes while you watch."""
        p = flatline.compile(pattern)
        start = time.perf_counter()
        p.search(evil_input(unit, suffix, 2000))
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, "took {:.2f}s on 2000 chars of {!r}".format(elapsed, pattern)


class TestBacktrackerActuallyBlowsUp:
    """The control group has to fail, or the comparison proves nothing."""

    @pytest.mark.parametrize("pattern,unit,suffix", EVIL)
    def test_backtracker_exhausts_its_budget(self, pattern: str, unit: str, suffix: str) -> None:
        p = flatline.compile(pattern)
        result = p.backtrack(evil_input(unit, suffix, 40), max_steps=2_000_000)
        assert result.gave_up, (
            "{!r} did not defeat the backtracker; it is not a good example".format(pattern)
        )

    def test_growth_is_visibly_exponential(self) -> None:
        p = flatline.compile("(a+)+b")
        counts = []
        for n in range(8, 17):
            r = p.backtrack("a" * n, max_steps=50_000_000)
            assert not r.gave_up, "budget too small to observe the growth at n={}".format(n)
            counts.append(r.steps)

        # Each extra character should roughly double the work.
        ratios = [b / a for a, b in zip(counts, counts[1:])]
        assert min(ratios) > 1.7, "growth is not exponential: {}".format(ratios)

    def test_pike_stays_flat_on_the_same_inputs(self) -> None:
        p = flatline.compile("(a+)+b")
        steps = [p.stats("a" * n)["steps"] for n in range(8, 17)]
        ratios = [b / a for a, b in zip(steps, steps[1:])]
        assert max(ratios) < 1.4, "the flat line is not flat: {}".format(ratios)


class TestEnginesAgree:
    """Where the backtracker can finish, it must give the same answer.

    This is what rules out the cheap explanation for the speed difference --
    that the fast engine is fast because it is doing something else.
    """

    CASES = [
        (r"(\w+)@(\w+)\.com", "mail bob@example.com now"),
        (r"a|ab", "ab"),
        (r"(a)(b)?c", "ac"),
        (r"[A-Za-z_]\w*", "  foo_bar9 "),
        (r"a{2,4}", "aaaaa"),
        (r"<.*?>", "<a><b>"),
        (r"\bcat\b", "the cat sat"),
        (r"^(?:GET|POST) (/\S*)$", "GET /index.html"),
        (r"(\d+)\.(\d+)", "pi is 3.14159"),
        (r"x*", "yyy"),
    ]

    @pytest.mark.parametrize("pattern,text", CASES)
    def test_same_answer(self, pattern: str, text: str) -> None:
        p = flatline.compile(pattern)
        pike = p.search(text)
        bt = p.backtrack(text)

        assert not bt.gave_up
        assert (pike is not None) == bt.matched
        if pike is not None:
            assert pike.span() == (bt.saved[0], bt.saved[1])
            assert list(pike._saved) == list(bt.saved)
