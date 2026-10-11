/* flatline -- JavaScript port of the engine.
 *
 * A direct translation of the Python package: same grammar, same instruction
 * set, same priority rules, same two execution strategies.  It exists so the
 * visualiser can step the machine at 60fps without a server round trip.
 *
 * Because a second implementation is a second chance to be wrong, this one is
 * not trusted on its word: `tools/gen_conformance.py` records what the Python
 * engine does across a corpus of patterns, and `tests/test_js_port.py` runs
 * this file under Node and fails if the two ever disagree.
 *
 * Loads as a plain script in the browser (defining globals) and as a CommonJS
 * module under Node.
 */
"use strict";

/* ------------------------------------------------------------------ *
 * character classes
 * ------------------------------------------------------------------ */

function mkClass(ranges, negated) {
  const sorted = ranges.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged = [];
  for (const [lo, hi] of sorted) {
    const last = merged[merged.length - 1];
    if (last && lo <= last[1] + 1) last[1] = Math.max(last[1], hi);
    else merged.push([lo, hi]);
  }
  return { ranges: merged, negated: !!negated };
}

function classMatches(cls, cp) {
  let lo = 0,
    hi = cls.ranges.length - 1,
    found = false;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const r = cls.ranges[mid];
    if (cp < r[0]) hi = mid - 1;
    else if (cp > r[1]) lo = mid + 1;
    else {
      found = true;
      break;
    }
  }
  return found !== cls.negated;
}

/* Instruction text is compared byte-for-byte against the Python engine's
 * rendering (see tools/gen_conformance.py), so these escaping rules are part
 * of the contract between the two implementations, not a display choice.
 * JSON.stringify is not usable: Python has no way to reproduce it exactly. */
const ESCAPES = { "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r", "\f": "\\f", "\v": "\\v" };

function escapeCp(code) {
  const ch = String.fromCodePoint(code);
  if (ESCAPES[ch]) return ESCAPES[ch];
  if (ch === "]" || ch === "-" || ch === "^") return "\\" + ch;
  if (code < 32 || code === 127) return "\\x" + code.toString(16).padStart(2, "0");
  if (code > 126) return "\\u" + code.toString(16).padStart(4, "0");
  return ch;
}

function quoteChar(ch) {
  const code = ch.codePointAt(0);
  if (ch === "'") return "\\'";
  if (ESCAPES[ch]) return ESCAPES[ch];
  if (code < 32 || code === 127) return "\\x" + code.toString(16).padStart(2, "0");
  if (code > 126) return "\\u" + code.toString(16).padStart(4, "0");
  return ch;
}

function describeClass(cls) {
  const parts = cls.ranges.map(([lo, hi]) =>
    lo === hi ? escapeCp(lo) : hi === lo + 1 ? escapeCp(lo) + escapeCp(hi) : escapeCp(lo) + "-" + escapeCp(hi)
  );
  return "[" + (cls.negated ? "^" : "") + parts.join("") + "]";
}

function complement(ranges) {
  const out = [];
  let prev = 0;
  for (const [lo, hi] of ranges) {
    if (lo > prev) out.push([prev, lo - 1]);
    prev = hi + 1;
  }
  if (prev <= 0x10ffff) out.push([prev, 0x10ffff]);
  return out;
}

const cp = (c) => c.codePointAt(0);

const CLS = {
  d: mkClass([[cp("0"), cp("9")]], false),
  w: mkClass(
    [
      [cp("0"), cp("9")],
      [cp("A"), cp("Z")],
      [cp("_"), cp("_")],
      [cp("a"), cp("z")],
    ],
    false
  ),
  s: mkClass([" ", "\t", "\n", "\r", "\f", "\v"].map((c) => [cp(c), cp(c)]), false),
};
CLS.D = mkClass(CLS.d.ranges, true);
CLS.W = mkClass(CLS.w.ranges, true);
CLS.S = mkClass(CLS.s.ranges, true);

const CHAR_ESCAPES = { n: "\n", t: "\t", r: "\r", f: "\f", v: "\v", "0": "\0", a: "\x07", e: "\x1b" };
const ASSERT_ESCAPES = { b: "wordb", B: "nwordb", A: "bos", Z: "eos" };

const MAX_REPEAT = 1000;

