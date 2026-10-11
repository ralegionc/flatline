/* Run the conformance corpus against the JavaScript engine and report on stdout.
 *
 *   node tools/check_port.js
 *
 * Exits non-zero if anything disagrees with what the Python engine recorded.
 * tests/test_js_port.py drives this, so a broken port fails the Python suite.
 */
"use strict";

const path = require("path");
const flatline = require(path.join(__dirname, "..", "web", "engine.js"));
const CONFORMANCE = require(path.join(__dirname, "..", "web", "conformance.js"));

const failures = [];
let checked = 0;

function eq(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

for (const c of CONFORMANCE.cases) {
  checked++;
  const label = `${JSON.stringify(c.pattern)} on ${JSON.stringify(c.text)}`;
  let prog;
  try {
    prog = flatline.compileProgram(c.pattern, c.flags, false);
  } catch (e) {
    failures.push(`${label}: compile threw ${e.message}`);
    continue;
  }

  const rendered = prog.insts.map(flatline.renderInst);
  if (!eq(rendered, c.program)) {
    failures.push(`${label}: program differs\n    python: ${JSON.stringify(c.program)}\n    js:     ${JSON.stringify(rendered)}`);
  }

  const r = flatline.pikeRun(prog, c.text);
  const matched = r.saved !== null;
  if (matched !== c.matched) {
    failures.push(`${label}: matched=${matched}, python said ${c.matched}`);
    continue;
  }
  if (matched) {
    const spans = [];
    for (let i = 0; i <= prog.ngroups; i++) {
      const s = r.saved[2 * i];
      const e = r.saved[2 * i + 1];
      spans.push(s === null || e === null ? [-1, -1] : [s, e]);
    }
    if (!eq(spans, c.spans)) {
      failures.push(`${label}: spans ${JSON.stringify(spans)}, python said ${JSON.stringify(c.spans)}`);
    }
  }

  // Step counts are the headline claim of the whole project.  If the two
  // implementations disagree here, the numbers on the page are not the numbers
  // the test suite verified.
  if (r.steps !== c.pike_steps) {
    failures.push(`${label}: pike took ${r.steps} steps, python took ${c.pike_steps}`);
  }
  if (r.maxThreads !== c.pike_max_threads) {
    failures.push(`${label}: peak ${r.maxThreads} threads, python saw ${c.pike_max_threads}`);
  }

  const bt = flatline.backtrackRun(prog, c.text, { maxSteps: 2000000 });
  if (bt.gaveUp !== c.backtrack_gave_up) {
    failures.push(`${label}: backtracker gaveUp=${bt.gaveUp}, python said ${c.backtrack_gave_up}`);
  } else if (!bt.gaveUp && bt.steps !== c.backtrack_steps) {
    failures.push(`${label}: backtracker took ${bt.steps} steps, python took ${c.backtrack_steps}`);
  }
}

for (const r of CONFORMANCE.refusals) {
  checked++;
  let threw = null;
  try {
    flatline.compileProgram(r.pattern, "", false);
  } catch (e) {
    threw = e;
  }
  if (!threw) {
    failures.push(`${JSON.stringify(r.pattern)}: should have been refused`);
  } else if (!threw.detail || !threw.detail.includes(r.fragment)) {
    failures.push(`${JSON.stringify(r.pattern)}: message ${JSON.stringify(threw.message)} lacks ${JSON.stringify(r.fragment)}`);
  } else if (threw.pos !== r.pos) {
    failures.push(`${JSON.stringify(r.pattern)}: error at offset ${threw.pos}, python said ${r.pos}`);
  }
}

console.log(JSON.stringify({ checked, failures }, null, 1));
process.exit(failures.length ? 1 : 0);
