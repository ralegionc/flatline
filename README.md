# flatline

**A regular expression engine that cannot be made to blow up.** Every search runs in
time proportional to `len(text) × len(pattern)` — for every input, with no pathological
case — because it simulates the whole automaton at once instead of backtracking through it.

Comes with a [visual debugger](#the-debugger) that steps the machine one character at a
time, and a `race` command that puts it next to a backtracking engine running the *same
compiled program*.

![python](https://img.shields.io/badge/python-3.10%2B-blue)
![tests](https://img.shields.io/badge/tests-245%20passing-brightgreen)
![dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![port](https://img.shields.io/badge/js%20port-verified%20against%20python-blueviolet)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

## The problem

This pattern is not unusual, and not obviously wrong:

```
^(\w+\s?)*$
```

Give it a long run of word characters ending in something it does not accept, and Python's
`re` — along with Java's `java.util.regex`, JavaScript's `RegExp`, .NET, Ruby, and PCRE —
will not come back. Not slowly. It will not come back.

Measured on this machine, with `(a+)+b` against `"a" * n + "!"`:

| n | `re.search` |
|---:|---:|
| 20 | 0.1 s |
| 24 | 2.3 s |
| 28 | 34.0 s |
| 32 | ~9 minutes *(extrapolated)* |

Each additional character doubles the work. flatline searches 5,000 characters of the same
input in **27 ms**, and its peak memory is two threads.

This is not a benchmark curiosity. It is CWE-1333, it took Cloudflare's global network down
for 27 minutes in July 2019, and it is a denial of service that fits in a form field.

## Why it happens

A backtracking matcher carries a **path**. At each choice point it takes the preferred
branch and remembers the alternative; when the branch fails it unwinds and tries the other.

For `(a+)+b` against `aaaa!`, every way of splitting those four `a`s into groups —
`(aaaa)`, `(aaa)(a)`, `(aa)(aa)`, `(a)(a)(a)(a)`, and the rest — is a distinct path. There
are 2ⁿ⁻¹ of them, and the matcher must exhaust every single one before it can conclude that
there is no `b`. The pattern is ambiguous, the input is long, and the answer is *no*: those
three together are the whole recipe.

A Pike VM carries a **set of states** instead. It follows both arms of every choice at once,
keeps all of them at the same input position, and advances the lot by one character per
step. Two threads sitting on the same instruction at the same position will behave
identically from there on, so the duplicate is dropped — and that one rule caps the thread
list at the length of the program, forever.

```
work per character ≤ len(program)        (nothing can widen the thread list)
characters         = len(text)           (each is visited once)
────────────────────────────────────────────────────────────────────────────
total work         ≤ len(text) × len(program)
```

No input appears anywhere on the right-hand side except as its length. That is the guarantee,
and [`tests/test_linearity.py`](tests/test_linearity.py) asserts it rather than asking you to
take it on faith.

## Install

```bash
git clone https://github.com/<you>/flatline && cd flatline
pip install -e .
```

No dependencies. Python 3.10+.

## Use it as a library

The API mirrors `re`, so swapping the import is a one-line experiment:

```python
import flatline

m = flatline.search(r"(\w+)@(\w+)\.com", "mail bob@example.com now")
m.group()       # 'bob@example.com'
m.groups()      # ('bob', 'example')
m.span(1)       # (5, 8)

flatline.findall(r"\d+", "a1 b22 c333")          # ['1', '22', '333']
flatline.sub(r"\s+", " ", "a   b     c")         # 'a b c'
flatline.compile(r"(?P<y>\d{4})-(?P<m>\d{2})").search("on 2024-03-19").groupdict()
# {'y': '2024', 'm': '03'}
```

Ask any pattern what it cost:

```python
flatline.compile("(a+)+b").stats("a" * 5000 + "!")
# {'matched': False, 'steps': 10002, 'max_threads': 2,
#  'program_length': 9, 'text_length': 5001}
```

## Use it from the terminal

### `flatline race` — the demonstration

```
$ flatline race "(a+)+b" --max-n 20

  (a+)+b   on   'a' * n + '!'
  both engines running the same 9-instruction program

    n    flatline    backtracking   backtracking steps (log scale)
  ----------------------------------------------------------------------------
    1           4              16   ######
    2           6              40   #########
    3           8              92   ###########
    ...                             (rows elided)
   14          30         229,312   ##############################
   15          32         458,684   ################################
   16          34         917,432   #################################
   17          36         gave up   ################################## >
   18          38         gave up   ################################## >

  at n=16: flatline 34 steps, backtracking 917,432 steps  (26,983x)
  the backtracker exceeded 1,000,000 steps at n=17; flatline needed 36
```

flatline's column is exactly `2n + 2`. The other one doubles.

Both columns come from the same compiled program. The backtracker in
[`flatline/backtrack.py`](flatline/backtrack.py) is not a straw man — it returns identical
answers to the Pike VM on every pattern it can finish, which is checked in
`TestEnginesAgree`. The only thing that differs is the execution strategy.

### `flatline explain` — what a pattern compiles to

```
$ flatline explain "a+b"

  a+b   (6 instructions)

    0  save 0
    1  char 'a'                         <- 2
    2  split 1, 3
    3  char 'b'
    4  save 1
    5  match

  2 of these consume input, so no search can take more than
  6 x len(text) thread-steps -- whatever the text is.
```

### `flatline trace` — the thread list at every position

```
$ flatline trace "a|ab" "ab"

  pos  char   live threads (pc: instruction)
  ------------------------------------------------------------------
  0    'a'      2: char 'a'
               4: char 'a'
  1    'b'      7: match <- match
               5: char 'b'
               2: char 'a'
               4: char 'a'
  2    eof    none

  matched 'a' at [0:1]
```

Threads are listed highest priority first. At position 1 the `match` thread outranks the one
still hoping for `b`, which is exactly why `a|ab` prefers the shorter branch — leftmost-first
semantics falling out of list order rather than being special-cased.

### `flatline match` — ordinary use

```
$ flatline match "(\w+)@(\w+)\.com" "mail bob@example.com now"

  mail bob@example.com now
       ^^^^^^^^^^^^^^^
  match [5:20] 'bob@example.com'
    1  'bob' [5:8]
    2  'example' [9:16]

  55 thread-steps, at most 4 live threads, program is 16 instructions
```

## The debugger

`web/index.html` — no build step and no dependencies. Opening the file directly works in
most browsers; if yours restricts local file access, serve the folder instead:

```bash
python -m http.server 8899 --directory web
```

**Part one steps the machine.** Type a pattern and a subject, then scrub through the search
one input position at a time. It shows:

- the **compiled program** as a listing, with control-flow arcs drawn in a gutter the way a
  disassembler draws them — nested loops nest visually, and the preferred arm of each
  `split` is solid where the fallback is dashed;
- **which instructions have live threads right now**, numbered by priority, so you can watch
  the thread list stay narrow while the input grows;
- the **capture slots** each thread is carrying, which is where submatch extraction stops
  being mysterious;
- a running check that peak threads never exceeded the program length.

**Part two runs the race** in the browser and plots it on a log axis, marking the point where
the backtracker exceeds its step budget and is abandoned.

### The visualiser runs a second implementation, and that is a risk

The page needs to step the machine at interaction speed without a server, so `web/engine.js`
is a full JavaScript port of the engine. A second implementation is a second chance to be
wrong, and a debugger that draws something the real engine would not do is worse than no
debugger at all.

So the port is not trusted. `tools/gen_conformance.py` records what the **Python** engine
does across the corpus — compiled programs, match spans, capture spans, *exact step counts*,
peak thread counts, and parse-error offsets — into `web/conformance.js`. That corpus is then
checked twice:

- in the browser on page load, reported in the header (`port verified · 86/86 match the
  Python engine`);
- under Node in [`tests/test_js_port.py`](tests/test_js_port.py), so a drifted port fails the
  Python test suite.

Step counts are the tight constraint. Two engines can agree on every answer while doing
different amounts of work, and the work is the thing this project is about. A second test
regenerates the corpus and fails if the file on disk differs, so a change to the Python
engine cannot leave the recorded expectations describing the old behaviour.

## What it supports

Literals · `.` · character classes `[a-z]` `[^...]` with escapes · alternation · greedy and
lazy `*` `+` `?` · counted repetition `{m}` `{m,}` `{m,n}` and their lazy forms · capturing,
non-capturing and named groups · backreference-free shorthands `\d \w \s \D \W \S` · anchors
`^ $ \A \Z` · word boundaries `\b \B` · flags `i` `s` `m` · `search` `match` `fullmatch`
`finditer` `findall` `sub` `split`.

## What it will not do

**Backreferences.** Not "not yet" — not ever. A pattern containing `\1` is not a regular
language, and no NFA simulation can evaluate one in linear time. Accepting them would mean
quietly abandoning the only thing this engine offers. The parser refuses them and says so:

```
$ flatline match "(a)\1" "aa"
pattern error: backreferences are not supported: a pattern with a backreference is not a
regular language, and cannot be matched in linear time by any NFA simulation. Supporting it
would mean giving up flatline's whole guarantee.
    (a)\1
       ^
  at offset 3
```

**Lookahead and lookbehind.** Refused too, but for a different reason, and the error message
keeps the distinction: these *are* compatible with linear-time matching. They are simply not
implemented yet.

**Speed on ordinary patterns.** This is pure Python against a C extension. On patterns `re`
handles well, `re` wins comfortably, and it should — the claim here is about the worst case,
not the average one. RE2, Rust's `regex`, and Go's `regexp` make the same guarantee in
compiled languages and are what you would reach for in production.

## One deliberate difference from `re`

Given `(a*)*b` matching `"aaab"`, Python reports group 1 as `""` and flatline reports
`"aaa"`. The overall match is identical; only the capture differs.

Perl-family engines run one final empty iteration of the outer star and let it set the group.
An NFA simulation stops the loop when it stops making progress, so the group keeps its last
real value. RE2 and Go's `regexp` diverge from PCRE here for exactly the same reason.

It is pinned down in `TestKnownDivergences` rather than left to be discovered. A divergence
that is written down and tested is a design decision; one that is not is a bug report waiting
to happen.

## Tests

```bash
pytest                              # 245 tests
node tools/check_port.js            # the JavaScript port, on its own
python tools/gen_conformance.py     # regenerate the corpus after engine changes
```

What they actually check:

| File | What it holds the engine to |
|---|---|
| `test_match.py` | spans, captures, greed, priority, anchors, flags, the whole API surface |
| `test_parser.py` | what parses, what is refused, and whether the refusal explains itself |
| `test_differential.py` | **4,000 generated patterns** compared against `re` for span *and* every capture group, plus 600 `finditer` comparisons and a hand-picked corpus of patterns people actually write |
| `test_linearity.py` | the thread bound, linear growth, and that the backtracker really does blow up on the same inputs |
| `test_js_port.py` | the JavaScript port against the Python engine, step counts included |

The differential fuzzer builds random patterns from a grammar that never quantifies a
nullable body, which keeps capture semantics identical between the two engines and makes any
disagreement a real bug rather than the documented divergence above.

`test_linearity.py` is the one worth reading. It is easy to write a test that a fast thing is
fast; the useful assertions are that the *control group fails* — every pattern in the `EVIL`
list must exhaust the backtracker's budget, or it is not evidence of anything — and that
growth is measurably exponential on one side and measurably flat on the other.

## Layout

```
flatline/
  program.py     instruction set, character classes, rendering
  parser.py      pattern text -> AST, with error offsets
  compiler.py    Thompson construction -> flat program
  pike.py        the Pike VM: the linear-time simulation
  backtrack.py   the control group, running the same program
  api.py         re-shaped public API
  trace.py       instrumented runs, serialisable
  cli.py         match / explain / trace / race
web/
  index.html     the debugger, no build step
  engine.js      JavaScript port of the engine
  conformance.js generated: what the Python engine does
tools/
  gen_conformance.py   regenerates the corpus from the Python engine
  check_port.js        runs the corpus against the JavaScript engine
```

## Reading

- Russ Cox, [*Regular Expression Matching Can Be Simple And Fast*](https://swtch.com/~rsc/regexp/regexp1.html) — the article this engine is built out of.
- Ken Thompson, *Regular Expression Search Algorithm*, CACM 11(6), 1968.
- Rob Pike's submatch-capturing VM, described in [part two](https://swtch.com/~rsc/regexp/regexp2.html) of Cox's series.
- Cloudflare, [*Details of the Cloudflare outage on July 2, 2019*](https://blog.cloudflare.com/details-of-the-cloudflare-outage-on-july-2-2019/).

## License

MIT.