class RegexSyntaxError extends Error {
  constructor(message, pattern, pos) {
    super(message);
    this.name = "RegexSyntaxError";
    this.pattern = pattern;
    this.pos = pos;
    this.detail = message;
  }
}

/* ------------------------------------------------------------------ *
 * parser
 * ------------------------------------------------------------------ */

class Parser {
  constructor(src) {
    this.src = src;
    this.pos = 0;
    this.ngroups = 0;
    this.names = {};
  }
  err(msg, pos) {
    return new RegexSyntaxError(msg, this.src, pos === undefined ? this.pos : pos);
  }
  eof() {
    return this.pos >= this.src.length;
  }
  peek() {
    return this.eof() ? null : this.src[this.pos];
  }
  next() {
    if (this.eof()) throw this.err("unexpected end of pattern");
    return this.src[this.pos++];
  }
  eat(ch) {
    if (this.peek() === ch) {
      this.pos++;
      return true;
    }
    return false;
  }

  parse() {
    const node = this.parseAlt();
    if (!this.eof()) throw this.err("unbalanced ')'");
    return node;
  }

  parseAlt() {
    const branches = [this.parseConcat()];
    while (this.eat("|")) branches.push(this.parseConcat());
    return branches.length === 1 ? branches[0] : { type: "alt", branches };
  }

  parseConcat() {
    const parts = [];
    while (!this.eof() && this.peek() !== "|" && this.peek() !== ")") parts.push(this.parseRepeat());
    if (parts.length === 0) return { type: "empty" };
    return parts.length === 1 ? parts[0] : { type: "concat", parts };
  }

  parseRepeat() {
    let node = this.parseAtom();
    for (;;) {
      const ch = this.peek();
      const start = this.pos;
      let lo, hi;
      if (ch === "*") {
        this.pos++;
        lo = 0;
        hi = null;
      } else if (ch === "+") {
        this.pos++;
        lo = 1;
        hi = null;
      } else if (ch === "?") {
        this.pos++;
        lo = 0;
        hi = 1;
      } else if (ch === "{") {
        const b = this.tryCounted();
        if (!b) break;
        [lo, hi] = b;
      } else break;

      if (node.type === "assert") throw this.err("nothing to repeat: cannot quantify an assertion", start);
      const greedy = !this.eat("?");
      node = { type: "repeat", body: node, lo, hi, greedy };
    }
    return node;
  }

  tryCounted() {
    const save = this.pos;
    this.pos++;
    const lo = this.readDigits();
    if (lo === null) {
      this.pos = save;
      return null;
    }
    let hi;
    if (this.eat("}")) hi = lo;
    else if (this.eat(",")) {
      hi = this.readDigits();
      if (!this.eat("}")) {
        this.pos = save;
        return null;
      }
    } else {
      this.pos = save;
      return null;
    }
    if (hi !== null && hi < lo) throw this.err(`bad repeat bounds: {${lo},${hi}} has max below min`, save);
    const limit = hi === null ? lo : hi;
    if (limit > MAX_REPEAT) throw this.err(`repeat count ${limit} exceeds the limit of ${MAX_REPEAT}`, save);
    return [lo, hi];
  }

  readDigits() {
    const start = this.pos;
    while (!this.eof() && this.src[this.pos] >= "0" && this.src[this.pos] <= "9") this.pos++;
    return this.pos === start ? null : parseInt(this.src.slice(start, this.pos), 10);
  }

  parseAtom() {
    const start = this.pos;
    const ch = this.next();
    if (ch === "(") return this.parseGroup(start);
    if (ch === "[") return { type: "class", cls: this.parseClass(start) };
    if (ch === ".") return { type: "any" };
    if (ch === "^") return { type: "assert", kind: "bol" };
    if (ch === "$") return { type: "assert", kind: "eol" };
    if (ch === "\\") return this.parseEscape(start);
    if (ch === "*" || ch === "+" || ch === "?") throw this.err("nothing to repeat", start);
    return { type: "lit", ch };
  }

