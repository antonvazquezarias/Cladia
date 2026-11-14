// node tests/test_dotplot.js: the dot plot grid (cladia/static/dotplot.js)
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "cladia", "static", "dotplot.js"), "utf8"), sandbox);
const { DotPlot } = sandbox;

// a reproducible random sequence
let seed = 7;
const rand = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
const randomSeq = (n) => Array.from({ length: n }, () => "ACGT"[Math.floor(rand() * 4)]).join("");
const revcomp = (s) => [...s].reverse().map((c) => ({ A: "T", C: "G", G: "C", T: "A" })[c]).join("");
const on = (g, grid, x, y) => grid[y * g.cols + x] === 1;

// A sequence against itself: the whole diagonal, forward; nothing reversed off it
{
  const s = randomSeq(5000);
  const g = DotPlot.grid({ a: s, b: s, word: 12, cols: 100, rows: 100 });
  for (let i = 0; i < 100; i++) assert(on(g, g.forward, i, i), `diagonal cell ${i}`);
  const off = g.forward.reduce((n, v, i) => n + (v && Math.floor(i / 100) !== i % 100 ? 1 : 0), 0);
  assert(off < 5, `few random hits off the diagonal (${off})`);
}

// An inverted copy: anti-diagonal in the reverse grid
{
  const s = randomSeq(4000);
  const g = DotPlot.grid({ a: s, b: revcomp(s), word: 12, cols: 80, rows: 80 });
  for (let i = 0; i < 80; i++) assert(on(g, g.reverse, i, 79 - i), `anti-diagonal cell ${i}`);
  assert(g.forward.reduce((n, v) => n + v, 0) < 5, "no forward diagonal");
}

// A rotated circle (another origin): two diagonal pieces
{
  const s = randomSeq(6000), r = s.slice(2000) + s.slice(0, 2000);
  const g = DotPlot.grid({ a: s, b: r, word: 12, cols: 60, rows: 60 });
  assert(on(g, g.forward, 40, 20) && on(g, g.forward, 10, 50), "both pieces");
}

// gaps and lower case are ignored, N breaks words, auto word size
{
  const s = randomSeq(900);
  const g = DotPlot.grid({ a: s.toLowerCase(), b: s.slice(0, 450) + "---" + s.slice(450), cols: 50, rows: 50 });
  assert.strictEqual(g.word, 8);
  assert(on(g, g.forward, 49, 49));
  assert.strictEqual(DotPlot.autoWord(150_000), 12);
  const n = DotPlot.grid({ a: "NNNNNNNNNNNN", b: "NNNNNNNNNNNN", word: 8, cols: 4, rows: 4 });
  assert.strictEqual(n.forward.reduce((t, v) => t + v, 0), 0);
}

// low complexity: a run doesn't explode (capped per word)
{
  const s = "A".repeat(20000);
  const g = DotPlot.grid({ a: s, b: s, word: 12, cols: 50, rows: 50 });
  assert(g.skipped > 0);
}

// word size 16 (all 32 bits of the code)
{
  const s = randomSeq(3000);
  const g = DotPlot.grid({ a: s, b: s, word: 16, cols: 30, rows: 30 });
  for (let i = 0; i < 30; i++) assert(on(g, g.forward, i, i));
  const r = DotPlot.grid({ a: s, b: revcomp(s), word: 16, cols: 30, rows: 30 });
  for (let i = 0; i < 30; i++) assert(on(r, r.reverse, i, 29 - i));
}

assert.throws(() => DotPlot.grid({ a: "", b: "ACGT", cols: 2, rows: 2 }));
assert.strictEqual(DotPlot.tickStep(150_000), 50_000);
console.log("dot plot tests passed");
