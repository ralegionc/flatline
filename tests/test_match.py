"""Matching semantics: spans, captures, greed, anchors, flags."""

from __future__ import annotations

import pytest

import flatline


class TestBasicMatching:
    @pytest.mark.parametrize(
        "pattern,text,span",
        [
            ("a", "a", (0, 1)),
            ("a", "bca", (2, 3)),
            ("abc", "xxabcxx", (2, 5)),
            ("a", "b", None),
            ("", "abc", (0, 0)),
            (".", "x", (0, 1)),
            (".", "\n", None),
            ("a|b", "b", (0, 1)),
            ("a*", "", (0, 0)),
            ("a*", "aaa", (0, 3)),
            ("a+", "", None),
            ("a+", "aaa", (0, 3)),
            ("a?", "", (0, 0)),
            ("ab?c", "ac", (0, 2)),
            ("ab?c", "abc", (0, 3)),
        ],
    )
    def test_span(self, pattern: str, text: str, span: tuple[int, int] | None) -> None:
        m = flatline.search(pattern, text)
        assert (m.span() if m else None) == span

    def test_dotall_flag(self) -> None:
        assert flatline.search(".", "\n") is None
        assert flatline.search(".", "\n", flags="s").span() == (0, 1)


class TestGreedAndPriority:
    def test_greedy_takes_the_most(self) -> None:
        assert flatline.search("a*", "aaa").span() == (0, 3)
        assert flatline.search("<.*>", "<a><b>").group() == "<a><b>"

    def test_lazy_takes_the_least(self) -> None:
        assert flatline.search("a*?", "aaa").span() == (0, 0)
        assert flatline.search("<.*?>", "<a><b>").group() == "<a>"
        assert flatline.search("a+?b", "aaab").span() == (0, 4)

    def test_alternation_prefers_the_left_branch(self) -> None:
        # Leftmost-first, not leftmost-longest: 'a' wins even though 'ab' is
        # longer.  This is Perl semantics, and it is what makes the ordering of
        # threads in the VM load-bearing.
        assert flatline.search("a|ab", "ab").group() == "a"
        assert flatline.search("ab|a", "ab").group() == "ab"

    def test_leftmost_beats_longer(self) -> None:
        assert flatline.search("a+", "b aa aaa").span() == (2, 4)


class TestCaptures:
    def test_simple_groups(self) -> None:
        m = flatline.search(r"(\d+)-(\d+)", "tel 555-1234 x")
        assert m.groups() == ("555", "1234")
        assert m.span(1) == (4, 7)
        assert m.span(2) == (8, 12)

    def test_unmatched_group_is_none(self) -> None:
        m = flatline.search(r"(a)(b)?c", "ac")
        assert m.groups() == ("a", None)
        assert m.span(2) == (-1, -1)

    def test_group_default(self) -> None:
        m = flatline.search(r"(a)(b)?c", "ac")
        assert m.groups(default="") == ("a", "")

    def test_repeated_group_keeps_the_last_iteration(self) -> None:
        m = flatline.search(r"(?:(\w)-)+", "a-b-c-")
        assert m.group(1) == "c"

    def test_named_groups(self) -> None:
        m = flatline.search(r"(?P<year>\d{4})-(?P<month>\d{2})", "on 2024-03-19")
        assert m.group("year") == "2024"
        assert m.group("month") == "03"
        assert m.groupdict() == {"year": "2024", "month": "03"}

    def test_bad_group_raises(self) -> None:
        m = flatline.search("(a)", "a")
        with pytest.raises(IndexError):
            m.group(2)
        with pytest.raises(IndexError):
            m.group("nope")

    def test_nested_groups(self) -> None:
        m = flatline.search(r"((a)(b))c", "abc")
        assert m.groups() == ("ab", "a", "b")


class TestAnchors:
    def test_start_and_end(self) -> None:
        assert flatline.search("^abc$", "abc").span() == (0, 3)
        assert flatline.search("^abc$", "xabc") is None
        assert flatline.search("^abc$", "abcx") is None

    def test_dollar_allows_one_trailing_newline(self) -> None:
        # Perl and Python both do this; \Z does not.
        assert flatline.search("abc$", "abc\n") is not None
        assert flatline.search(r"abc\Z", "abc\n") is None
        assert flatline.search(r"abc\Z", "abc") is not None

    def test_multiline_flag(self) -> None:
        text = "one\ntwo\nthree"
        assert [m.group() for m in flatline.compile("^\\w+", "m").finditer(text)] == [
            "one",
            "two",
            "three",
        ]
        assert [m.group() for m in flatline.compile("^\\w+").finditer(text)] == ["one"]

    @pytest.mark.parametrize(
        "pattern,text,expected",
        [
            (r"\bcat\b", "the cat sat", (4, 7)),
            (r"\bcat\b", "concatenate", None),
            (r"\Bcat\B", "concatenate", (3, 6)),
            (r"\bcat", "cat", (0, 3)),
        ],
    )
    def test_word_boundaries(self, pattern, text, expected) -> None:
        m = flatline.search(pattern, text)
        assert (m.span() if m else None) == expected