  parseGroup(start) {
    let name = null;
    let capturing = true;
    if (this.peek() === "?") {
      this.pos++;
      const kind = this.peek();
      if (kind === ":") {
        this.pos++;
        capturing = false;
      } else if (kind === "=" || kind === "!") {
        throw this.err(UNSUPPORTED.lookaround, start);
      } else if (kind === "<") {
        const after = this.src[this.pos + 1];
        if (after === "=" || after === "!") throw this.err(UNSUPPORTED.lookaround, start);
        this.pos++;
        name = this.readGroupName(start);
      } else if (kind === "P") {
        this.pos++;
        if (!this.eat("<")) throw this.err("expected '<' after '(?P'", start);
        name = this.readGroupName(start);
      } else {
        throw this.err(`unsupported group syntax '(?${kind || ""}'`, start);
      }
    }

    let index = null;
    if (capturing) {
      index = ++this.ngroups;
      if (name !== null) {
        if (name in this.names) throw this.err(`duplicate group name '${name}'`, start);
        this.names[name] = index;
      }
    }
    const body = this.parseAlt();
    if (!this.eat(")")) throw this.err("missing ')' for group opened here", start);
    return { type: "group", body, index, name };
  }

  readGroupName(start) {
    let out = "";
    while (!this.eof() && this.peek() !== ">") out += this.next();
    if (!this.eat(">")) throw this.err("unterminated group name", start);
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(out)) throw this.err(`invalid group name '${out}'`, start);
    return out;
  }

  parseEscape(start) {
    if (this.eof()) throw this.err("pattern ends with a trailing backslash", start);
    const ch = this.next();
    if (CLS[ch]) return { type: "class", cls: CLS[ch] };
    if (ASSERT_ESCAPES[ch]) return { type: "assert", kind: ASSERT_ESCAPES[ch] };
    if (ch >= "1" && ch <= "9") throw this.err(UNSUPPORTED.backreference, start);
    if (ch === "k") throw this.err(UNSUPPORTED.backreference, start);
    return { type: "lit", ch: this.escapeChar(ch, start) };
  }

  escapeChar(ch, start) {
    if (CHAR_ESCAPES[ch] !== undefined) return CHAR_ESCAPES[ch];
    if (ch === "x") return this.readHex(2, start);
    if (ch === "u") return this.readHex(4, start);
    if (ch === "U") return this.readHex(8, start);
    if (/[A-Za-z0-9]/.test(ch)) throw this.err(`unknown escape '\\${ch}'`, start);
    return ch;
  }

  readHex(n, start) {
    const digits = this.src.slice(this.pos, this.pos + n);
    if (digits.length < n || !/^[0-9a-fA-F]+$/.test(digits)) throw this.err(`expected ${n} hex digits in escape`, start);
    this.pos += n;
    return String.fromCodePoint(parseInt(digits, 16));
  }

  parseClass(start) {
    const negated = this.eat("^");
    const ranges = [];
    let first = true;
    for (;;) {
      if (this.eof()) throw this.err("unterminated character class opened here", start);
      if (this.peek() === "]" && !first) {
        this.pos++;
        break;
      }
      first = false;

      const loPos = this.pos;
      let ch = this.next();
      let loChar;
      if (ch === "\\") {
        if (this.eof()) throw this.err("trailing backslash in character class", loPos);
        const esc = this.next();
        if (CLS[esc]) {
          const sub = CLS[esc];
          for (const r of sub.negated ? complement(sub.ranges) : sub.ranges) ranges.push([r[0], r[1]]);
          continue;
        }
        loChar = this.escapeChar(esc, loPos);
      } else loChar = ch;

      const after = this.src[this.pos + 1];
      if (this.peek() === "-" && after !== "]" && after !== undefined) {
        this.pos++;
        const hiPos = this.pos;
        let hiRaw = this.next();
        let hiChar;
        if (hiRaw === "\\") {
          const esc = this.next();
          if (CLS[esc]) throw this.err("a shorthand class cannot be the end of a range", hiPos);
          hiChar = this.escapeChar(esc, hiPos);
        } else hiChar = hiRaw;
        if (cp(hiChar) < cp(loChar)) throw this.err(`range ${loChar}-${hiChar} is out of order`, loPos);
        ranges.push([cp(loChar), cp(hiChar)]);
      } else {
        ranges.push([cp(loChar), cp(loChar)]);
      }
    }
    if (ranges.length === 0) throw this.err("empty character class", start);
    return mkClass(ranges, negated);
  }
}

