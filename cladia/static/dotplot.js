// ── Dot plots: where two DNA sequences share words ──────────────────────────
// A dot wherever a word of `word` bases of A is found in B: on the same strand
// (forward, drawn in one colour) or as its reverse complement (inversions, in
// another). The plot is a grid of cells (cols across for A, rows down for B),
// each cell marked when any shared word falls in it, so the work and memory
// follow the sequences' length, not the plot's area.
//
// Used by the side panel's Dot plot tab (app-dotplot.js) and the dot plot
// figure (figure-dotplot.js). No DOM here: tests/test_dotplot.js loads it in node.
//
//   const job = DotPlot.steps({ a, b, word, cols, rows });  // a generator
//   for (const done of job) …;  // yields the share done every so often; returns
//   job's result: { forward, reverse (Uint8Array cols*rows), cols, rows, word, skipped }
//   DotPlot.grid(...) does all of it at once.

(function (root) {
  const MAX_LENGTH = 2_000_000;  // per side
  const PER_WORD = 400;          // at most this many places of A per word of B (poly-A runs, repeats)
  const TABLE_BITS = 20;         // hash table of 2^20 chains
  const CHUNK = 100_000;         // positions of B between yields

  const CODE = new Uint8Array(256).fill(255);
  for (const [c, v] of [["A", 0], ["C", 1], ["G", 2], ["T", 3], ["U", 3]]) {
    CODE[c.charCodeAt(0)] = v;
    CODE[c.toLowerCase().charCodeAt(0)] = v;
  }

  // The word size for sequences this long: short words find more in short
  // sequences; long ones keep random matches out of genomes
  function autoWord(length) {
    return length < 1_000 ? 8 : length < 8_000 ? 10 : length < 200_000 ? 12 : 14;
  }

  const strip = (seq) => String(seq).replace(/[-.\s]/g, "");

  const hash = (code) => (Math.imul(code ^ (code >>> 15), 0x2c1b3c6d) >>> (32 - TABLE_BITS));

  function* steps({ a, b, word = 0, cols, rows }) {
    a = strip(a); b = strip(b);
    if (!a.length || !b.length) throw new Error("Both sequences need bases");
    if (a.length > MAX_LENGTH || b.length > MAX_LENGTH) throw new Error(`Dot plots take sequences up to ${MAX_LENGTH / 1e6} Mb`);
    const k = word || autoWord(Math.max(a.length, b.length));
    if (k < 4 || k > 16) throw new Error("Words of 4 to 16 bases");
    cols = Math.max(1, Math.min(cols, a.length)); rows = Math.max(1, Math.min(rows, b.length));
    const mask = k === 16 ? 0xffffffff : (1 << (2 * k)) - 1;
    const top = 2 * (k - 1);

    // A's words: a chain per hash slot (codes kept to tell collisions apart)
    const n = a.length - k + 1;
    const head = new Int32Array(1 << TABLE_BITS).fill(-1);
    const next = new Int32Array(Math.max(0, n));
    const codes = new Uint32Array(Math.max(0, n));
    let code = 0, valid = 0;
    for (let i = 0; i < a.length; i++) {
      const v = CODE[a.charCodeAt(i)];
      if (v === 255) { valid = 0; continue; }
      code = ((code << 2) | v) & mask;
      if (++valid < k) continue;
      const at = i - k + 1, slot = hash(code >>> 0);
      codes[at] = code >>> 0;
      next[at] = head[slot];
      head[slot] = at;
    }
    yield 0.1;

    const forward = new Uint8Array(cols * rows), reverse = new Uint8Array(cols * rows);
    const xScale = cols / a.length, yScale = rows / b.length;
    let skipped = 0;
    const mark = (grid, query, y) => {
      let seen = 0;
      for (let at = head[hash(query)]; at !== -1; at = next[at]) {
        if (codes[at] !== query) continue;
        if (++seen > PER_WORD) { skipped++; return; }
        grid[y + Math.floor(at * xScale)] = 1;
      }
    };
    let fw = 0, rc = 0;
    valid = 0;
    for (let j = 0; j < b.length; j++) {
      const v = CODE[b.charCodeAt(j)];
      if (v === 255) valid = 0;
      else {
        fw = ((fw << 2) | v) & mask;
        rc = (rc >>> 2) | ((3 - v) << top);  // the reverse complement of the same word
        if (++valid >= k) {
          const y = Math.floor((j - k + 1) * yScale) * cols;
          mark(forward, fw >>> 0, y);
          mark(reverse, rc >>> 0, y);
        }
      }
      if (j % CHUNK === CHUNK - 1) yield 0.1 + 0.9 * (j / b.length);
    }
    return { forward, reverse, cols, rows, word: k, skipped, lengths: [a.length, b.length] };
  }

  function grid(options) {
    const job = steps(options);
    for (;;) { const r = job.next(); if (r.done) return r.value; }
  }

  // A generator run in slices of the event loop; resolves to its result, or
  // null when stillWanted() turned false meanwhile
  async function run(options, stillWanted = () => true, onProgress = null) {
    const job = steps(options);
    for (;;) {
      const r = job.next();
      if (r.done) return r.value;
      onProgress?.(r.value);
      await new Promise((resolve) => setTimeout(resolve, 0));
      if (!stillWanted()) return null;
    }
  }

  // Round tick spacing (1, 2, 5 × 10^k) for about `count` ticks over `length`
  function tickStep(length, count = 5) {
    const raw = length / count, p = 10 ** Math.floor(Math.log10(raw));
    return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw) || p * 10;
  }
  const tickText = (value, step) => (step >= 1000 ? `${(value / 1000).toLocaleString()} kb` : value.toLocaleString());

  root.DotPlot = { steps, grid, run, autoWord, strip, tickStep, tickText, MAX_LENGTH };
})(typeof window !== "undefined" ? window : globalThis);