class TestCharacterClasses:
    @pytest.mark.parametrize(
        "pattern,text,expected",
        [
            ("[abc]+", "xxabcaxx", "abca"),
            ("[^abc]+", "abxyzab", "xyz"),
            ("[a-z]+", "ABCdefGHI", "def"),
            ("[]]", "]", "]"),
            ("[-a]+", "-a-", "-a-"),
            ("[a-]+", "a-", "a-"),
            (r"[\d]+", "ab123", "123"),
            (r"[\w.]+", " a.b ", "a.b"),
            (r"[^\d]+", "12ab34", "ab"),
        ],
    )
    def test_classes(self, pattern: str, text: str, expected: str) -> None:
        assert flatline.search(pattern, text).group() == expected


class TestCountedRepetition:
    @pytest.mark.parametrize(
        "pattern,text,expected",
        [
            ("a{3}", "aaaa", "aaa"),
            ("a{3}", "aa", None),
            ("a{2,4}", "aaaaa", "aaaa"),
            ("a{2,}", "aaaaa", "aaaaa"),
            ("a{0,2}", "aaa", "aa"),
            ("a{0}b", "b", "b"),
            ("(ab){2}", "ababab", "abab"),
            ("a{2,4}?", "aaaa", "aa"),
        ],
    )
    def test_counts(self, pattern: str, text: str, expected: str | None) -> None:
        m = flatline.search(pattern, text)
        assert (m.group() if m else None) == expected


class TestCaseInsensitive:
    def test_literals(self) -> None:
        assert flatline.search("hello", "HELLO", flags="i").group() == "HELLO"
        assert flatline.search("HeLLo", "hello", flags="i").group() == "hello"
        assert flatline.search("hello", "HELLO") is None

    def test_classes(self) -> None:
        assert flatline.search("[a-f]+", "ABCXYZ", flags="i").group() == "ABC"


class TestApiSurface:
    def test_match_is_anchored(self) -> None:
        assert flatline.match("bc", "abc") is None
        assert flatline.match("ab", "abc").span() == (0, 2)

    def test_fullmatch_needs_the_whole_string(self) -> None:
        assert flatline.fullmatch("ab", "abc") is None
        assert flatline.fullmatch("abc", "abc").span() == (0, 3)
        # The whole point of compiling fullmatch separately: the preferred
        # branch here is the short one, but only the long one reaches the end.
        assert flatline.fullmatch("a|ab", "ab").group() == "ab"

    def test_finditer(self) -> None:
        spans = [m.span() for m in flatline.finditer(r"\d+", "a1 b22 c333")]
        assert spans == [(1, 2), (4, 6), (8, 11)]

    def test_finditer_terminates_on_empty_matches(self) -> None:
        assert len(list(flatline.finditer("a*", "bbb"))) == 4

    def test_findall_shapes(self) -> None:
        assert flatline.findall(r"\d+", "a1 b22") == ["1", "22"]
        assert flatline.findall(r"(\d)(\w)", "1a 2b") == [("1", "a"), ("2", "b")]
        assert flatline.findall(r"(\d)", "1 2") == ["1", "2"]

    def test_sub(self) -> None:
        assert flatline.sub(r"\s+", " ", "a   b     c") == "a b c"
        assert flatline.sub(r"\d", "#", "a1b2c3", count=2) == "a#b#c3"

    def test_split(self) -> None:
        assert flatline.compile(r",\s*").split("a, b,c") == ["a", "b", "c"]

    def test_pattern_metadata(self) -> None:
        p = flatline.compile(r"(?P<x>a)(b)")
        assert p.groups == 2
        assert p.groupindex == {"x": 1}
        assert "flatline.compile" in repr(p)

    def test_compile_is_cached(self) -> None:
        assert flatline.compile("abc") is flatline.compile("abc")
        assert flatline.compile("abc") is not flatline.compile("abc", "i")

    def test_match_repr(self) -> None:
        assert "span=(0, 1)" in repr(flatline.search("a", "abc"))

    def test_search_from_pos(self) -> None:
        assert flatline.compile("a").search("aaa", pos=2).span() == (2, 3)