const UNSUPPORTED = {
  backreference:
    "backreferences are not supported: a pattern with a backreference is not a regular language, " +
    "and cannot be matched in linear time by any NFA simulation. Supporting it would mean giving " +
    "up flatline's whole guarantee.",
  lookaround:
    "lookahead and lookbehind are not supported yet. Unlike backreferences these are compatible " +
    "with linear-time matching, they are simply not implemented.",
};

/* ------------------------------------------------------------------ *
 * compiler
 * ------------------------------------------------------------------ */

function foldChar(ch) {
  return new Set([ch, ch.toLowerCase(), ch.toUpperCase()]);
}

function foldClass(cls) {
  const extra = cls.ranges.map((r) => [r[0], r[1]]);
  for (const [lo, hi] of cls.ranges) {
    if (hi - lo > 255) continue;
    for (let c = lo; c <= hi; c++) {
      for (const v of foldChar(String.fromCodePoint(c))) extra.push([cp(v), cp(v)]);
    }
  }
  return mkClass(extra, cls.negated);
}

function renderInst(inst) {
  switch (inst.op) {
    case "char":
      return "char '" + quoteChar(inst.ch) + "'";
    case "class":
      return "class " + describeClass(inst.cls);
    case "any":
      return "any" + (inst.dotall ? " (dotall)" : "");
    case "match":
      return "match";
    case "jmp":
      return "jmp " + inst.x;
    case "split":
      return `split ${inst.x}, ${inst.y}`;
    case "save":
      return "save " + inst.slot;
    case "assert":
      return "assert " + inst.kind;
  }
  return inst.op;
}

class Compiler {
  constructor(flags) {
    this.insts = [];
    this.ignorecase = flags.includes("i");
    this.dotall = flags.includes("s");
    this.multiline = flags.includes("m");
  }
  emit(inst) {
    this.insts.push(inst);
    return this.insts.length - 1;
  }
  get here() {
    return this.insts.length;
  }

  compile(node) {
    switch (node.type) {
      case "empty":
        return;
      case "lit": {
        if (this.ignorecase) {
          const folded = [...foldChar(node.ch)];
          if (folded.length > 1) {
            this.emit({ op: "class", cls: mkClass(folded.map((c) => [cp(c), cp(c)]), false) });
            return;
          }
        }
        this.emit({ op: "char", ch: node.ch });
        return;
      }
      case "class":
        this.emit({ op: "class", cls: this.ignorecase ? foldClass(node.cls) : node.cls });
        return;
      case "any":
        this.emit({ op: "any", dotall: this.dotall });
        return;
      case "assert": {
        let kind = node.kind;
        if (!this.multiline) kind = kind === "bol" ? "bos" : kind === "eol" ? "eot" : kind;
        this.emit({ op: "assert", kind });
        return;
      }
      case "concat":
        for (const p of node.parts) this.compile(p);
        return;
      case "alt": {
        const jumps = [];
        for (const branch of node.branches.slice(0, -1)) {
          const split = this.emit({ op: "split", x: 0, y: 0 });
          this.insts[split].x = this.here;
          this.compile(branch);
          jumps.push(this.emit({ op: "jmp", x: 0 }));
          this.insts[split].y = this.here;
        }
        this.compile(node.branches[node.branches.length - 1]);
        for (const j of jumps) this.insts[j].x = this.here;
        return;
      }
      case "group":
        if (node.index === null) {
          this.compile(node.body);
          return;
        }
        this.emit({ op: "save", slot: 2 * node.index });
        this.compile(node.body);
        this.emit({ op: "save", slot: 2 * node.index + 1 });
        return;
      case "repeat":
        this.repeat(node);
        return;
    }
    throw new Error("unknown node " + node.type);
  }

  repeat(node) {
    const { body, lo, hi, greedy } = node;
    if (hi === 0) return;
    if (lo === 0 && hi === null) this.star(body, greedy);
    else if (lo === 1 && hi === null) this.plus(body, greedy);
    else if (lo === 0 && hi === 1) this.opt(body, greedy);
    else if (hi === null) {
      for (let i = 0; i < lo - 1; i++) this.compile(body);
      this.plus(body, greedy);
    } else {
      for (let i = 0; i < lo; i++) this.compile(body);
      this.nestedOpt(body, hi - lo, greedy);
    }
  }

  arms(split, bodyPc, afterPc, greedy) {
    this.insts[split].x = greedy ? bodyPc : afterPc;
    this.insts[split].y = greedy ? afterPc : bodyPc;
  }

  star(body, greedy) {
    const top = this.emit({ op: "split", x: 0, y: 0 });
    const bodyPc = this.here;
    this.compile(body);
    this.emit({ op: "jmp", x: top });
    this.arms(top, bodyPc, this.here, greedy);
  }

  plus(body, greedy) {
    const bodyPc = this.here;
    this.compile(body);
    const split = this.emit({ op: "split", x: 0, y: 0 });
    this.arms(split, bodyPc, this.here, greedy);
  }

  opt(body, greedy) {
    const split = this.emit({ op: "split", x: 0, y: 0 });
    const bodyPc = this.here;
    this.compile(body);
    this.arms(split, bodyPc, this.here, greedy);
  }

  nestedOpt(body, count, greedy) {
    if (count <= 0) return;
    const split = this.emit({ op: "split", x: 0, y: 0 });
    const bodyPc = this.here;
    this.compile(body);
    this.nestedOpt(body, count - 1, greedy);
    this.arms(split, bodyPc, this.here, greedy);
  }
}

function compileProgram(pattern, flags, full) {
  flags = flags || "";
  const p = new Parser(pattern);
  const ast = p.parse();
  const c = new Compiler(flags);
  c.emit({ op: "save", slot: 0 });
  c.compile(ast);
  c.emit({ op: "save", slot: 1 });
  if (full) c.emit({ op: "assert", kind: "eos" });
  c.emit({ op: "match" });
  return {
    insts: c.insts,
    ngroups: p.ngroups,
    names: p.names,
    pattern,
    flags,
    nslots: 2 * (p.ngroups + 1),
  };
}

/* ------------------------------------------------------------------ *
 * shared execution helpers
 * ------------------------------------------------------------------ */

const WORD_RE = /[A-Za-z0-9_]/;
const isWord = (text, i) => i >= 0 && i < text.length && WORD_RE.test(text[i]);

function checkAssert(kind, text, sp) {
  switch (kind) {
    case "bos":
      return sp === 0;
    case "eos":
      return sp === text.length;
    case "eot":
      return sp === text.length || (sp === text.length - 1 && text[sp] === "\n");
    case "bol":
      return sp === 0 || text[sp - 1] === "\n";
    case "eol":
      return sp === text.length || text[sp] === "\n";
    case "wordb":
      return isWord(text, sp - 1) !== isWord(text, sp);
    case "nwordb":
      return isWord(text, sp - 1) === isWord(text, sp);
  }
  throw new Error("unknown assertion " + kind);
}

function consumes(inst, ch) {
  if (inst.op === "char") return ch === inst.ch;
  if (inst.op === "class") return classMatches(inst.cls, ch.codePointAt(0));
  if (inst.op === "any") return inst.dotall || ch !== "\n";
  return false;
}

/* ------------------------------------------------------------------ *
 * the Pike VM
 * ------------------------------------------------------------------ */

function addThread(prog, text, list, seen, pc, sp, saved) {
  const stack = [[pc, saved]];
  while (stack.length) {
    const [p, sv] = stack.pop();
    if (seen.has(p)) continue;
    seen.add(p);
    const inst = prog.insts[p];
    if (inst.op === "jmp") stack.push([inst.x, sv]);
    else if (inst.op === "split") {
      stack.push([inst.y, sv]);
      stack.push([inst.x, sv]);
    } else if (inst.op === "save") {
      const next = sv.slice();
      next[inst.slot] = sp;
      stack.push([p + 1, next]);
    } else if (inst.op === "assert") {
      if (checkAssert(inst.kind, text, sp)) stack.push([p + 1, sv]);
    } else {
      list.push([p, sv]);
    }
  }
}

/* Runs the VM.  `onStep(sp, threads, matched)` is called once per input
 * offset with the live thread list, which is what the visualiser records. */
function pikeRun(prog, text, opts) {
  opts = opts || {};
  const start = opts.start || 0;
  const anchored = !!opts.anchored;
  const onStep = opts.onStep;

  let clist = [],
    cseen = new Set(),
    nlist = [],
    nseen = new Set();
  let matched = null,
    steps = 0,
    maxThreads = 0;
  const blank = new Array(prog.nslots).fill(null);

  let sp = start;
  for (;;) {
    if (matched === null && (!anchored || sp === start)) addThread(prog, text, clist, cseen, 0, sp, blank);
    if (onStep) onStep(sp, clist.map((t) => [t[0], t[1].slice()]), matched !== null);
    if (clist.length === 0 && (matched !== null || anchored)) break;

    maxThreads = Math.max(maxThreads, clist.length);
    nlist = [];
    nseen = new Set();
    const ch = sp < text.length ? text[sp] : "";

    for (let i = 0; i < clist.length; i++) {
      const [pc, saved] = clist[i];
      steps++;
      const inst = prog.insts[pc];
      if (inst.op === "match") {
        matched = saved;
        clist.length = i + 1; // every thread below this one is lower priority
        break;
      }
      if (ch && consumes(inst, ch)) addThread(prog, text, nlist, nseen, pc + 1, sp + 1, saved);
    }

    if (sp >= text.length) break;
    sp++;
    clist = nlist;
    cseen = nseen;
  }
  return { saved: matched, steps, maxThreads };
}

/* ------------------------------------------------------------------ *
 * the backtracker (the control group)
 * ------------------------------------------------------------------ */

const DEFAULT_MAX_STEPS = 1000000;

function backtrackRun(prog, text, opts) {
  opts = opts || {};
  const start = opts.start || 0;
  const anchored = !!opts.anchored;
  const maxSteps = opts.maxSteps === undefined ? DEFAULT_MAX_STEPS : opts.maxSteps;
  const blank = new Array(prog.nslots).fill(null);
  const lastStart = anchored ? start : text.length;
  let steps = 0;
  let peakDepth = 0;

  for (let sp0 = start; sp0 <= lastStart; sp0++) {
    const stack = [[0, sp0, blank]];
    while (stack.length) {
      steps++;
      if (steps > maxSteps) return { saved: null, steps: steps - 1, gaveUp: true, peakDepth };
      peakDepth = Math.max(peakDepth, stack.length);

      const [pc, sp, saved] = stack.pop();
      const inst = prog.insts[pc];
      if (inst.op === "match") return { saved, steps, gaveUp: false, peakDepth };
      if (inst.op === "jmp") stack.push([inst.x, sp, saved]);
      else if (inst.op === "split") {
        stack.push([inst.y, sp, saved]);
        stack.push([inst.x, sp, saved]);
      } else if (inst.op === "save") {
        const next = saved.slice();
        next[inst.slot] = sp;
        stack.push([pc + 1, sp, next]);
      } else if (inst.op === "assert") {
        if (checkAssert(inst.kind, text, sp)) stack.push([pc + 1, sp, saved]);
      } else if (sp < text.length && consumes(inst, text[sp])) {
        stack.push([pc + 1, sp + 1, saved]);
      }
    }
  }
  return { saved: null, steps, gaveUp: false, peakDepth };
}

/* ------------------------------------------------------------------ *
 * convenience API
 * ------------------------------------------------------------------ */

function search(pattern, text, flags) {
  const prog = compileProgram(pattern, flags, false);
  const r = pikeRun(prog, text);
  if (!r.saved) return null;
  return { span: [r.saved[0], r.saved[1]], saved: r.saved, prog };
}

function trace(pattern, text, flags) {
  const prog = compileProgram(pattern, flags, false);
  const steps = [];
  const r = pikeRun(prog, text, {
    onStep: (sp, threads, matched) => {
      steps.push({ sp, char: sp < text.length ? text[sp] : null, matched, threads });
    },
  });
  return { prog, steps, saved: r.saved, maxThreads: r.maxThreads, totalSteps: r.steps };
}

const flatline = {
  compileProgram,
  pikeRun,
  backtrackRun,
  search,
  trace,
  renderInst,
  describeClass,
  RegexSyntaxError,
  DEFAULT_MAX_STEPS,
  UNSUPPORTED,
};

if (typeof module !== "undefined" && module.exports) module.exports = flatline;
if (typeof window !== "undefined") window.flatline = flatline;
