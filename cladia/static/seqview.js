// Cladia sequence viewer: one canvas viewer, in the style of Geneious, for
//   "single"     one sequence: ruler, bases coloured, the complementary strand
//                when zoomed in, features in lanes, each CDS's translation under it;
//   "sequences"  several sequences, each a row with its features (not aligned);
//   "alignment"  an alignment: consensus and identity on top, each sequence in
//                grey with only the bases that differ from the consensus coloured.
// The same zoom, wheel, selection, find and copy everywhere. A status line and an
// overview of the whole length are at the bottom. Only what is on screen is
// drawn, so long organelle genomes and alignments stay fast.
//
//   const view = new SeqView(container, { onOpenRow, onSelection, translate, aaColor });
//   view.setRows([{ name, sequence }, ...], "alignment");  // sequences (with their gaps)
//   view.setFeatures({ rowName: { features, code, item } }); // features in each sequence's own positions
//   view.setShow({ features: true, translations: true, complement: true });
//
// Mouse: drag over the sequences selects a block, over the ruler whole columns,
// over the names whole rows (Shift extends); clicking a feature selects it;
// double-click a name opens that sequence. Ctrl+wheel zooms (keeping the
// selection/cursor centred through a quick transition), the wheel moves along, Shift+wheel scrolls the
// rows, the overview jumps.

(function () {
  // ── Look ───────────────────────────────────────────────────────────────
  const BASE_COLORS = { A: "#ef6f6f", C: "#8080f0", G: "#f2c94c", T: "#62cc62", U: "#62cc62" };
  const OTHER_COLOR = "#d6a6e6";  // other letters: IUPAC codes, amino acids
  const IDENTITY_COLORS = { full: "#3fae49", part: "#a3a33a", low: "#d9534f" };  // 100 %, 30-99 %, <30 %
  const SELECTION = "rgba(47, 111, 94, 0.18)", SELECTION_EDGE = "#2f6f5e";
  const MATCH = "#e67e22";
  const STOP = "#b0171f";  // an internal stop codon's mark
  const NAME_WIDTH = 150;   // names column (px), until dragged wider or narrower
  const LABEL_FIELDS = { name: "Name", organism: "Organism", description: "Description" };
  const RULER = 22, ROW = 17, IDENTITY = 14, GAP = 6, STATUS = 20, OVERVIEW = 30;
  const VARIANT = 9;        // optional alignment variant lane, above identity
  const TRANS = 13;         // a CDS's translation track (under its arrow)
  const COVERAGE = 46;      // a mapping's coverage graph
  const AGREE = 16;         // a mapping's agreement row (the share of reads with the commonest base)
  const CLIPS = 16;         // a mapping's clipped-reads row
  const TRACE = 84;         // a Sanger read's trace (the four dye curves)
  const QUAL = 18;          // and its quality bars
  const TRACE_FROM = 2;     // character width (px) from which the curves are drawn
  const ROW_TRACE = 56;     // a read's trace under its row (assembled reads), zoomed in
  const TRACE_COLORS = { A: "#d94f4f", C: "#4f5fd9", G: "#c49a12", T: "#3a9e3a" };  // the bases' colours, darker
  const BLOCKS = 26;        // a genome comparison's blocks lane, under each genome's features
  const LINKS = 40;         // and the room under it for the bands to the next genome
  const SCROLLBAR = 10;     // the rows' vertical scrollbar (px)
  const TRACKS_FROM = 3;    // character width (px) from which translations are drawn
  const LETTERS_FROM = 8;   // character width (px) from which letters are drawn
  const MAX_CHAR = 18;      // most zoomed in
  const EDGE = 12;          // px of margin before the first base and after the last (a cursor there stays visible)
  const FONT = "12px Consolas, 'Courier New', monospace";
  const SMALL_FONT = "10px 'Segoe UI', system-ui, sans-serif";
  const LABEL_FONT = "11px 'Segoe UI', system-ui, sans-serif";
  const NAME_FONT = "12px 'Segoe UI', system-ui, sans-serif";
  const NAMES_MARGIN = 14;  // px before the names' edge where hovering doesn't open them
  const NAMES_DELAY = 500;  // ms the mouse rests on the names before they open
  const COMPLEMENT = { A: "T", C: "G", G: "C", T: "A", U: "A", R: "Y", Y: "R", K: "M", M: "K", S: "S", W: "W",
                       B: "V", V: "B", D: "H", H: "D", N: "N", "-": "-" };

  // A trace's curves scaled to its tall peaks (the 95th percentile of the called
  // bases' peaks in the part kept; a few higher ones go off the top)
  function traceScale(t) {
    const heights = [];
    for (let b = t.start; b < t.end; b++) {
      const ch = t.channels[t.bases[b]];
      if (ch) heights.push(ch[t.peaks[b]] || 0);
    }
    heights.sort((a, b) => a - b);
    return Math.max(1, (heights.length ? heights[Math.floor(heights.length * 0.95)] : 1) * 1.1);
  }

  // Amino acids by kind, as in Clustal X: hydrophobic blue, positive red, negative
  // magenta, polar green, cysteine pink, glycine orange, proline yellow, aromatic cyan
  const AMINO_COLORS = {};
  for (const [letters, color] of [["AILMFWV", "#80a0f0"], ["KR", "#f01505"], ["DE", "#c048c0"], ["NQST", "#15c015"],
                                  ["C", "#f08080"], ["G", "#f09048"], ["P", "#c0c000"], ["HY", "#15a4a4"]]) {
    for (const ch of letters) AMINO_COLORS[ch] = color;
  }
  // Mostly letters that aren't nucleotide codes = amino acids (as genbank.looks_protein)
  function looksProtein(text) {
    const letters = text.toUpperCase().replace(/[-.*?X\s]/g, "");
    if (!letters) return false;
    return letters.replace(/[^ACGTUN]/g, "").length < 0.9 * letters.length;
  }

  // A CIGAR string ("20S130M2D") -> [[20, "S"], [130, "M"], [2, "D"]]
  function parseCigar(cigar) {
    const ops = [];
    for (const m of String(cigar).matchAll(/(\d+)([MIDNSHP=X])/g)) ops.push([Number(m[1]), m[2]]);
    return ops;
  }

  // The first index in a sorted array whose value is >= x
  function lowerBound(sorted, x) {
    let lo = 0, hi = sorted.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (sorted[mid] < x) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  // ── Feature looks (shared with the circular map) ───────────────────────
  // A colour mixed with black (k > 0) or white (k < 0)
  const shades = new Map();
  function shade(hex, k) {
    const key = hex + k;
    if (!shades.has(key)) {
      const n = parseInt(hex.slice(1), 16), to = k > 0 ? 0 : 255, a = Math.abs(k);
      const mix = (c) => Math.round(c + (to - c) * a);
      shades.set(key, `rgb(${mix(n >> 16)}, ${mix((n >> 8) & 255)}, ${mix(n & 255)})`);
    }
    return shades.get(key);
  }
  // Text on a fill: white on dark colours, near-black on light ones
  function inkFor(hex) {
    const n = parseInt(hex.slice(1), 16);
    const lum = 0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
    return lum < 140 ? "#fff" : "#1a1a1a";
  }
  // The text, shortened with "…" to fit `room` px; "" when not even 2 letters fit
  // Which rows are in view (0-based first and last), the same words everywhere
function rowsText(first, last, total) {
  return `Rows ${first + 1}–${last + 1} of ${total}`;
}

function fitText(ctx, text, room) {
    if (ctx.measureText(text).width <= room) return text;
    let n = Math.floor((text.length * room) / ctx.measureText(text).width);
    while (n >= 2 && ctx.measureText(text.slice(0, n) + "…").width > room) n--;
    return n >= 2 ? text.slice(0, n) + "…" : "";
  }
  // Fill, edge and text of a feature; hovered ones lighter, selected ones a thicker edge
  function featureStyle(f, hovered, selected) {
    const color = f.color || "#b9b9b9";
    return { fill: hovered ? shade(color, -0.3) : color, edge: shade(color, selected ? 0.55 : 0.32),
             edgeWidth: selected ? 2 : 1, ink: inkFor(color) };
  }
  // Pairs of parts joined by an intron line: [earlier, later] by position, in the
  // feature's own order, but not the two halves of a feature across the origin
  function introns(f, length) {
    const pairs = [];
    for (let i = 1; i < f.parts.length; i++) {
      const [a, b] = [f.parts[i - 1], f.parts[i]].sort((p, q) => p.start - q.start);
      if (!(a.start === 0 && b.end === length - 1)) pairs.push([a, b]);
    }
    return pairs;
  }
  // The stretches a feature takes in its lane: from its first to its last part
  // (introns too), or, across the origin of a circular sequence (parts at both
  // ends), the two pieces either side of the origin: the gap between its parts
  // at the origin is the longest one.
  function spansOf(f, length) {
    const parts = [...f.parts].sort((a, b) => a.start - b.start);
    const first = parts[0], last = parts[parts.length - 1];
    if (!length || parts.length < 2 || first.start !== 0 || last.end !== length - 1) return [[f.c0, f.c1]];
    let cut = 0, widest = -1;  // the origin's side of the feature: after the longest gap
    for (let i = 0; i + 1 < parts.length; i++) {
      const gap = parts[i + 1].start - parts[i].end;
      if (gap > widest) { widest = gap; cut = i; }
    }
    return [[parts[cut + 1].c0, last.c1], [first.c0, parts[cut].c1]];
  }
  // The part of a feature that carries the arrowhead (its 3' end), or null
  function arrowPart(f) {
    if (f.strand !== 1 && f.strand !== -1) return null;
    return f.parts.reduce((a, b) => (f.strand === 1 ? (b.end > a.end ? b : a) : (b.start < a.start ? b : a)));
  }

  // The keys a wheel event counts with. Smooth-scrolling mice keep sending wheel
  // events after the wheel was let go; a key released meanwhile must not turn the
  // rest of that scroll into another action (a zoom into a pan). So within one
  // continuous scroll (gaps under WHEEL_STREAK ms) released keys still count;
  // keys pressed take effect at once.
  const WHEEL_STREAK = 150;
  let lastWheel = { at: 0, ctrl: false, shift: false };
  function wheelKeys(e) {
    const keys = { ctrl: e.ctrlKey, shift: e.shiftKey };
    if (e.timeStamp - lastWheel.at < WHEEL_STREAK && !keys.ctrl && !keys.shift) {
      keys.ctrl = lastWheel.ctrl;
      keys.shift = lastWheel.shift;
    }
    lastWheel = { at: e.timeStamp, ...keys };
    return keys;
  }

  class SeqView {
    constructor(container, options = {}) {
      this.container = container;
      this.options = { wheelPan: 0.35, ...options };
      this.canvas = document.createElement("canvas");
      this.canvas.style.display = "block";
      this.canvas.tabIndex = 0;  // for Esc and Ctrl+C (app.js)
      this.namesCanvas = document.createElement("canvas");
      this.namesCanvas.setAttribute("aria-hidden", "true");
      this.namesCanvas.style.position = "absolute";
      this.namesCanvas.style.left = "0";
      this.namesCanvas.style.top = "0";
      this.namesCanvas.style.zIndex = "1";
      this.namesCanvas.style.pointerEvents = "none";
      container.append(this.canvas, this.namesCanvas);
      this.ctx = this.canvas.getContext("2d");
      this.namesCtx = this.namesCanvas.getContext("2d");
      this.mode = "alignment";
      this.rows = [];
      this.length = 0;
      this.charWidth = 1;
      this.scrollX = 0;  // px of the sequence area scrolled off to the left
      this.scrollY = 0;  // px of the rows scrolled off the top
      this.selection = null;  // { c0, c1, r0, r1 }: columns and rows, inclusive
      this.matches = [];      // find results: { row, c0, c1 }
      this.variantColumns = [];
      this.variantAt = new Map();
      this.variantBaseColumns = [];
      this.matchIndex = -1;
      this.show = { features: true, translations: true, complement: true };
      this.hover = "";
      this.nameWidth = options.nameWidth || NAME_WIDTH;
      this.labelFields = options.labelFields || ["name"];

      this.canvas.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
      this.canvas.addEventListener("mousedown", (e) => this.onMouseDown(e));
      this.canvas.addEventListener("dblclick", (e) => this.onDoubleClick(e));
      this.canvas.addEventListener("mousemove", (e) => this.onHover(e));
      this.canvas.addEventListener("mouseleave", () => { this.hover = ""; this.hoverTrack = false; this.hoverFeature = null; this.namesOpen = false;
        clearTimeout(this.namesTimer); this.namesTimer = null; this.draw(); });
      this.resize();  // measure now: data may arrive before the first resize event
      new ResizeObserver(() => this.resize()).observe(container);
    }

    get aligned() { return this.mode === "alignment"; }
    get single() { return this.mode === "single"; }

    // ── Data ───────────────────────────────────────────────────────────────
    // keepView: the same sequence after an edit (zoom, scroll and rotation stay)
    setRows(rows, mode = "alignment", keepView = false) {
      this.cancelNavigation();
      this.hover = ""; this.hoverTrack = false;
      const kept = keepView && { charWidth: this.charWidth, scrollX: this.scrollX, scrollY: this.scrollY,
                                 circZoom: this.circZoom, rotation: this.rotation };
      this.mode = mode;
      this.variantColumns = [];
      this.variantAt.clear();
      this.variantBaseColumns = [];
      this.circularSequence = false;  // the topology: setTopology, after the rows
      // amino acids: their own colours, no complementary strand, translations or circular map
      this.protein = looksProtein(rows.slice(0, 20).map((r) => r.sequence.slice(0, 5000)).join(""));
      this.mapping = null;  // a mapping's coverage and consensus: setMapping, after the rows
      this.reads = null;    // the mapping's reads in view: setReads; packed under the consensus
      this.readsEnabled = false;
      this.readRows = [];   // [{ end, reads }]: reads side by side when they don't overlap
      this.packedReadsSource = null;
      this.readLane = 0;    // the packed reads' height (px)
      this.readCaret = null; // { read, col }: a click in a read (a cursor there)
      this.readSel = null;   // { read, c0, c1 }: the part of one read selected (dragged)
      this.readOffset = 0;   // the first packed row shown (the lane scrolls)
      this.onViewChange = null;  // (first, last): the app fetches the reads of a new window
      this.viewSent = null;      // the window onViewChange last told about
      this.trace = null;    // a Sanger read's trace and qualities: setTrace, after the rows
      this.compare = null;  // a genome comparison's blocks: setComparison, after the rows
      this.rows = rows.map((r) => {
        const seq = r.sequence.toUpperCase();
        // Gaps before the first and after the last base are missing data, as in
        // Geneious: left blank and not counted against identity
        const first = seq.search(/[^-]/);
        let last = seq.length - 1;
        while (last >= 0 && seq[last] === "-") last--;  // (not [...seq].reverse(): this runs for every row on every redraw)
        return { name: r.name, description: r.description || "", organism: r.organism || "",
                 seq, first: first < 0 ? seq.length : first, last: first < 0 ? -1 : last,
                 features: [], lanes: 0, cdsLanes: new Set(), code: 1, item: null };
      });
      this.length = Math.max(0, ...this.rows.map((r) => r.seq.length));
      if (this.aligned) this.computeConsensus();
      for (const row of this.rows) {
        row.mismatches = [];  // alignments: columns where this row has a base that isn't the consensus
        row.segments = [];    // [start, end) runs without gaps, for the zoomed-out view
        row.basesUpTo = new Int32Array(this.length);  // bases up to and including each column
        let runStart = -1, bases = 0;
        for (let col = 0; col < this.length; col++) {
          const ch = row.seq[col] || "-";
          if (ch !== "-") bases++;
          row.basesUpTo[col] = bases;
          if (this.aligned && ch !== "-" && ch !== this.consensus[col]) row.mismatches.push(col);
          if (ch !== "-" && runStart < 0) runStart = col;
          if (ch === "-" && runStart >= 0) { row.segments.push([runStart, col]); runStart = -1; }
        }
        if (runStart >= 0) row.segments.push([runStart, this.length]);
        row.bases = bases;
      }
      this.selection = null;
      this.caret = null;
      this.matches = [];
      this.overview = null;
      this.scrollX = -EDGE;  // the first base after the margin
      this.scrollY = 0;
      this.circZoom = 1;  // the circular map (seqview-circular.js)
      this.rotation = 0;
      if (!this.single) this.circularMap = false;
      this.charWidth = this.minCharWidth();
      if (kept) {
        Object.assign(this, kept);
        this.charWidth = Math.max(this.minCharWidth(), kept.charWidth);
      }
      this.layout();
      this.draw();
      this.selectionChanged();
    }

    setVariantSites(sites, referenceName) {
      this.variantColumns = [];
      this.variantAt.clear();
      this.variantBaseColumns = [];
      const reference = this.aligned && this.rows.find((row) => row.name === referenceName);
      if (reference) {
        const columns = [];
        for (let col = 0; col < reference.seq.length; col++)
          if (reference.seq[col] !== "-" && reference.seq[col] !== ".") columns.push(col);
        this.variantBaseColumns = columns;
        for (const site of sites) {
          const col = columns[site.position - 1];
          if (col === undefined) continue;
          this.variantAt.set(col, site);
        }
        this.variantColumns = [...this.variantAt.keys()].sort((a, b) => a - b);
      }
      this.layout();
      this.draw();
    }

    // Per column, over the sequences present there (terminal gaps left out):
    // consensus = the most common character (a base wins a tie with a gap);
    // identity = Geneious's: the share of identical pairs, a base against a gap
    // counting as different and a gap against a gap not counted.
    // One pass with counts by character code: no object per column, and numeric
    // indexing instead of string keys (this runs for every redraw).
    computeConsensus() {
      const length = this.length;
      const rows = this.rows;
      const consensus = new Array(length);
      this.identity = new Float32Array(length);
      const counts = new Uint32Array(65536);  // character code -> times seen in this column
      const seen = [];  // those codes, to clear only them after each column
      for (let col = 0; col < length; col++) {
        let best = 45, bestCount = 0, present = 0, same = 0, gapPairs = 0;  // 45 = "-"
        for (const row of rows) {
          if (col < row.first || col > row.last) continue;
          const code = col < row.seq.length ? row.seq.charCodeAt(col) : 45;
          present++;
          const c = (counts[code] = counts[code] + 1);
          if (c === 1) seen.push(code);
          // New pairs made by this occurrence: c - 1 with the other copies of it.
          if (code === 45) gapPairs += c - 1; else same += c - 1;
          if (c > bestCount || (c === bestCount && best === 45)) { best = code; bestCount = c; }
        }
        consensus[col] = present ? String.fromCharCode(best) : " ";
        const all = (present * (present - 1)) / 2 - gapPairs;
        this.identity[col] = all > 0 ? same / all : 1;
        for (const code of seen) counts[code] = 0;
        seen.length = 0;
      }
      this.consensus = consensus.join("");
    }

    // Column of each base of a row (made when first needed: features, find)
    columnsOf(row) {
      if (!row.columnOfBase) {
        row.columnOfBase = new Int32Array(row.bases);
        let i = 0;
        for (let col = 0; col < this.length; col++) if ((row.seq[col] || "-") !== "-") row.columnOfBase[i++] = col;
      }
      return row.columnOfBase;
    }

    // Features and translations. byName: row name -> { features, code, item },
    // features in the sequence's own positions (0-based, inclusive):
    // { name, type, color, strand, locations: [{ start, end }], codonStart }
    setFeatures(byName) {
      for (const row of this.rows) {
        const info = byName[row.name];
        row.features = [];
        row.codons = null;
        row.code = info ? info.code : 1;
        row.item = info ? info.item : null;  // the library item this row came from
        row.trace = info?.trace ? { ...info.trace, scale: traceScale(info.trace) } : null;  // a Sanger read
        if (!info) continue;
        const columns = this.columnsOf(row);
        for (const f of info.features) {
          const parts = f.locations.filter((l) => l.start < row.bases && l.end < row.bases)
            .map((l) => ({ start: l.start, end: l.end, c0: columns[l.start], c1: columns[l.end] }));
          if (parts.length) row.features.push({ ...f, parts, c0: Math.min(...parts.map((p) => p.c0)), c1: Math.max(...parts.map((p) => p.c1)) });
        }
        // Lanes: a feature goes in the first lane where it doesn't overlap another,
        // and below its gene, so genes are always above their CDSs, tRNAs...: its
        // gene = the closest matching gene on its strand (a named CDS may start
        // or end past its gene). Placed in order of start, each feature
        // right after its gene. What a feature takes is its stretches (spansOf):
        // one, or two for one across the origin (not the whole sequence, which
        // its first and last parts would make it look like). A lane takes a
        // feature only if it starts after the lane's last end (so any order is
        // safe) and ends before the piece at the sequence's end of a feature
        // across the origin there (those have the smallest anchor: placed first).
        const length = this.single ? row.seq.length : 0;
        for (const f of row.features) f.spans = spansOf(f, length);
        const overlaps = (a, b) => a.spans.some(([a0, a1]) => b.spans.some(([b0, b1]) => a0 <= b1 && b0 <= a1));
        const within = (f, g) => f.spans.every(([f0, f1]) => g.spans.some(([g0, g1]) => g0 <= f0 && f1 <= g1));
        const isGene = (f) => f.type.toLowerCase() === "gene";
        const genes = row.features.filter(isGene);
        const spanLength = f => f.spans.reduce((n, [a, b]) => n + b - a + 1, 0);
        for (const f of row.features) {
          f.gene = null;
          if (isGene(f)) continue;
          // Nested genes may have different display names (e.g. "gene" versus
          // "hypothetical protein"). Never attach them to an opposite-strand
          // enclosing gene simply because it appeared first in the file.
          const sameName = g => g.name && g.name === f.name;
          const exact = g => within(f, g) && within(g, f);
          const near = genes.filter(g => (!g.strand || !f.strand || g.strand === f.strand)
            && overlaps(f, g) && (sameName(g) || within(f, g)));
          near.sort((a, b) => Number(exact(b)) - Number(exact(a))
            || Number(b.strand === f.strand) - Number(a.strand === f.strand)
            || Number(!!sameName(b)) - Number(!!sameName(a)) || spanLength(a) - spanLength(b));
          f.gene = near[0] || null;
        }
        const anchor = (f) => (f.gene ? f.gene.c0 : f.c0);
        const lanes = [];  // each lane: { end: its last end, limit: where a piece at the sequence's end starts }
        for (const f of row.features.sort((a, b) => anchor(a) - anchor(b) || !!a.gene - !!b.gene || a.c0 - b.c0 || b.c1 - a.c1)) {
          const below = f.gene ? f.gene.lane + 1 : 0;
          const free = (l) => f.spans.every(([a, b]) => a > l.end && b < l.limit);
          let lane = lanes.findIndex((l, i) => i >= below && free(l));
          if (lane < 0) {
            lane = Math.max(lanes.length, below);
            while (lanes.length <= lane) lanes.push({ end: -1, limit: Infinity });
          }
          const l = lanes[lane];
          if (f.spans.length === 2) {  // across the origin: [the piece at the end, the piece from 0]
            l.limit = Math.min(l.limit, f.spans[0][0]);
            l.end = Math.max(l.end, f.spans[1][1]);
          } else l.end = Math.max(l.end, f.spans[0][1]);
          f.lane = lane;
        }
        row.lanes = lanes.length;
        row.cdsLanes = new Set(row.features.filter((f) => f.type === "CDS").map((f) => f.lane));  // lanes with a translation track
      }
      this.layout();
      this.draw();
    }

    setShow(show) {
      Object.assign(this.show, show);
      this.layout();
      this.draw();
    }

    // A row's genetic code (the Code menu): its translations are redone
    setCode(code, rowIndex = 0) {
      const row = this.rows[rowIndex];
      if (!row) return;
      row.code = code;
      row.codons = null;
      this.draw();
    }

    // A CDS's codons as columns, for its translation track (made when first drawn)
    codonsOf(row, f) {
      if (!row.codons) row.codons = new Map();
      if (row.codons.has(f)) return row.codons.get(f);
      const codons = [];
      row.codons.set(f, codons);
      const translate = this.options.translate;
      if (translate) {
        // The CDS's bases in reading order
        const parts = [...f.parts].sort((a, b) => (f.strand === -1 ? b.start - a.start : a.start - b.start));
        const positions = [];
        for (const p of parts) {
          if (f.strand === -1) for (let i = p.end; i >= p.start; i--) positions.push(i);
          else for (let i = p.start; i <= p.end; i++) positions.push(i);
        }
        const columns = this.columnsOf(row);
        const seqBase = (i) => row.seq[columns[i]];
        for (let k = (f.codonStart || 1) - 1; k + 2 < positions.length; k += 3) {
          const three = positions.slice(k, k + 3);
          let codon = three.map(seqBase).join("");
          if (f.strand === -1) codon = [...codon].map((b) => COMPLEMENT[b] || "N").join("");
          const cols = three.map((i) => columns[i]);
          const c = { c0: Math.min(...cols), c1: Math.max(...cols), aa: translate(codon, row.code) };
          // a codon split by an intron: its bases on either side (not one box across the intron)
          if (Math.abs(three[2] - three[0]) !== 2) {
            c.pieces = [];
            for (const [k, i] of three.entries()) {
              const prev = c.pieces[c.pieces.length - 1];
              if (prev && Math.abs(i - three[k - 1]) === 1) prev[1] = Math.max(prev[1], columns[i]), prev[0] = Math.min(prev[0], columns[i]);
              else c.pieces.push([columns[i], columns[i]]);
            }
          }
          codons.push(c);
        }
      }
      return codons;
    }

    // ── Internal stop codons ───────────────────────────────────────────────
    // A CDS's first stop codon before its last codon (its protein ends early), in
    // the genetic code shown, or null. As in Geneious, the CDS is drawn lighter from
    // there to its 3' end, with a mark at the stop.
    internalStop(row, f) {
      if (f.type !== "CDS" || this.protein || !this.options.translate) return null;
      const codons = this.codonsOf(row, f);
      if (codons.stop === undefined) codons.stop = codons.findIndex((c, i) => c.aa === "*" && i < codons.length - 1);
      return codons.stop >= 0 ? codons[codons.stop] : null;
    }

    // { c0, c1, stop }: the columns from the stop to the 3' end, or null
    afterStop(row, f) {
      const stop = this.internalStop(row, f);
      if (!stop) return null;
      return f.strand === -1 ? { c0: f.c0, c1: stop.c1, stop } : { c0: stop.c0, c1: f.c1, stop };
    }

    stopTip(row, f) {
      const stop = this.internalStop(row, f);
      return stop ? ` · stop codon at ${row.basesUpTo[stop.c0]}–${row.basesUpTo[stop.c1]}` : "";
    }

    // ── Geometry ───────────────────────────────────────────────────────────
    get seqWidth() { return Math.max(1, this.width - this.nameWidth); }
    get variantHeight() { return this.aligned && this.show.variants && this.variantColumns.length ? VARIANT : 0; }
    get headerHeight() {
      if (this.aligned) return RULER + ROW + this.variantHeight + IDENTITY + GAP;
      if (this.single && this.trace) return RULER + TRACE + QUAL + GAP;
      if (this.single && this.mapping) return this.mappingTop("consensus") + (this.mapping.coverageOnly ? 0 : ROW) + GAP;
      return RULER + GAP + (this.gcHeight?.() || 0);
    }
    get rowsTop() { return this.headerHeight; }
    // the reads come under the sequence (the user's wish), filling the rest
    get readsTop() {
      if (!(this.single && this.mapping)) return this.rowsTop;
      const full = this.rows[0]?.height ?? ROW, shown = this.rowsHeight;
      // the row's bottom padding left out (the lane adds its own 4 px), unless the features scroll
      return this.rowsTop + (shown < full ? shown : full - 5);
    }
    // ── Vertical scrollbar (rows taller than the view) ───────────────────
    canScrollRows() { return !this.circularMap && (this.contentHeight || 0) > this.rowsHeight + 1; }

    scrollbar() {  // the thumb: { x, top, height } in canvas pixels
      const track = this.rowsHeight, total = this.contentHeight;
      const height = Math.max(24, (track * track) / total);
      const top = this.rowsTop + ((track - height) * this.scrollY) / Math.max(1, total - track);
      return { x: this.width - SCROLLBAR - 2, top, height };
    }

    onScrollbar(x, y) {
      return this.canScrollRows() && x >= this.width - SCROLLBAR - 4 && y >= this.rowsTop && y < this.rowsTop + this.rowsHeight;
    }

    dragScrollbar(e) {
      const bar = this.scrollbar(), start = this.point(e).y;
      const onThumb = start >= bar.top && start < bar.top + bar.height;
      const total = this.contentHeight, track = this.rowsHeight;
      const perPixel = (total - track) / Math.max(1, track - bar.height);
      if (!onThumb) {  // on the track: jump there
        this.scrollY = (start - this.rowsTop - bar.height / 2) * perPixel;
        this.clampScroll();
        this.draw();
      }
      const from = this.scrollY, y0 = this.point(e).y;
      this.drag((ev) => {
        this.scrollY = from + (this.point(ev).y - y0) * perPixel;
        this.clampScroll();
        this.draw();
      });
    }

    drawScrollbar() {
      if (!this.canScrollRows()) return;
      const bar = this.scrollbar();
      this.paintScrollbar(this.rowsTop, this.rowsHeight, bar.top, bar.height);
    }

    // One look for every vertical scrollbar (the rows', a mapping's read lane): a
    // light track at the right edge, a dark rounded thumb
    paintScrollbar(trackTop, track, thumbTop, thumb) {
      const ctx = this.ctx, x = this.width - SCROLLBAR - 2;
      ctx.fillStyle = "rgba(0, 0, 0, 0.06)";
      ctx.fillRect(x, trackTop, SCROLLBAR, track);
      ctx.fillStyle = "rgba(0, 0, 0, 0.32)";
      ctx.beginPath();
      ctx.roundRect(x + 1, thumbTop + 1, SCROLLBAR - 2, Math.max(2, thumb - 2), 3);
      ctx.fill();
    }

    get rowsHeight() {
      if (this.single && this.mapping) {
        // the sequence row only; a deep stack of features takes at most half the room under
        // the tracks while reads show (they scroll there, as rows do), so the reads keep the rest
        const full = Math.max(0, this.rows[0]?.height || ROW);
        const room = Math.max(0, this.height - this.rowsTop - STATUS - OVERVIEW - 2);
        return Math.min(full, this.readsEnabled ? Math.max(ROW + 2 * this.laneHeight, Math.floor(room / 2)) : room);
      }
      return Math.max(0, this.height - this.rowsTop - STATUS - OVERVIEW - 2);
    }
    get laneHeight() { return this.aligned ? 14 : 19; }
    get letters() { return this.charWidth >= LETTERS_FROM; }
    minCharWidth() { return Math.min(MAX_CHAR, Math.max(1, this.seqWidth - 2 * EDGE) / Math.max(1, this.length)); }

    // Translation tracks are shown (zoomed in enough, Translations ticked)
    tracksOn() { return this.show.translations && !this.protein && this.charWidth >= TRACKS_FROM; }
    layoutKey() {
      // the reads lane's row height changes with zoom (bases or bars), so it lays out again
      return `${this.gcHeight?.() || 0}|${this.letters}|${this.tracksOn()}|${this.charWidth >= TRACE_FROM}|${this.height}|${this.readsEnabled}|` +
        `${this.reads?.reads?.length ? this.readRowHeight().toFixed(2) : 0}`;
    }

    // Each row's height and top: the sequence (and, for a single sequence zoomed
    // in, its complementary strand), then feature lanes; a lane with a CDS is
    // taller when translations are shown (the track under the arrow)
    layout() {
      let top = 0;
      const strands = this.single && !this.mapping && this.letters && this.show.complement && !this.protein ? 2 : 1;
      const track = this.tracksOn() ? TRANS : 0;
      for (const row of this.rows) {
        row.top = top;
        row.lanesTop = ROW * strands;
        row.traceTop = null;
        if (row.trace && !this.single && this.charWidth >= TRACE_FROM) {  // its trace, between its bases and features
          row.traceTop = row.lanesTop;
          row.lanesTop += ROW_TRACE;
        }
        row.laneTops = [];
        let lanes = 0;
        if (this.show.features) {
          for (let lane = 0; lane < row.lanes; lane++) {
            row.laneTops.push(lanes);
            lanes += this.laneHeight + (row.cdsLanes.has(lane) ? track : 0);
          }
        }
        row.height = row.lanesTop + (lanes ? lanes + 3 : 0) + (this.single ? 4 : 0);
        if (this.compare) {  // the blocks lane, then room for the bands to the next genome
          row.blocksTop = row.height;
          row.height += BLOCKS + (row === this.rows[this.rows.length - 1] ? 4 : LINKS);
        }
        top += row.height;
      }
      this.contentHeight = top;
      if (this.single && this.mapping) {  // the reads lane takes the height under the sequence
        this.readLane = this.readsEnabled ? Math.max(0, this.height - this.readsTop - OVERVIEW - STATUS - 2) : 0;
        if (this.reads?.reads?.length) this.packReads();
        else { this.readRows = []; this.readShown = 0; }
      } else {
        this.readLane = 0;
      }
      this.laidOutKey = this.layoutKey();
    }

    clampScroll() {
      const maxX = Math.max(-EDGE, this.length * this.charWidth - this.seqWidth + EDGE);
      const maxY = Math.max(0, (this.contentHeight || 0) - this.rowsHeight);
      this.scrollX = Math.min(maxX, Math.max(-EDGE, this.scrollX));
      this.scrollY = Math.min(maxY, Math.max(0, this.scrollY));
    }

    resize() {
      this.cancelNavigation();
      const middle = this.middleColumn();
      const dpr = window.devicePixelRatio || 1;
      this.width = this.container.clientWidth;
      this.height = this.container.clientHeight;
      this.canvas.width = Math.round(this.width * dpr);
      this.canvas.height = Math.round(this.height * dpr);
      this.canvas.style.width = `${this.width}px`;
      this.canvas.style.height = `${this.height}px`;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.namesCanvas.width = Math.round(this.width * dpr);
      this.namesCanvas.height = Math.round(this.height * dpr);
      this.namesCanvas.style.width = `${this.width}px`;
      this.namesCanvas.style.height = `${this.height}px`;
      this.namesCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.dpr = dpr;
      this.overview = null;
      this.charWidth = Math.max(this.charWidth, this.minCharWidth());
      if (middle !== null && Number.isFinite(middle)) this.centerOn(middle, false);
      this.draw();
    }

    middleColumn() {
      return this.length ? (this.scrollX + this.seqWidth / 2) / this.charWidth : null;
    }

    cancelNavigation() {
      if (this.navigation) window.cancelAnimationFrame?.(this.navigation.frame);
      this.navigation = null;
    }

    // One camera transition for all jump commands. New input starts from the
    // currently displayed frame, while repeated zoom gestures accumulate targets.
    navigate(target, animate = true, zoomAnchor = null) {
      this.cancelNavigation();
      target = { ...target };
      if ('scrollX' in target) target.scrollX = Math.max(-EDGE, Math.min(target.scrollX,
        Math.max(-EDGE, this.length * (target.charWidth ?? this.charWidth) - this.seqWidth + EDGE)));
      if ('rotation' in target) target.rotation = ((target.rotation % this.length) + this.length) % this.length;
      const from = Object.fromEntries(Object.keys(target).map(key => [key, this[key]]));
      const apply = state => {
        Object.assign(this, state);
        if (this.layoutKey() !== this.laidOutKey) this.layout();
        if (target.charWidth && (this.readSel || this.readCaret)) this.centerReadRow();
        this.clampScroll();
        this.draw();
      };
      if (!animate || !window.requestAnimationFrame || Object.keys(target).every(key => target[key] === from[key])
        || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
        apply(target); return;
      }
      const motion = { target, frame: null, start: performance.now() };
      this.navigation = motion;
      const tick = now => {
        if (this.navigation !== motion) return;
        const t = Math.min(1, Math.max(0, (now - motion.start) / 170)), eased = 1 - (1 - t) ** 3;
        const state = {};
        for (const key of Object.keys(target)) {
          let delta = target[key] - from[key];
          if (key === 'rotation') delta = ((delta % this.length + this.length * 1.5) % this.length) - this.length / 2;
          state[key] = key === 'charWidth' || key === 'circZoom'
            ? from[key] * (target[key] / from[key]) ** eased : from[key] + delta * eased;
        }
        if (zoomAnchor !== null && 'charWidth' in target && 'scrollX' in target) {
          // Scale and scroll must describe the same camera. Interpolating scale
          // geometrically but scroll linearly makes even a centred base drift
          // away and return. Interpolate its screen position, then derive scroll.
          const startX = zoomAnchor * from.charWidth - from.scrollX;
          const endX = zoomAnchor * target.charWidth - target.scrollX;
          state.scrollX = zoomAnchor * state.charWidth - (startX + (endX - startX) * eased);
        }
        if (t === 1) { this.navigation = null; apply(target); }
        else { apply(state); motion.frame = window.requestAnimationFrame(tick); }
      };
      motion.frame = window.requestAnimationFrame(tick);
    }

    centerOn(col, redraw = true) {
      const scrollX = col * this.charWidth - this.seqWidth / 2;
      if (redraw) this.navigate({ scrollX });
      else { this.cancelNavigation(); this.scrollX = scrollX; this.clampScroll(); }
    }

    // Exact selected/clicked destination, reached with a brief camera transition.
    zoom(factor, x = null) {
      const pending = this.navigation?.target, previous = pending?.charWidth ?? this.charWidth;
      const previousX = pending?.scrollX ?? this.scrollX;
      const width = Math.min(MAX_CHAR, Math.max(this.minCharWidth(), previous * factor));
      if (width === previous) return;
      const s = this.selection && (this.selection.wraps || (this.selection.c1 - this.selection.c0 + 1) * width <= this.seqWidth)
        ? this.selection : null;
      const rs = this.readSel && (this.readSel.c1 - this.readSel.c0 + 1) * width <= this.seqWidth ? this.readSel : null;
      const mouse = !s && !rs && !this.readCaret && this.caret === null && x !== null && x > this.nameWidth;
      const at = mouse ? (x - this.nameWidth + previousX) / previous : null;
      const middle = s ? this.selectionMiddle(s) : rs ? (rs.c0 + rs.c1) / 2
        : this.readCaret ? this.readCaret.col : this.caret !== null ? this.caret : (previousX + this.seqWidth / 2) / previous;
      this.navigate({ charWidth: width, scrollX: (mouse ? at : middle) * width - (mouse ? x - this.nameWidth : this.seqWidth / 2) }, true, mouse ? at : middle);
    }

    hasZoomSelection() { return !!(this.selection || this.readSel || this.readCaret); }

    fitWhole() {
      if (this.circularMap) return this.navigate({ circZoom: 1 });
      this.navigate({ charWidth: this.minCharWidth(), scrollX: 0, scrollY: 0 });
    }

    fitSelection() {
      const s = this.selection, read = this.readSel || this.readCaret;
      if (!s && !read) return;
      const length = s ? this.selectionLength(s) : this.readSel
        ? read.c1 - read.c0 + 1 : read.read[1] - read.read[0] + 1;
      const middle = s ? this.selectionMiddle(s) : this.readSel
        ? (read.c0 + read.c1 + 1) / 2 : (read.read[0] + read.read[1] + 1) / 2;
      if (this.circularMap) {
        const g = this.circGeometry();
        // Fit the selected arc across the usable width, centered at the top.
        const angle = Math.min(Math.PI, Math.max(1, length) / this.length * 2 * Math.PI);
        const radius = Math.max(g.base, (this.width - 80) / (2 * Math.sin(angle / 2)));
        const max = Math.max(1, 18 * this.length / (2 * Math.PI * g.base));
        return this.navigate({ circZoom: length >= this.length / 2 ? 1 : Math.min(max, radius / g.base), rotation: middle });
      }
      const width = Math.min(MAX_CHAR, Math.max(this.minCharWidth(), (this.seqWidth - 2 * EDGE) / Math.max(1, length)));
      this.navigate({ charWidth: width, scrollX: middle * width - this.seqWidth / 2 }, true, middle);
      if (s) this.scrollToRow(s.r0);
      else this.centerReadRow();
    }

    columnAt(x) { return Math.floor((x - this.nameWidth + this.scrollX) / this.charWidth); }

    rowAt(y) {
      const inside = y - this.rowsTop + this.scrollY;
      return this.rows.findIndex((r) => inside >= r.top && inside < r.top + r.height);
    }

    // ── Selection ──────────────────────────────────────────────────────────
    // wraps: c0..end and 0..c1 (c0 > c1), across the origin of a circular sequence
    // consensus: the consensus row's columns (Copy copies the consensus; rows are
    // all rows, so Extract and the identity matrix work on those columns)
    // picked: separate rows (Ctrl+click on names), else the rows r0..r1
    // parts: column ranges within c0..c1 (a feature in parts: its exons, not what's between)
    select(c0, c1, r0, r1, wraps = false, consensus = false, picked = null, parts = null, wholeRows = false) {
      const clampCol = (c) => Math.min(this.length - 1, Math.max(0, c));
      const clampRow = (r) => Math.min(this.rows.length - 1, Math.max(0, r));
      if (wraps && this.single && this.circularSequence && c0 > c1) {
        this.selection = { c0: clampCol(c0), c1: clampCol(c1), r0: 0, r1: 0, wraps: true };
      } else {
        this.selection = { c0: clampCol(Math.min(c0, c1)), c1: clampCol(Math.max(c0, c1)),
                           r0: clampRow(Math.min(r0, r1)), r1: clampRow(Math.max(r0, r1)) };
        if (consensus && (this.aligned || (this.single && this.mapping))) this.selection.consensus = true;
        const rows = picked && [...new Set(picked)].sort((a, b) => a - b);
        if (rows && rows.length > 1 && rows[rows.length - 1] - rows[0] + 1 !== rows.length) {  // not one block
          Object.assign(this.selection, { rows, r0: rows[0], r1: rows[rows.length - 1] });
        }
        if (parts && parts.length > 1) this.selection.parts = [...parts].sort((a, b) => a[0] - b[0]);
        if (wholeRows) this.selection.wholeRows = true;
      }
      this.caret = null;
      this.draw();
      this.selectionChanged();
    }

    // The selection's column ranges: one, two when it wraps around the origin, or
    // a feature's parts
    selectionPieces(s = this.selection) {
      if (!s) return [];
      return s.parts || (s.wraps ? [[s.c0, this.length - 1], [0, s.c1]] : [[s.c0, s.c1]]);
    }

    selectionLength(s = this.selection) {
      return this.selectionPieces(s).reduce((n, [a, b]) => n + b - a + 1, 0);
    }

    // A feature clicked: its range, or its parts (exons) when it has several
    selectFeature(feature, r) {
      const parts = feature.parts.length > 1 ? feature.parts.map((p) => [p.c0 ?? p.start, p.c1 ?? p.end]) : null;
      this.select(feature.c0, feature.c1, r, r, false, false, null, parts);
    }

    // A feature selected and brought to the middle (the annotation table's click): the map turned to it,
    // the line moved to it, its row scrolled into view
    revealFeature(feature, r) {
      this.selectFeature(feature, r);
      const mid = (feature.c0 + feature.c1 + 1) / 2;
      if (this.circularMap) this.rotateTo(mid);
      else this.centerOn(mid);
      this.scrollToRow(r);
      this.draw();
    }

    // The middle of the selection (the zoom anchor), across the origin too
    selectionMiddle(s = this.selection) {
      return s.wraps ? ((s.c0 + this.selectionLength(s) / 2) % this.length) : (s.c0 + s.c1 + 1) / 2;
    }

    // Whether the sequence shown is circular (its topology, whichever map shows it)
    setTopology(circular) {
      this.circularSequence = !!circular;
      this.draw();
    }

    clearSelection() {
      if (this.compare) this.compare.active = null;
      this.selection = null;
      this.draw();
      this.selectionChanged();
    }

    // The cursor: between columns, 0..length, in a row (a single sequence: row 0);
    // typing goes there
    setCaret(pos, row = 0) {
      this.caretRow = Math.max(0, Math.min(this.rows.length - 1, row));
      const end = this.aligned || this.single ? this.length : this.rows[this.caretRow].seq.length;  // its own end
      this.caret = Math.max(0, Math.min(end, pos));
      this.selection = null;
      this.draw();
      this.selectionChanged();
    }

    // ── Mapped reads (a mapping item): coverage and consensus ─────────────
    // m: { depth: [per position], consensus: one character per reference position
    // ("*" deleted, N too few reads), insertions: { position: bases after it }, minDepth }
    // ── Genome comparison (progressiveMauve's blocks) ──────────────────────
    // blocks: [{ parts: [{ row, start, end, strand }] }] (1-based, inclusive).
    // Each genome's blocks are drawn in a lane under its features, the same colour
    // in every genome, above a line on the + strand and below it when inverted, as
    // in Mauve; bands link a block to the same block in the next genome that has
    // it. Clicking one highlights it everywhere and calls options.onBlockClick
    // (app.js lines the genomes up on it). shifts: each row's leading gaps (the
    // genomes lined up: the rows' sequences start with that many "-").
    setComparison(blocks, shifts = null, active = null) {
      this.compare = blocks ? {
        blocks: blocks.map((b, k) => ({ ...b, index: k, hue: (k * 137.508 + 20) % 360 })),
        active, shifts: shifts || this.rows.map(() => 0),
      } : null;
      this.layout();
      this.draw();
    }

    // A genome's position (1-based) at a column, and back (its leading gaps counted)
    blockColumn(r, pos) { return pos - 1 + this.compare.shifts[r]; }

    // The block (and its part) under the mouse in a genome's blocks lane
    blockAt(x, y) {
      if (!this.compare || x < this.nameWidth || y < this.rowsTop) return null;
      const r = this.rowAt(y);
      if (r < 0) return null;
      const inRow = y - this.rowsTop + this.scrollY - this.rows[r].top - this.rows[r].blocksTop;
      if (inRow < 0 || inRow >= BLOCKS) return null;
      const pos = this.columnAt(x) + 1 - this.compare.shifts[r];
      for (const block of this.compare.blocks) {
        const part = block.parts.find((p) => p.row === r && pos >= p.start && pos <= p.end);
        if (part) return { block, part, row: r };
      }
      return null;
    }

    blockTip({ block, part }) {
      const n = (x) => x.toLocaleString();
      return `Block ${block.index + 1} of ${this.compare.blocks.length} · ${n(part.start)}–${n(part.end)} ` +
        `(${n(part.end - part.start + 1)} bp${part.strand < 0 ? ", inverted" : ""}) · in ${block.parts.length} of ` +
        `${this.rows.length} genomes · right-click to extract it as an alignment`;
    }

    drawComparison(xOf) {
      const ctx = this.ctx, c = this.compare;
      const yOf = (r) => this.rowsTop + this.rows[r].top - this.scrollY;
      const lit = (block) => c.active === null || c.active === block.index;
      const clip = (x) => Math.max(this.nameWidth - 5, Math.min(this.width + 5, x));
      // bands first, under the blocks: from a genome's lane to the next genome with the block
      for (const block of c.blocks) {
        const parts = [...block.parts].sort((a, b) => a.row - b.row);
        ctx.fillStyle = `hsla(${block.hue}, 60%, 55%, ${c.active === block.index ? 0.45 : lit(block) ? 0.2 : 0.06})`;
        for (let i = 0; i + 1 < parts.length; i++) {
          const a = parts[i], b = parts[i + 1];
          const ya = yOf(a.row) + this.rows[a.row].blocksTop + BLOCKS, yb = yOf(b.row);
          if (yb < this.rowsTop || ya > this.rowsTop + this.rowsHeight) continue;
          const [a0, a1] = [xOf(this.blockColumn(a.row, a.start)), xOf(this.blockColumn(a.row, a.end) + 1)];
          const [b0, b1] = [xOf(this.blockColumn(b.row, b.start)), xOf(this.blockColumn(b.row, b.end) + 1)];
          if (Math.max(a1, b1) < this.nameWidth || Math.min(a0, b0) > this.width) continue;
          const crossed = a.strand !== b.strand;  // inverted between the two: the band twists
          ctx.beginPath();
          ctx.moveTo(clip(a0), ya);
          ctx.lineTo(clip(a1), ya);
          ctx.lineTo(clip(crossed ? b0 : b1), yb);
          ctx.lineTo(clip(crossed ? b1 : b0), yb);
          ctx.closePath();
          ctx.fill();
        }
      }
      // each genome's lane: a line, the blocks above it (+) or below it (inverted)
      ctx.font = SMALL_FONT;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      this.rows.forEach((row, r) => {
        const mid = yOf(r) + row.blocksTop + BLOCKS / 2;
        if (mid < this.rowsTop - BLOCKS || mid > this.rowsTop + this.rowsHeight + BLOCKS) return;
        ctx.fillStyle = "#b5b5b5";
        const start = Math.max(this.nameWidth, xOf(c.shifts[r])), end = Math.min(this.width, xOf(c.shifts[r] + row.bases));
        if (end > start) ctx.fillRect(start, mid - 0.5, end - start, 1);
        for (const block of c.blocks) {
          for (const p of block.parts) {
            if (p.row !== r) continue;
            const x0 = xOf(this.blockColumn(r, p.start)), x1 = xOf(this.blockColumn(r, p.end) + 1);
            if (x1 < this.nameWidth || x0 > this.width) continue;
            const top = p.strand > 0 ? mid - 10 : mid + 1;
            ctx.fillStyle = `hsl(${block.hue}, 60%, ${lit(block) ? 55 : 82}%)`;
            this.colRect(x0, top, Math.max(1, x1 - x0), 9);
            if (c.active === block.index) {
              ctx.strokeStyle = `hsl(${block.hue}, 60%, 25%)`;
              ctx.lineWidth = 2;
              ctx.strokeRect(x0, top, Math.max(1, x1 - x0), 9);
              ctx.lineWidth = 1;
            }
            if (x1 - x0 > 24) {
              ctx.fillStyle = lit(block) ? "#fff" : "#999";
              ctx.fillText(String(block.index + 1), (Math.max(x0, this.nameWidth) + Math.min(x1, this.width)) / 2, top + 5);
            }
          }
        }
      });
    }

    setMapping(m) {
      if (!m) {
        this.mapping = null;
        this.consensus = null;  // the alignment-style colouring (drawRow) goes with the mapping
        this.reads = null;
        this.readsEnabled = false;
        this.readRows = [];
        this.packedReadsSource = null;
        this.readLane = 0;
        this.readCaret = null;
        this.readSel = null;
      } else {
        let max = 1;
        for (const d of m.depth) if (d > max) max = d;
        const ref = this.rows[0].seq;
        const diffs = [];  // columns where the consensus isn't the reference
        for (let c = 0; c < m.consensus.length; c++) if (m.consensus[c] !== ref[c]) diffs.push(c);
        const insertions = Object.keys(m.insertions || {}).map(Number).sort((a, b) => a - b);
        const clips = m.clips ? new Int32Array(m.consensus.length) : null;  // reads clipped at each position
        let maxClips = 1;
        for (const [pos, n] of m.clips || []) { clips[pos] = n; if (n > maxClips) maxClips = n; }
        const variable = new Map((m.variable || []).map((v) => [v.pos, v]));
        this.mapping = { ...m, maxDepth: max, diffs: Int32Array.from(diffs), insertionAt: insertions,
                         clipsAt: clips, maxClips, variableAt: variable };
        // As in an alignment: bases are coloured where they differ from the consensus
        this.consensus = m.consensus;
        this.rows[0].mismatches = diffs;
      }
      this.layout();
      this.draw();
    }

    // The mapping's reads in the window shown (the server's window, [start, end,
    // strand, mapq, cigar, seq, row] each), or null: drawn under the consensus
    setReads(data) {
      const previous = { sel: this.readSel, caret: this.readCaret };
      this.readsEnabled = !!data?.all;
      const list = data && data.reads ? [...data.reads] : null;
      this.reads = list ? { ...data, reads: list } : null;
      const found = (old) => list?.find((r) => this.sameRead(r, old.read));
      // a wide window samples reads: keep the selected one (a sequence's
      // selection also stays while it is off screen)
      for (const old of [previous.sel?.read, previous.caret?.read]) {
        if (old && list && !list.some((r) => this.sameRead(r, old))) list.push(old);
      }
      if (list) list.sort((a, b) => a[0] - b[0]);
      this.readSel = previous.sel && (found(previous.sel) ? { ...previous.sel, read: found(previous.sel) } : previous.sel);
      this.readCaret = previous.caret && (found(previous.caret) ? { ...previous.caret, read: found(previous.caret) } : previous.caret);
      if (!this.reads) { this.readSel = this.readCaret = null; this.readOffset = 0; }
      this.layout();
      this.draw();
    }

    sameRead(a, b) {
      if (a === b) return true;
      if (!a || !b) return false;
      if (a[7] != null && b[7] != null) return a[7] === b[7] && a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
      return a.slice(0, 6).every((value, i) => value === b[i]);
    }

    readRowHeight() {
      // Keep alignment spacing while letters are visible; below that threshold,
      // taper smoothly to compact bars as the sequence zooms farther out.
      const compact = 3;
      const fraction = Math.max(0, Math.min(1, this.charWidth / LETTERS_FROM));
      return compact + (ROW - compact) * fraction;
    }

    // Pack the current window tightly, keeping the previous row for reads that
    // remain visible while the user pans. New reads take the first free row.
    packReads() {
      const list = this.reads?.reads || [];
      if (this.packedReadsSource !== list) {
        let rows;
        if (this.reads?.all && list.every((read) => Number.isInteger(read[6]))) {
          // Full mode can group by the sidecar's complete, non-overlapping lanes
          // in linear time, even when the window contains tens of thousands.
          const lanes = new Map();
          for (const read of list) {
            if (!lanes.has(read[6])) lanes.set(read[6], { lane: read[6], end: -1, reads: [] });
            const row = lanes.get(read[6]);
            row.reads.push(read);
            row.end = Math.max(row.end, read[1]);
          }
          rows = [...lanes.values()].sort((a, b) => a.lane - b.lane);
        } else {
          const key = (r) => `${r[0]}\t${r[1]}\t${r[2]}\t${r[3]}\t${r[4]}\t${r[5]}\t${r[6] ?? ""}\t${r[7] ?? ""}`;
          const previous = new Map();
          this.readRows.forEach((row, i) => row.reads.forEach((read) => previous.set(key(read), i)));
          rows = [];
          const add = (read, index) => {
            rows[index] ||= { end: -1, reads: [] };
            rows[index].reads.push(read);
            rows[index].end = Math.max(rows[index].end, read[1]);
          };
          const overlaps = (read, row) => row.reads.some((other) => read[0] <= other[1] && read[1] >= other[0]);
          const placed = new Set();
          // Keep the rows of reads shared with the last window wherever possible.
          for (const read of list) {
            const index = previous.get(key(read));
            if (index !== undefined && !overlaps(read, rows[index] || { reads: [] })) {
              add(read, index);
              placed.add(read);
            }
          }
          // Reads entering the window fill the first lane where they don't overlap.
          for (const read of list) {
            if (placed.has(read)) continue;
            let index = rows.findIndex((row) => !row || !overlaps(read, row));
            if (index < 0) index = rows.length;
            add(read, index);
          }
        }
        this.readRows = rows.filter((row) => row?.reads.length);
        this.readShown = list.length;
        this.packedReadsSource = list;
      }
      const per = this.readRowsVisible();
      this.readOffset = Math.min(this.readOffset, Math.max(0, this.readRows.length - per));
      this.centerReadRow();
      // the lane's height is layout's (it fills the viewer)
    }

    readRowsVisible() { return Math.max(1, Math.floor((Math.max(0, this.readLane) - 8) / this.readRowHeight())); }

    readScrollbar() {
      const per = this.readRowsVisible(), total = this.readRows.length;
      const top = this.readsTop + 4, track = Math.max(0, this.readLane - 8);
      if (total <= per || track <= 0) return null;
      const thumb = Math.min(track, Math.max(24, track * per / total));  // (as the rows' bar)
      const max = Math.max(0, total - per);
      return { x: this.width - SCROLLBAR - 2, top: top + (track - thumb) * (this.readOffset / max),
               trackTop: top, track, thumb, max };
    }

    onReadScrollbar(x, y) {
      return !!this.readScrollbar() && x >= this.width - SCROLLBAR - 4 &&
        y >= this.readsTop && y < this.readsTop + this.readLane;
    }

    dragReadScrollbar(e) {
      const bar = this.readScrollbar(), start = this.point(e).y;
      if (!bar) return;
      const onThumb = start >= bar.top && start < bar.top + bar.thumb;
      const offsetAt = (y) => Math.max(0, Math.min(bar.max,
        ((y - bar.trackTop - bar.thumb / 2) / Math.max(1, bar.track - bar.thumb)) * bar.max));
      const from = onThumb ? this.readOffset : offsetAt(start);
      this.readOffset = Math.round(from);
      this.draw();
      const y0 = this.point(e).y;
      this.drag((ev) => {
        this.readOffset = Math.round(onThumb ? from + (this.point(ev).y - y0) *
          bar.max / Math.max(1, bar.track - bar.thumb) : offsetAt(this.point(ev).y));
        this.readOffset = Math.max(0, Math.min(bar.max, this.readOffset));
        this.draw();
      });
    }

    // The selected read's row in the middle of the lane (zooming keeps to it)
    centerReadRow() {
      const mark = this.readSel || this.readCaret;
      if (!mark || !this.readRows.length) return;
      let row = Number.isInteger(mark.row) && mark.row >= 0
        ? this.readRows.findIndex((r) => r.lane === mark.row)
        : -1;
      if (row < 0) row = this.readRows.findIndex((r) => r.reads.some((read) => this.sameRead(read, mark.read)));
      if (row < 0) return;
      const per = this.readRowsVisible();
      this.readOffset = Math.max(0, Math.min(row - Math.floor(per / 2), Math.max(0, this.readRows.length - per)));
    }

    // ── A Sanger read: its trace and qualities above the bases ────────────
    // t: /api/trace (the whole read; the sequence shown is its part start..end).
    // Each base's column holds its stretch of the trace, from halfway to the
    // peak before to halfway to the next. The curves are scaled to the read's
    // tall peaks (a few higher ones go off the top).
    setTrace(t) {
      this.trace = t ? { ...t, scale: traceScale(t) } : null;
      this.layout();
      this.draw();
    }

    // The trace still matches the bases shown (only replaced bases since: bases
    // added or removed would shift everything after them)
    traceFits() {
      const t = this.trace;
      return t && this.rows[0] && this.rows[0].seq.length === t.end - t.start;
    }

    drawTrace(first, last, xOf) {
      const ctx = this.ctx, t = this.trace, cw = this.charWidth;
      const top = RULER + 2, h = TRACE - 6, base = top + h;
      ctx.fillStyle = "#fbfbfc";
      ctx.fillRect(this.nameWidth, top, this.seqWidth, h);
      const note = (text) => {
        ctx.fillStyle = "#888";
        ctx.font = SMALL_FONT;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(text, this.nameWidth + 10, top + h / 2);
      };
      if (!this.traceFits()) return note("Bases were added or removed in this read: its trace no longer lines up (Undo, or trim it again).");
      // quality bars: as high as the quality (to 60), blue from 30, olive from 20, red under; zoomed out, the lowest in each pixel
      const qy = RULER + TRACE;
      if (t.quality.length) {
        this.mappingBins(first, last, xOf, (c0, c1, x, w) => {
          let q = 99;
          for (let c = c0; c <= c1; c++) q = Math.min(q, t.quality[t.start + c] ?? 99);
          ctx.fillStyle = q >= 30 ? "#7d9cc4" : q >= 20 ? IDENTITY_COLORS.part : IDENTITY_COLORS.low;
          const bh = Math.max(1, Math.round((Math.min(60, q) / 60) * (QUAL - 4)));
          this.colRect(x, qy + QUAL - 2 - bh, Math.max(1, w - (cw >= 4 ? 1 : 0)), bh);
        });
      }
      if (cw < TRACE_FROM) return note("Zoom in to see the trace");
      this.traceCurves(t, top, h, first, last, xOf, (c) => t.start + c);
    }

    // The four curves: each base's stretch of scans (from halfway to the peak
    // before to halfway to the next) across its column; baseAt(column): the
    // base of the read there, or -1 (a gap: the curves break)
    traceCurves(t, top, h, first, last, xOf, baseAt) {
      const ctx = this.ctx, cw = this.charWidth, p = t.peaks, base = top + h;
      ctx.save();
      ctx.beginPath();
      ctx.rect(this.nameWidth, top, this.seqWidth, h);
      ctx.clip();
      ctx.lineWidth = 1.2;
      for (const [letter, values] of Object.entries(t.channels)) {
        ctx.strokeStyle = TRACE_COLORS[letter] || "#888";
        ctx.beginPath();
        let started = false;
        for (let c = Math.max(0, first - 1); c < Math.min(last + 1, this.length); c++) {
          const b = baseAt(c);
          if (b < 0 || b >= p.length) { started = false; continue; }
          const s0 = b > 0 ? (p[b - 1] + p[b]) / 2 : p[b] - (p[b + 1] - p[b]) / 2;
          const s1 = b < p.length - 1 ? (p[b] + p[b + 1]) / 2 : p[b] + (p[b] - p[b - 1]) / 2;
          for (let scan = Math.ceil(s0); scan <= Math.floor(s1); scan++) {
            const x = xOf(c) + ((scan - s0) / Math.max(1, s1 - s0)) * cw;
            const y = base - (Math.min(values[scan] || 0, t.scale * 1.15) / t.scale) * (h - 4);
            if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true; }
          }
        }
        ctx.stroke();
      }
      ctx.restore();
    }

    // A read's trace under its row in an alignment (its gaps break the curves)
    drawRowTrace(row, y, first, last, xOf) {
      const t = row.trace, ctx = this.ctx;
      const top = y + 2, h = ROW_TRACE - 6;
      ctx.fillStyle = "#fbfbfc";
      ctx.fillRect(this.nameWidth, top, this.seqWidth, h);
      if (row.bases + t.offset > t.end - t.start) return;  // edited since: it no longer lines up
      const from = t.start + t.offset - 1;  // basesUpTo counts from 1
      this.traceCurves(t, top, h, first, last, xOf, (c) => (row.seq[c] === "-" ? -1 : from + row.basesUpTo[c]));
    }

    traceTip(col, y) {
      const t = this.trace, b = t.start + col;
      if (!this.traceFits()) return "";
      const q = t.quality.length ? `, quality ${t.quality[b]}` : "";
      const called = t.bases[b] !== this.rows[0].seq[col] ? ` (called ${t.bases[b]})` : "";
      return `Position ${col + 1}: ${this.rows[0].seq[col]}${called}${q} · base ${b + 1} of the read's ${t.bases.length}` +
        (y >= RULER + TRACE ? "" : " · peaks scaled to the read's tall ones");
    }

    // The rows of a mapping's header, top to bottom: coverage, agreement and
    // clipped reads (mappings made since they exist), consensus
    mappingTop(row) {
      const m = this.mapping;
      let y = RULER + COVERAGE;
      if (row === "agreement") return y;
      if (m?.agreement) y += AGREE;
      if (row === "clips") return y;
      if (m?.clipsAt) y += CLIPS;
      return y;
    }

    // The columns of each drawing bin: one column, or (zoomed out) all in a pixel
    mappingBins(first, last, xOf, each) {
      const cw = this.charWidth;
      const bins = cw >= 1 ? last - first : this.seqWidth;
      for (let b = 0; b < bins; b++) {
        if (cw >= 1) { each(first + b, first + b, xOf(first + b), cw); continue; }
        const c0 = Math.floor((this.scrollX + b) / cw);
        if (c0 < 0) continue;  // the margin before the first base: nothing there
        if (c0 >= this.length) break;
        each(c0, Math.max(c0, Math.min(this.length, Math.floor((this.scrollX + b + 1) / cw)) - 1), this.nameWidth + b, 1);
      }
    }

    // The coverage graph (the highest depth in each pixel; red underneath where
    // it drops below the minimum depth; the stretches extended from the reads
    // tinted orange), the agreement and clipped-reads rows, the consensus row
    drawMapping(first, last, xOf, letters) {
      const ctx = this.ctx, m = this.mapping, cw = this.charWidth;
      const top = RULER + 2, h = COVERAGE - 8, base = top + h;
      ctx.fillStyle = "#f6f7f9";
      ctx.fillRect(this.nameWidth, top, this.seqWidth, h);
      const ext = m.extension || {};
      ctx.fillStyle = "#fbe3cc";
      for (const [c0, c1] of [[0, (ext.left || 0) - 1], [this.length - (ext.right || 0), this.length - 1]]) {
        if (c1 < c0) continue;
        const x0 = Math.max(this.nameWidth, xOf(c0)), x1 = Math.min(this.width, xOf(c1 + 1));
        if (x1 > x0) ctx.fillRect(x0, top, x1 - x0, h);
      }
      const bins = cw >= 1 ? last - first : this.seqWidth;
      for (let b = 0; b < bins; b++) {
        let c0, c1, x, w;
        if (cw >= 1) {
          c0 = c1 = first + b; x = xOf(c0); w = cw;
        } else {
          c0 = Math.floor((this.scrollX + b) / cw);
          if (c0 < 0) continue;  // the margin before the first base
          if (c0 >= this.length) break;
          c1 = Math.max(c0, Math.min(this.length, Math.floor((this.scrollX + b + 1) / cw)) - 1);
          x = this.nameWidth + b; w = 1;
        }
        let hi = 0, lo = Infinity;
        for (let c = c0; c <= c1; c++) {
          const d = m.depth[c] || 0;
          if (d > hi) hi = d;
          if (d < lo && m.consensus[c] !== "*") lo = d;  // a deletion isn't missing coverage
        }
        const bh = hi ? Math.max(1, (hi / m.maxDepth) * h) : 0;
        ctx.fillStyle = "#5b8fd6";
        this.colRect(x, base - bh, Math.max(1, w - (cw >= 4 ? 1 : 0)), bh);
        if (lo < m.minDepth) {
          ctx.fillStyle = "#d9534f";
          this.colRect(x, base, Math.max(1, w), 3);
        }
      }
      // agreement: green at 95 % or more (full height: below that is sequencing
      // noise), olive to 80 %, red under, as high as the share (from 50 %); zoomed
      // out, the least agreeing position in each pixel
      if (m.agreement) {
        const ay = this.mappingTop("agreement");
        this.mappingBins(first, last, xOf, (c0, c1, x, w) => {
          let low = 100;
          for (let c = c0; c <= c1; c++) if (m.agreement[c] < low) low = m.agreement[c];
          ctx.fillStyle = low >= 95 ? IDENTITY_COLORS.full : low >= 80 ? IDENTITY_COLORS.part : IDENTITY_COLORS.low;
          const bh = low >= 95 ? AGREE - 4 : Math.max(2, Math.round(((Math.max(50, low) - 50) / 45) * (AGREE - 4)));
          this.colRect(x, ay + AGREE - 2 - bh, Math.max(1, w), bh);
        });
      }
      // clipped reads: dark red bars, as high as the count (square root, so a few show)
      if (m.clipsAt) {
        const cy = this.mappingTop("clips");
        ctx.fillStyle = STOP;
        this.mappingBins(first, last, xOf, (c0, c1, x, w) => {
          let n = 0;
          for (let c = c0; c <= c1; c++) n += m.clipsAt[c];
          if (!n) return;
          const bh = Math.max(2, Math.round(Math.sqrt(Math.min(1, n / m.maxClips)) * (CLIPS - 4)));
          this.colRect(x, cy + CLIPS - 2 - bh, Math.max(1, w), bh);
        });
      }
      if (m.coverageOnly) return;  // (an assembly graph's track: its depth only)
      // consensus: a grey bar where it agrees with the reference, the differences coloured
      const y = this.mappingTop("consensus");
      ctx.fillStyle = "#e3e5ea";
      const x0 = Math.max(this.nameWidth, xOf(0)), x1 = Math.min(this.width, xOf(this.length));
      ctx.fillRect(x0, y + 4, x1 - x0, ROW - 8);
      const from = lowerBound(m.diffs, first);
      for (let i = from; i < m.diffs.length && m.diffs[i] < last; i++) {
        const c = m.diffs[i], ch = m.consensus[c];
        ctx.fillStyle = ch === "*" ? "#444" : ch === "N" ? "#c4c4c4" : this.colorOf(ch);
        ctx.fillRect(xOf(c), y + 1, Math.max(2, cw), ROW - 2);
      }
      if (letters) {
        this.drawLetters(first, last, xOf, y + ROW / 2, (c) => (m.consensus[c] === "*" ? "-" : m.consensus[c] || ""),
                         (c) => (m.consensus[c] === this.rows[0].seq[c] ? "#999" : m.consensus[c] === "*" ? "#fff" : "#111"));
      }
      ctx.fillStyle = "#8e44ad";  // insertions: a mark between the columns
      for (const p of m.insertionAt) {
        if (p + 1 < first || p >= last) continue;
        ctx.fillRect(xOf(p + 1) - 1, y, 2, ROW);
      }
    }

    // ── The mapping's reads lane ──────────────────────────────────────────
    // Packed rows under the consensus, drawn as the alignment draws its rows:
    // bases with the consensus colouring, or the grey bar and ticks zoomed out.
    drawReads(first, last, xOf) {
      const ctx = this.ctx, rh = this.readRowHeight(), top = this.readsTop;
      ctx.fillStyle = "#fafbfc";
      ctx.fillRect(this.nameWidth, top, this.seqWidth, this.readLane);
      const per = this.readRowsVisible();
      const from = Math.min(this.readOffset, Math.max(0, this.readRows.length - per));
      for (let ri = from; ri < Math.min(this.readRows.length, from + per); ri++) {
        const y = top + 4 + (ri - from) * rh;
        for (const read of this.readRows[ri].reads) {
          if (read[1] < first || read[0] > last) continue;
          if (!this.letters) { this.drawReadBar(read, y, rh, first, last, xOf); continue; }
          this.drawSequenceRow({
            base: (col) => this.readMap(read)[col - read[0]] || "",
            first: read[0],
            last: read[1],
            segments: this.readSegments(read),
            mismatches: this.readDiffs(read),
            line: true,
          }, y, rh, first, last, xOf, this.letters);
          // A one-pixel boundary keeps adjacent reads distinguishable when
          // their packed bars touch, without adding space to the row layout.
          const edge = xOf(read[0]);
          if (edge > this.nameWidth && edge < this.width) {
            // At base zoom, use a darker edge against nucleotide backgrounds;
            // compact bars keep the light seam that separates touching reads.
            ctx.fillStyle = this.letters ? "rgba(55,65,75,.55)" : "rgba(255,255,255,.9)";
            ctx.fillRect(Math.floor(edge), y, 1, rh);
          }
        }
      }
      const bar = this.readScrollbar();
      if (bar) this.paintScrollbar(bar.trackTop, bar.track, bar.top, bar.thumb);  // where in the packed rows the lane is
      const marked = this.readSel || this.readCaret;
      if (marked) {  // the selected read's row, as an alignment's selection
        const caretLane = this.readCaret?.row;
        let ri = Number.isInteger(caretLane) && caretLane >= 0
          ? this.readRows.findIndex((r) => r.lane === caretLane)
          : -1;
        if (ri < 0) ri = this.readRows.findIndex((r) => r.reads.some((read) => this.sameRead(read, marked.read)));
        if (ri >= from && ri < from + per) {
          const y = top + 4 + (ri - from) * rh;
          if (this.readSel) {
            const a = Math.max(this.nameWidth, xOf(Math.max(this.readSel.c0, first)));
            const b = Math.min(this.width, xOf(Math.min(this.readSel.c1 + 1, last)));
            if (b > a) {
              ctx.fillStyle = SELECTION;
              ctx.fillRect(a, y, b - a, rh);
              ctx.fillStyle = SELECTION_EDGE;
              ctx.fillRect(a, y, 1, rh);
              ctx.fillRect(Math.max(a, b - 1), y, 1, rh);
            }
          }
        }
      }
    }

    // A read zoomed out: a solid bar over its aligned stretches (at least a pixel
    // wide, so sub-pixel reads still add up to the coverage's shape), a thin line
    // across deletions, a seam at its start only when it is wide enough to need one,
    // and mismatch ticks only from half a pixel per base (further out they were
    // confetti over everything; the agreement track shows differences there)
    drawReadBar(read, y, rh, first, last, xOf) {
      const ctx = this.ctx, cw = this.charWidth;
      const bar = rh > 3 ? rh - 1 : rh - 0.5;  // rows touch, with a hairline between them
      const segments = this.readSegments(read);
      if (segments.length > 1 && (read[1] - read[0]) * cw >= 4) {
        ctx.fillStyle = "#9a9a9a";
        this.colRect(xOf(read[0]), y + rh / 2 - 0.5, (read[1] - read[0] + 1) * cw, 1);
      }
      ctx.fillStyle = "#b4b9c1";
      for (const [start, end] of segments) {
        if (end <= first || start >= last) continue;
        ctx.fillRect(Math.floor(xOf(start)), y, Math.max(1, Math.round((end - start) * cw)), bar);
      }
      const x0 = xOf(read[0]), wide = (read[1] - read[0] + 1) * cw >= 6;
      if (wide && x0 > this.nameWidth && x0 < this.width) {
        ctx.fillStyle = "rgba(255,255,255,.9)";
        ctx.fillRect(Math.floor(x0), y, 1, bar);
      }
      if (cw < 0.5) return;
      for (const col of this.readDiffs(read)) {
        const ch = this.readMap(read)[col - read[0]];
        if (!ch || ch === "-") continue;
        ctx.fillStyle = this.colorOf(ch);
        ctx.fillRect(Math.floor(xOf(col)), y, Math.max(1, cw), bar);
      }
    }

    // A read's bases by reference column (null outside; "-" for a deletion), its
    // runs of bases and the columns where it differs from the consensus; cached
    // on the read's array, so panning and zooming don't recompute them
    readMap(read) {
      if (read.byColumn) return read.byColumn;  // not read.map: that is Array.prototype.map
      const map = new Array(read[1] - read[0] + 1).fill(null);
      let r = read[0], i = 0;
      for (const [n, op] of read.ops || (read.ops = parseCigar(read[4]))) {
        if (op === "M" || op === "=" || op === "X") {
          for (let k = 0; k < n; k++, r++, i++) map[r - read[0]] = read[5][i] || "N";
        } else if (op === "I" || op === "S") {
          i += n;
        } else if (op === "D" || op === "N") {
          for (let k = 0; k < n; k++, r++) map[r - read[0]] = "-";
        }
      }
      return (read.byColumn = map);
    }

    readSegments(read) {
      if (read.segments) return read.segments;
      const segments = [];
      let r = read[0], start = null;
      for (const [n, op] of read.ops || (read.ops = parseCigar(read[4]))) {
        if (op === "M" || op === "=" || op === "X") {
          if (start === null) start = r;
          r += n;
        } else if (op === "D" || op === "N") {
          if (start !== null) segments.push([start, r]);
          start = null;
          r += n;
        } else if (op === "I" || op === "S") {
          // no reference columns
        }
      }
      if (start !== null) segments.push([start, r]);
      return (read.segments = segments);
    }

    readDiffs(read) {
      if (read.diffs) return read.diffs;
      const map = this.readMap(read), ref = this.rows[0].seq, diffs = new Set();
      for (let col = read[0]; col <= read[1]; col++) {
        const base = map[col - read[0]];
        if (!base || base === "-") continue;
        const consensus = this.consensus?.[col];
        const target = consensus && consensus !== "N" ? consensus : ref[col];
        if (base.toUpperCase() !== (target || "").toUpperCase()) diffs.add(col);
      }
      return (read.diffs = diffs);
    }

    // The read under the mouse in the reads lane, or null
    readAt(x, y) {
      if (!this.readLane || y < this.readsTop || y > this.readsTop + this.readLane) return null;
      const rh = this.readRowHeight();
      const per = this.readRowsVisible();
      const from = Math.min(this.readOffset, Math.max(0, this.readRows.length - per));
      const row = this.readRows[from + Math.floor((y - this.readsTop - 4) / rh)];
      if (!row) return null;
      const col = (x - this.nameWidth + this.scrollX) / this.charWidth;
      return row.reads.find((read) => read[0] <= col && col < read[1] + 1) || null;
    }

    readTip(read) {
      const mismatches = this.readDiffs(read).size;
      return `Read ${read[0] + 1}–${read[1] + 1} (${read[2]}), MAPQ ${read[3]} · ` +
        `${mismatches} mismatch${mismatches === 1 ? "" : "es"} · ${read[4]}`;
    }

    // A read's bases in the selected column range (the alignment's Copy: bases,
    // no gaps; insertion bases whose column is in the range)
    readRangeFasta(label) {
      const sel = this.readSel;
      if (!sel) return "";
      const read = sel.read, { c0, c1 } = sel;
      const ops = read.ops || (read.ops = parseCigar(read[4]));
      let r = read[0], i = 0, bases = "";
      for (const [n, op] of ops) {
        if (op === "M" || op === "=" || op === "X") {
          for (let k = 0; k < n; k++, r++, i++) if (r >= c0 && r <= c1) bases += read[5][i] || "N";
        } else if (op === "I") {
          if (r - 1 >= c0 && r - 1 <= c1) bases += read[5].slice(i, i + n);  // inserted after this column
          i += n;
        } else if (op === "D" || op === "N") {
          r += n;
        } else if (op === "S") {
          i += n;
        }
      }
      if (!bases) return "";
      return `>${label || "read"} ${Math.max(read[0], c0) + 1}-${Math.min(read[1], c1) + 1} ${read[2]}\n${bases}\n`;
    }

    readSelText() {
      const { read, c0, c1 } = this.readSel;
      return `Read ${read[0] + 1}–${read[1] + 1} (${read[2]}) · columns ${c0 + 1}–${c1 + 1} ` +
        `(${c1 - c0 + 1} bp)`;
    }

    readCaretText() {
      const { read, col } = this.readCaret;
      return `Read ${read[0] + 1}–${read[1] + 1} (${read[2]}) · cursor at ${col + 1}`;
    }

    // The app's Copy/Extract buttons follow the read selection too (a read and the
    // consensus are exclusive: this is called with this.selection already null)
    readSelectionChanged() {
      if (this.options.onSelection) this.options.onSelection(this.selection);
    }

    // Keep a position (the cursor, an edit) in view
    showPosition(pos) {
      if (this.circularMap) {
        const range = this.visibleRange(this.circGeometry());
        if (range && !this.inView(range, pos, pos)) this.rotateTo(pos);
      } else {
        const x = pos * this.charWidth - this.scrollX;
        if (x < 20 || x > this.seqWidth - 20) this.centerOn(pos);
      }
      this.draw();
    }

    // A row's own positions (1-based) of the bases in columns c0..c1, or null
    positionsIn(row, c0, c1) {
      const before = c0 > 0 ? row.basesUpTo[c0 - 1] : 0;
      const upTo = row.basesUpTo[c1];
      return upTo > before ? [before + 1, upTo] : null;
    }

    // The status line about the selection: which sequence(s), positions, length
    selectionText() {
      const s = this.selection;
      if (!s) return "";
      const n = (x) => x.toLocaleString();
      const span = (pos) => (pos ? `${n(pos[0])}–${n(pos[1])} (${n(pos[1] - pos[0] + 1)} ${this.unit})` : "only gaps");
      if (this.single && s.wraps) {
        return `Selected ${n(s.c0 + 1)}–${n(this.length)} + 1–${n(s.c1 + 1)} ` +
          `(${n(this.selectionLength(s))} ${this.unit}, across the origin)`;
      }
      if (s.parts && s.r0 === s.r1) {  // a feature's parts
        const row = this.rows[s.r0];
        const ranges = s.parts.map(([a, b]) => this.positionsIn(row, a, b)).filter(Boolean);
        const bases = ranges.reduce((k, [a, b]) => k + b - a + 1, 0);
        return `${this.single ? "" : `${row.name} · `}Selected ${ranges.map(([a, b]) => `${n(a)}–${n(b)}`).join(", ")} ` +
          `(${n(bases)} ${this.unit} in ${ranges.length} parts)`;
      }
      if (this.single && s.consensus) {
        const bases = this.consensus.slice(s.c0, s.c1 + 1).replace(/\*/g, "").length;
        return `Consensus · positions ${n(s.c0 + 1)}–${n(s.c1 + 1)} · ${n(bases)} ${this.unit}`;
      }
      if (this.single) {
        const pos = this.positionsIn(this.rows[0], s.c0, s.c1);
        return pos ? `Selected ${span(pos)}` : "";
      }
      if (s.consensus) {
        const bases = this.consensus.slice(s.c0, s.c1 + 1).replace(/[- ]/g, "").length;
        return `Consensus · columns ${n(s.c0 + 1)}–${n(s.c1 + 1)} · ${n(bases)} ${this.unit} (Copy copies them)`;
      }
      const picked = this.selectedRows(s), rows = picked.length;
      const columns = this.aligned ? ` · columns ${n(s.c0 + 1)}–${n(s.c1 + 1)}` : "";
      if (rows === 1) return `${this.rows[picked[0]].name} · ${span(this.positionsIn(this.rows[picked[0]], s.c0, s.c1))}${columns}`;
      const names = picked.slice(0, 4).map((r) => this.rows[r].name).join(", ") + (rows > 4 ? "…" : "");
      const of = rows === this.rows.length ? `all ${rows} sequences` : `${rows} of ${this.rows.length} sequences`;
      if (!this.aligned && !this.compare?.shifts.some(Boolean)) {  // not aligned: the same positions in each (where long enough)
        return `${span([s.c0 + 1, s.c1 + 1])} · ${of}: ${names}`;
      }
      const each = picked.slice(0, 4).map((r) => {
        const pos = this.positionsIn(this.rows[r], s.c0, s.c1);
        return `${this.rows[r].name} ${pos ? `${n(pos[0])}–${n(pos[1])}` : "(gaps)"}`;
      });
      return `Columns ${n(s.c0 + 1)}–${n(s.c1 + 1)} (${n(s.c1 - s.c0 + 1)}) · ${of} · ${each.join(", ")}${rows > 4 ? "…" : ""}`;
    }

    // The label by the mouse while dragging a selection (as in Geneious): its length
    dragTipText() {
      const s = this.selection;
      if (!s) return "";
      const n = (x) => x.toLocaleString();
      if (this.single) return `${n(this.selectionLength(s))} ${this.unit}`;
      if (s.consensus) return `${n(s.c1 - s.c0 + 1)} columns`;
      const rows = this.selectedRows(s).length;
      if (rows === 1) {  // one row: its bases (gaps not counted)
        const pos = this.positionsIn(this.rows[s.r0], s.c0, s.c1);
        return pos ? `${n(pos[1] - pos[0] + 1)} ${this.unit}` : "";
      }
      const width = `${n(s.c1 - s.c0 + 1)} ${this.aligned ? "columns" : this.unit}`;
      return rows > 1 ? `${width} × ${rows} sequences` : width;
    }

    showDragTip(ev) {
      if (!this.tip) {
        this.tip = document.createElement("div");
        this.tip.className = "seq-dragtip";
        document.body.append(this.tip);
      }
      const text = this.dragTipText();
      this.tip.hidden = !text;
      this.tip.textContent = text;
      // beside the mouse, on its left near the window's right edge (or above it near the bottom)
      const { offsetWidth: w, offsetHeight: h } = this.tip;
      const left = ev.clientX + 14 + w > window.innerWidth - 4 ? ev.clientX - 10 - w : ev.clientX + 14;
      const top = ev.clientY + 16 + h > window.innerHeight - 4 ? ev.clientY - 10 - h : ev.clientY + 16;
      this.tip.style.left = `${Math.max(4, left)}px`;
      this.tip.style.top = `${Math.max(4, top)}px`;
    }

    // The rows a selection covers: the ones picked, or r0..r1
    selectedRows(s = this.selection) {
      if (!s) return [];
      return s.rows || Array.from({ length: s.r1 - s.r0 + 1 }, (_, i) => s.r0 + i);
    }

    selectionChanged() {
      if (this.options.onSelection) this.options.onSelection(this.selection);
    }

    // What Copy puts on the clipboard: the bases of one sequence, one row or the
    // consensus (no name, no gaps: ready to paste anywhere), else FASTA (several
    // rows, with the alignment's gaps)
    selectionCopy() {
      const s = this.selection;
      if (!s) return "";
      if (this.single && s.consensus) return this.consensus.slice(s.c0, s.c1 + 1).replace(/\*/g, "");
      if (this.single) return this.selectionPieces(s).map(([a, b]) => this.rows[0].seq.slice(a, b + 1)).join("").replace(/-/g, "");
      if (s.consensus) return this.consensus.slice(s.c0, s.c1 + 1).replace(/[- ]/g, "");
      if (s.r0 === s.r1) return this.selectionPieces(s).map(([a, b]) => this.rows[s.r0].seq.slice(a, b + 1)).join("").replace(/-/g, "");
      if (this.aligned && s.wholeRows) {
        const rows = this.selectedRows(s).map((r) => this.rows[r]);
        const width = Math.max(...rows.map((row) => row.seq.length));
        const seqs = rows.map((row) => row.seq.padEnd(width, "-"));
        let start = 0, end = width;
        while (start < end && seqs.every((seq) => /[-.]/.test(seq[start]))) start++;
        while (end > start && seqs.every((seq) => /[-.]/.test(seq[end - 1]))) end--;
        return rows.map((row, i) => `>${row.name}\n${seqs[i].slice(start, end)}\n`).join("");
      }
      let text = "";
      for (const r of this.selectedRows(s)) {
        const row = this.rows[r];
        const piece = row.seq.slice(s.c0, s.c1 + 1);
        text += `>${row.name}\n${this.aligned ? piece : piece.replace(/-/g, "")}\n`;
      }
      return text;
    }

    // ── Find ───────────────────────────────────────────────────────────────
    // A motif in the sequences (gaps ignored, both strands), row names and
    // feature names; returns the count
    find(query) {
      // Allow pasted motif spacing/gaps, but never turn an accession or other
      // identifier into a shorter motif by discarding its digits/punctuation.
      const motifQuery = query.toUpperCase().replace(/[\s-]/g, "");
      const q = /^[A-Z*]+$/.test(motifQuery) ? motifQuery : "";
      const name = query.trim().toLowerCase();
      this.matches = [];
      this.matchIndex = -1;
      if (q) {
        const reverse = [...q].reverse().map((b) => COMPLEMENT[b] || b).join("");
        const motifs = reverse === q || this.protein || /[^ACGTUN]/.test(q) ? [q] : [q, reverse];
        this.rows.forEach((row, r) => {
          const ungapped = row.seq.replace(/-/g, "");
          const columns = this.columnsOf(row);
          for (const motif of motifs) {
            for (let i = ungapped.indexOf(motif); i >= 0 && this.matches.length < 10000; i = ungapped.indexOf(motif, i + 1)) {
              this.matches.push({ row: r, c0: columns[i], c1: columns[i + motif.length - 1] });
            }
          }
        });
      }
      if (name.length >= 2) {
        this.rows.forEach((row, r) => {
          if (row.name.toLowerCase().includes(name)) {
            this.matches.push({ row: r, c0: 0, c1: row.seq.length - 1, name: row.name });
          }
          for (const f of row.features) {
            if (f.name.toLowerCase().includes(name)) this.matches.push({ row: r, c0: f.c0, c1: f.c1, feature: f.name });
          }
        });
      }
      this.matches.sort((a, b) => a.c0 - b.c0 || a.row - b.row);
      this.draw();
      return this.matches.length;
    }

    // The next (or previous) match: selected and moved to the middle
    findNext(step = 1) {
      if (!this.matches.length) return null;
      this.matchIndex = (this.matchIndex + step + this.matches.length) % this.matches.length;
      const m = this.matches[this.matchIndex];
      this.select(m.c0, m.c1, m.row, m.row);
      const mid = (m.c0 + m.c1 + 1) / 2;
      if (this.circularMap) this.rotateTo(mid);
      else this.centerOn(mid);
      this.scrollToRow(m.row);
      this.draw();
      return this.matchIndex;
    }

    scrollToRow(r) {
      const row = this.rows[r];
      if (row.top < this.scrollY) this.scrollY = row.top;
      else if (row.top + row.height > this.scrollY + this.rowsHeight) this.scrollY = row.top + row.height - this.rowsHeight;
      this.clampScroll();
    }

    // ── Mouse ──────────────────────────────────────────────────────────────
    onWheel(e) {
      e.preventDefault();
      const keys = wheelKeys(e);
      if (!keys.ctrl) this.cancelNavigation();
      const { x, y } = this.point(e);
      const reads = !this.circularMap && this.single && this.mapping &&
        this.readRows.length > this.readRowsVisible();
      const horizontal = Math.abs(e.deltaX) > Math.abs(e.deltaY);
      const delta = horizontal ? e.deltaX : e.deltaY;
      if (reads && !keys.ctrl && (keys.shift || (x < this.nameWidth && !horizontal))) {
        // A sideways gesture always pans along the reference, including over the
        // names. Only a vertical gesture (or Shift+wheel) scrolls the read rows.
        if (delta) {
          const step = Math.max(1, Math.round(Math.abs(delta) / 30));
          this.readOffset += delta > 0 ? step : -step;
          this.readOffset = Math.max(0, Math.min(this.readOffset, this.readRows.length - this.readRowsVisible()));
          this.draw();
        }
        return;
      }
      if (!this.circularMap && !keys.ctrl && this.canScrollRows() && x < this.nameWidth) {
        this.scrollY += e.deltaY;  // over the names: up and down through the rows
        this.clampScroll();
        this.draw();
        return;
      }
      if (this.circularMap) {  // Ctrl+wheel zooms, the wheel rotates
        const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
        if (keys.ctrl) this.circularZoom(Math.pow(1.25, -e.deltaY / 100));
        else this.rotateBy(delta * this.options.wheelPan);
        return;
      }
      if (keys.ctrl) {
        this.zoom(Math.pow(1.25, -e.deltaY / 100), x);
      } else if (keys.shift) {
        this.scrollY += e.deltaY || e.deltaX;
        this.clampScroll();
        this.draw();
      } else {
        this.scrollX += delta * this.options.wheelPan;
        this.clampScroll();
        this.draw();
      }
    }

    onMouseDown(e) {
      if (e.button !== 0 || !this.length) return;
      this.cancelNavigation();
      this.hoverTrack = false;
      this.canvas.focus();
      const { x, y } = this.point(e);
      if (!this.circularMap && y >= RULER && y < RULER + (this.gcHeight?.() || 0)) return;
      if (this.onScrollbar(x, y)) return this.dragScrollbar(e);
      if (this.onReadScrollbar(x, y)) return this.dragReadScrollbar(e);
      if (y > RULER && y < this.height - OVERVIEW - STATUS && this.onNameEdge(x)) return this.dragNameEdge();
      // Coverage, agreement and clipped-read bands are data tracks. They show
      // values on hover, but are not sequence columns to select or drag across.
      if (this.single && this.mapping && y >= RULER && y < this.mappingTop("consensus")) return;
      if (this.readLane && y >= this.readsTop && y < this.readsTop + this.readLane && x >= this.nameWidth) {
        const read = this.readAt(x, y);
        if (!read) {  // empty lane: let the read selection go
          this.readCaret = this.readSel = null;
          this.readSelectionChanged();
          this.draw();
          return;
        }
        const clamp = (c) => Math.max(read[0], Math.min(read[1], c));
        const anchor = clamp(this.columnAt(x));
        const start = { x: e.clientX, y: e.clientY };
        let moved = false;
        // a read and the consensus don't both keep the focus
        this.selection = null;
        this.caret = null;
        this.readSel = null;
        this.readCaret = { read, col: anchor, row: Number.isInteger(read[6]) ? read[6] : -1 };
        this.readSelectionChanged();
        this.draw();  // show the click immediately; a drag replaces it with a range
        this.drag((ev) => {
          if (!moved && Math.abs(ev.clientX - start.x) + Math.abs(ev.clientY - start.y) > 3) moved = true;
          if (!moved) return;
          const col = clamp(this.columnAt(this.point(ev).x));
          this.readSel = { read, c0: Math.min(anchor, col), c1: Math.max(anchor, col) };
          this.readCaret = null;
          this.draw();
        }, () => {
          if (!moved) return;  // the caret was already shown on mouse down
          this.readSelectionChanged();
          this.draw();
        });
        return;
      }
      if (this.readCaret || this.readSel) {
        this.readCaret = null;  // clicking anywhere else lets it go
        this.readSel = null;
        this.readSelectionChanged();
        this.draw();
      }
      const lastRow = this.rows.length - 1;
      let area;
      if (y > this.height - OVERVIEW) area = "overview";
      else if (this.circularMap) area = "circle";
      else if (y >= this.rowsTop && y < this.rowsTop + this.rowsHeight) area = x < this.namesEdge() ? "names" : "rows";
      else if (this.aligned && y >= RULER && y < RULER + ROW) area = x < this.nameWidth ? "consensus name" : "consensus";
      else if (this.single && this.mapping && !this.mapping.coverageOnly && y >= this.mappingTop("consensus") &&
               y < this.mappingTop("consensus") + ROW) area = x < this.nameWidth ? "consensus name" : "consensus";
      else if (y < this.rowsTop && x >= this.nameWidth) area = "columns";
      else return;

      if (area !== "overview" && this.circularMap) return this.circMouseDown(e);
      if (area === "overview") {
        const at = (ev) => Math.max(0, Math.min(this.length - 1, ((this.point(ev).x - this.nameWidth) / this.seqWidth) * this.length));
        const jump = (ev, animate = true) => {
          if (this.circularMap) this.rotateTo(at(ev), animate);
          else if (animate) this.centerOn(at(ev));
          else { this.centerOn(at(ev), false); this.draw(); }
        };
        jump(e);
        this.drag(ev => jump(ev, false));
        return;
      }
      // Shift extends the selection from where it started
      const s = this.selection;
      if (area === "names" && (e.ctrlKey || e.metaKey) && s && !s.consensus && !this.single) {
        const r = this.rowAt(y);  // Ctrl+click on a name: that row in or out of the selection
        if (r < 0) return;
        const rows = new Set(this.selectedRows(s));
        rows.has(r) ? rows.delete(r) : rows.add(r);
        if (!rows.size) return this.clearSelection();
        return this.select(0, this.length - 1, Math.min(...rows), Math.max(...rows), false, false, [...rows], null, true);
      }
      const anchor = e.shiftKey && s ? { col: s.c0, row: s.r0 } : { col: this.columnAt(x), row: this.rowAt(y) };
      const mappingConsensus = this.single && this.mapping && area === "consensus";
      const update = (ev, moved) => {
        const p = this.point(ev);
        const col = this.columnAt(p.x);
        const r = this.rowAt(p.y);
        const row = r >= 0 ? r : p.y < this.rowsTop ? 0 : lastRow;
        if (area === "columns") this.select(anchor.col, col, 0, lastRow);
        else if (area === "consensus") this.select(anchor.col, col, 0, lastRow, false, true);
        else if (area === "consensus name") this.select(0, this.length - 1, 0, lastRow, false, true);
        else if (area === "names") this.select(0, this.length - 1, anchor.row < 0 ? row : anchor.row, row, false, false, null, null, true);
        else if (moved || e.shiftKey) this.select(anchor.col, col, anchor.row < 0 ? row : anchor.row, row);
        this.autoScroll(p);
      };
      const start = { x: e.clientX, y: e.clientY };
      let moved = false;
      if ((area !== "rows" && !mappingConsensus) || e.shiftKey) update(e, false);
      this.drag((ev) => {
        moved = moved || Math.abs(ev.clientX - start.x) + Math.abs(ev.clientY - start.y) > 3;
        if (moved || (area !== "rows" && !mappingConsensus)) update(ev, moved);
      }, () => {
        if (mappingConsensus && !moved && !e.shiftKey) return this.setCaret(anchor.col, 0);
        if (moved || area !== "rows" || e.shiftKey) return;
        // A plain click: on a feature selects it; on a single sequence's bases
        // places the cursor; elsewhere clears
        const r = this.rowAt(y);
        const inRow = r >= 0 ? y - this.rowsTop + this.scrollY - this.rows[r].top : -1;
        const hit = this.blockAt(x, y);
        if (hit) {  // a comparison's block: highlighted in all genomes, and (app.js) the genomes lined up on it
          this.compare.active = hit.block.index;
          if (this.options.onBlockClick) return this.options.onBlockClick(hit.block.index, hit.row);
          return this.select(this.blockColumn(hit.row, hit.part.start), this.blockColumn(hit.row, hit.part.end), hit.row, hit.row);
        }
        const feature = r >= 0 && this.featureAt(this.rows[r], this.columnAt(x), inRow);
        if (feature) this.selectFeature(feature, r);
        else if (inRow >= 0 && inRow < this.rows[r].lanesTop) {
          this.setCaret(Math.round((x - this.nameWidth + this.scrollX) / this.charWidth), r);
        } else this.clearSelection();
      }, true);
    }

    // tip: the selection's length by the mouse while dragging
    drag(onMove, onUp, tip = false) {
      const move = (ev) => {
        onMove(ev);
        if (tip && this.selection) this.showDragTip(ev);
      };
      const up = () => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
        if (this.tip) this.tip.hidden = true;
        if (onUp) onUp();
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    }

    // Selecting past the edge of the sequences moves the view along
    autoScroll(p) {
      if (p.x > this.width - 20) this.scrollX += 20;
      else if (p.x < this.nameWidth + 10 && p.x >= this.nameWidth - 40) this.scrollX -= 20;
      this.clampScroll();
    }

    onDoubleClick(e) {
      const { x, y } = this.point(e);
      if (this.readLane && y >= this.readsTop && y < this.readsTop + this.readLane && x >= this.nameWidth) {
        const read = this.readAt(x, y);
        if (read) {  // a double click: the whole read selected
          this.readCaret = null;
          this.readSel = { read, c0: read[0], c1: read[1] };
          this.selection = null;
          this.caret = null;
          this.readSelectionChanged();
          this.draw();
        }
        return;
      }
      const feature = this.featureUnder(x, y);
      if (feature && this.options.onFeatureDoubleClick) {
        return this.options.onFeatureDoubleClick(feature, this.circularMap ? 0 : this.rowAt(y));
      }
      if (x >= this.namesEdge() || y < this.rowsTop || this.single) return;
      const r = this.rowAt(y);
      if (r >= 0 && this.options.onOpenRow) this.options.onOpenRow(this.rows[r].name);
    }

    // Tooltip and status line: the sequence, column, its own position and base
    onHover(e) {
      if (this.circularMap) return this.circHover(e);
      const { x, y } = this.point(e);
      const gcTrack = this.gcTrackAt?.(y);
      if (y >= RULER && y < RULER + (this.gcHeight?.() || 0) && gcTrack) {
        this.hoverTrack = true;
        this.setHoverFeature(null);
        this.hover = x >= this.nameWidth ? this.gcTip(gcTrack, this.columnAt(x)) :
          this.gcSummary(gcTrack);
        this.drawStatus(); return;
      }
      const col = this.columnAt(x);
      const r = this.rowAt(y);
      this.updateNamesOpen(x, y);
      const edge = this.namesEdge();
      let tip = "", feature = null;
      const wasHoverTrack = this.hoverTrack;
      this.hoverTrack = false;
      if (this.single && this.mapping && y >= RULER && y < this.rowsTop) {
        // the mapping's tracks, whatever the names column is doing: their numbers
        const track = this.mappingTrack(y);
        this.hoverTrack = true;
        if (x >= this.nameWidth && col >= 0 && col < this.length) tip = this.mappingTrackTip(track, col);
        else if (x < this.nameWidth) tip = this.mappingTrackSummary(track);
      } else if (x >= edge && col >= 0 && col < this.length) {
        if (y >= this.rowsTop && y < this.rowsTop + this.rowsHeight && r >= 0) {
          const row = this.rows[r];
          const ch = row.seq[col] || "-";
          const pos = ch !== "-" ? row.basesUpTo[col] : null;
          tip = this.single ? `Position ${pos}: ${ch}` : `${row.name} · ${this.aligned ? `column ${col + 1}: ` : ""}${ch}${pos ? ` (position ${pos})` : ""}`;
          if (this.aligned) tip += ` · consensus ${this.consensus[col]}`;
          feature = this.featureAt(row, col, y - this.rowsTop + this.scrollY - row.top);
          if (feature) tip += ` · ${feature.type} ${feature.name}${this.stopTip(row, feature)}`;
          const hit = this.blockAt(x, y);
          if (hit) tip = `${row.name} · ${this.blockTip(hit)}`;
        } else if (this.aligned && y >= RULER && y < this.rowsTop) {
          tip = `Column ${col + 1}: consensus ${this.consensus[col]}, identity ${Math.round(this.identity[col] * 100)} %`;
          if (this.variantHeight && y >= RULER + ROW && y < RULER + ROW + this.variantHeight) {
            const site = this.variantAt.get(col);
            tip = site ? `${site.type} at ${site.position}: ${site.ref} → ${site.alt} · ${site.alternateCount} alternate sample(s)` : "Variant sites";
          }
        } else if (this.readLane && y >= this.readsTop && y < this.readsTop + this.readLane) {
          const read = this.readAt(x, y);
          tip = read ? this.readTip(read) : "";
        } else if (this.single && this.mapping && y >= RULER && y < this.rowsTop) {
          tip = this.mappingTrackTip(this.mappingTrack(y), col);
        } else if (this.single && this.trace && y >= RULER && y < this.rowsTop) {
          tip = this.traceTip(col, y);
        }
      } else if (x < edge && r >= 0 && y >= this.rowsTop) {
        tip = this.rowInfo(this.rows[r]) + (this.single ? "" : " · double-click to open");
      }
      this.canvas.style.cursor = this.onReadScrollbar(x, y) ? "ns-resize"
        : y > RULER && this.onNameEdge(x) ? "col-resize"
        : this.readAt(x, y) ? "pointer" : "";
      this.setHoverFeature(feature);
      if (tip !== this.hover || wasHoverTrack !== this.hoverTrack) {
        this.hover = tip;
        this.drawStatus();
      }
    }

    // Which of the mapping's header tracks a y is in: coverage, agreement, clips
    // (when drawn) or the consensus row
    mappingTrack(y) {
      const m = this.mapping;
      if (!m) return "consensus";
      if (y < this.mappingTop("agreement")) return "coverage";
      if (m.agreement && y < this.mappingTop("clips")) return "agreement";
      if (m.clipsAt && y < this.mappingTop("consensus")) return "clips";
      return "consensus";
    }

    // The numbers of one track at a position (the hover text): depth, agreement
    // (with the base counts) or clipped reads; the consensus row keeps mappingTip
    mappingTrackTip(track, pos) {
      const m = this.mapping;
      const depth = m.depth[pos] || 0;
      const where = `Position ${pos + 1}`;
      if (track === "coverage") {
        const low = m.minDepth && depth < m.minDepth ? ` · below the minimum ${m.minDepth}×` : "";
        const ext = m.extension || {};
        const extended = pos < (ext.left || 0) || pos >= this.length - (ext.right || 0) ? " · extended from the reads" : "";
        return `${where} · coverage ${depth.toLocaleString()}×${low}${extended}`;
      }
      if (track === "agreement") {
        const v = m.variableAt?.get(pos);
        const counts = v ? ` (${Object.entries(v.counts).filter(([, n]) => n).map(([b, n]) => `${b} ${n}`).join(", ")})` : "";
        return `${where} · agreement ${m.agreement[pos]} %${counts}`;
      }
      if (track === "clips") {
        const n = m.clipsAt?.[pos] || 0;
        return `${where} · ${n ? `${n} read${n === 1 ? "" : "s"} clipped here` : "no reads clipped here"}`;
      }
      return this.mappingTip(pos);
    }

    // What a track's name says (no single position under a name): its numbers
    // over the visible window
    mappingTrackSummary(track) {
      const m = this.mapping;
      const cw = this.charWidth;
      const first = Math.max(0, Math.floor(this.scrollX / cw));
      const last = Math.min(this.length, Math.ceil((this.scrollX + this.seqWidth) / cw));
      const n = Math.max(1, last - first);
      const span = `${(last - first).toLocaleString()} bp`;
      if (track === "coverage") {
        let sum = 0, max = 0;
        for (let c = first; c < last; c++) { const d = m.depth[c] || 0; sum += d; if (d > max) max = d; }
        return `Coverage · mean ${Math.round(sum / n)}×, max ${max.toLocaleString()}× in ${span}`;
      }
      if (track === "agreement") {
        let min = 100, max = 0, sum = 0;
        for (let c = first; c < last; c++) {
          const a = m.agreement[c] ?? 0;
          if (a < min) min = a;
          if (a > max) max = a;
          sum += a;
        }
        return `Agreement · ${min}–${max} % (mean ${Math.round(sum / n)} %) in ${span}`;
      }
      if (track === "clips") {
        let count = 0;
        for (let c = first; c < last; c++) count += m.clipsAt[c] || 0;
        return `Clipped · ${count.toLocaleString()} read${count === 1 ? "" : "s"} in ${span}`;
      }
      return "Consensus · hover the row for its base and the numbers";
    }

    mappingTip(pos) {
      const m = this.mapping, ref = this.rows[0].seq[pos], ch = m.consensus[pos];
      const what = ch === "*" ? "deleted in the reads" : ch === "N" ? "N (too few reads)"
        : ch === ref ? ch : `${ch} (reference ${ref})`;
      const inserted = m.insertions?.[pos] ? ` · then ${m.insertions[pos]} inserted` : "";
      const v = m.variableAt?.get(pos);
      const counts = v ? ` (${Object.entries(v.counts).filter(([, n]) => n).map(([b, n]) => `${b} ${n}`).join(", ")})` : "";
      const agree = m.agreement ? ` · agreement ${m.agreement[pos]} %${counts}` : "";
      const clipped = m.clipsAt?.[pos] ? ` · ${m.clipsAt[pos]} reads clipped here` : "";
      const ext = m.extension || {};
      const extended = pos < (ext.left || 0) || pos >= this.length - (ext.right || 0) ? " · extended from the reads" : "";
      return `Position ${pos + 1}: coverage ${(m.depth[pos] || 0).toLocaleString()}×${agree}${clipped} · consensus ${what}${inserted}${extended}`;
    }

    colorOf(ch) { return (this.protein ? AMINO_COLORS[ch] : BASE_COLORS[ch]) || OTHER_COLOR; }
    get unit() { return this.protein ? "aa" : "bp"; }

    // ── Names column: what it shows, and its width ──────────────────────
    // A row's label: the chosen fields (name, organism, description), joined by " | "
    rowLabel(row) {
      const label = this.labelFields.map((f) => row[f]).filter(Boolean).join(" | ");
      return label || row.name;
    }

    setLabels(fields) {
      this.labelFields = fields.length ? fields : ["name"];
      this.draw();
    }

    // Everything known about a row, for the status line
    rowInfo(row) {
      return [row.name, row.organism, row.description].filter(Boolean).join(" · ");
    }

    onNameEdge(x) { return !this.circularMap && !this.namesOpen && Math.abs(x - this.nameWidth) <= 4; }

    // The names column opened to the longest label (hovering it, when labels are cut)
    namesFullWidth() {
      const key = this.labelFields.join();
      if (this._full?.rows !== this.rows || this._full.key !== key) {
        this.ctx.font = NAME_FONT;
        const widest = Math.max(0, ...this.rows.map((r) => this.ctx.measureText(this.rowLabel(r)).width));
        this._full = { rows: this.rows, key, widest };
      }
      return Math.round(Math.min(this.width - 80, Math.max(this.nameWidth, this._full.widest + 24)));
    }

    namesEdge() { return this.namesOpen ? this.namesFullWidth() : this.nameWidth; }

    // Open after the mouse rests over the names for NAMES_DELAY ms; not in the last
    // NAMES_MARGIN px before the edge (that strip is for reaching the edge, to resize)
    updateNamesOpen(x, y) {
      const inRows = !this.circularMap && y >= this.rowsTop && y < this.rowsTop + this.rowsHeight;
      const open = inRows && this.namesFullWidth() > this.nameWidth &&
        (this.namesOpen ? x < this.namesFullWidth() : x < this.nameWidth - NAMES_MARGIN);
      if (!open) {
        clearTimeout(this.namesTimer);
        this.namesTimer = null;
        if (this.namesOpen) { this.namesOpen = false; this.draw(); }
      } else if (!this.namesOpen && !this.namesTimer) {
        this.namesTimer = setTimeout(() => {
          this.namesTimer = null;
          this.namesOpen = true;
          this.draw();
        }, NAMES_DELAY);
      }
    }

    // Dragging the edge of the names column: its width (remembered by the app)
    dragNameEdge() {
      this.drag((ev) => {
        const x = this.point(ev).x;
        this.nameWidth = Math.round(Math.max(60, Math.min(this.width - 200, x)));
        this.clampScroll();
        this.draw();
      }, () => this.options.onNameWidth?.(this.nameWidth));
    }

    setHoverFeature(feature) {
      if (feature === (this.hoverFeature || null)) return;
      this.hoverFeature = feature;
      this.draw();
    }

    featureUnder(x, y) {
      if (this.circularMap) {
        const g = this.circGeometry(), { pos, r } = this.circPosition(x, y, g);
        return this.circFeatureAt(g, pos, r);
      }
      const r = this.rowAt(y);
      if (r < 0 || x < this.namesEdge()) return null;  // the names (also while open over the rows)
      return this.featureAt(this.rows[r], this.columnAt(x), y - this.rowsTop + this.scrollY - this.rows[r].top);
    }

    featureAt(row, col, yInRow) {
      if (!this.show.features || !row.features || yInRow < row.lanesTop) return null;
      const lane = row.laneTops.findLastIndex((t) => t <= yInRow - row.lanesTop);
      return row.features.find((f) => f.lane === lane && col >= f.c0 && col <= f.c1) || null;
    }

    point(e) {
      const r = this.canvas.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    }

    // ── Drawing ────────────────────────────────────────────────────────────
    draw() {
      if (!this.width) return;
      if (this.layoutKey() !== this.laidOutKey) this.layout();  // zoomed past a threshold (however it was set)
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.width, this.height);
      ctx.fillStyle = "white";
      ctx.fillRect(0, 0, this.width, this.height);
      if (!this.length) return;
      if (this.circularMap) {  // seqview-circular.js
        this.namesCtx.clearRect(0, 0, this.width, this.height);
        this.drawCircular();
        this.drawStatus();
        this.drawOverview();
        return;
      }
      this.clampScroll();
      const cw = this.charWidth;
      const first = Math.max(0, Math.floor(this.scrollX / cw));
      const last = Math.min(this.length, Math.ceil((this.scrollX + this.seqWidth) / cw));
      const xOf = (col) => this.nameWidth + col * cw - this.scrollX;
      const letters = this.letters;
      if (this.onViewChange && (!this.viewSent || this.viewSent[0] !== first || this.viewSent[1] !== last)) {
        this.viewSent = [first, last];
        this.onViewChange(first, last);  // app.js: the reads of this window
      }

      this.drawGC?.(first, last, xOf);
      // Sequence area, clipped to the right of the names
      ctx.save();
      ctx.beginPath();
      ctx.rect(this.nameWidth, 0, this.seqWidth, this.rowsTop + this.rowsHeight);
      ctx.clip();
      this.drawRuler(first, last, xOf);
      if (this.aligned) {
        this.drawConsensus(first, last, xOf, letters);
        if (this.variantHeight) this.drawVariantTrack(first, last, xOf);
        this.drawIdentity(first, last, xOf);
      }
      if (this.single && this.mapping) this.drawMapping(first, last, xOf, letters);
      if (this.single && this.trace) this.drawTrace(first, last, xOf);
      this.drawColumnSelection(xOf);

      ctx.beginPath();
      ctx.rect(this.nameWidth, this.rowsTop, this.seqWidth, this.rowsHeight);
      ctx.clip();
      this.forVisibleRows((row, y) => {
        this.drawRow(row, y, first, last, xOf, letters);
        if (row.traceTop !== null && row.traceTop !== undefined) this.drawRowTrace(row, y + row.traceTop, first, last, xOf);
        if (this.show.features && row.lanes) this.drawFeatures(row, y + row.lanesTop, first, last, xOf);
      });
      if (this.compare) this.drawComparison(xOf);
      this.drawMatches(first, last, xOf);
      this.drawSelection(xOf);
      this.drawCaret(xOf);
      ctx.restore();

      if (this.single && this.mapping && this.readLane) {  // under the sequence
        ctx.save();
        ctx.beginPath();
        ctx.rect(this.nameWidth, this.readsTop, this.seqWidth, this.readLane);
        ctx.clip();
        this.drawReads(first, last, xOf);
        ctx.restore();
      }

      this.drawNames();
      this.drawScrollbar();
      this.drawStatus();
      this.drawOverview();
    }

    forVisibleRows(callback) {
      const from = this.scrollY, to = this.scrollY + this.rowsHeight;
      this.rows.forEach((row, i) => {
        if (row.top + row.height > from && row.top < to) callback(row, this.rowsTop + row.top - this.scrollY, i);
      });
    }

    drawRuler(first, last, xOf) {
      const ctx = this.ctx;
      const steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000];
      const step = steps.find((s) => s * this.charWidth >= 70) || 200000;
      ctx.fillStyle = "#555";
      ctx.strokeStyle = "#999";
      ctx.font = "11px 'Segoe UI', sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "alphabetic";
      ctx.beginPath();
      ctx.moveTo(this.nameWidth, RULER - 0.5);
      ctx.lineTo(this.width, RULER - 0.5);
      for (let pos = Math.ceil((first + 1) / step) * step; pos <= last; pos += step) {
        const x = xOf(pos - 1) + this.charWidth / 2;  // ticks label positions (alignment columns), 1-based
        ctx.moveTo(Math.round(x) + 0.5, RULER - 6);
        ctx.lineTo(Math.round(x) + 0.5, RULER);
        ctx.fillText(pos.toLocaleString(), x, RULER - 8);
      }
      ctx.stroke();
    }

    // Columns drawn one by one when wide enough, else one pixel at a time
    // (each pixel showing the first column under it)
    eachColumn(first, last, xOf, callback) {
      const cw = this.charWidth;
      if (cw >= 1) {
        for (let col = first; col < last; col++) callback(col, xOf(col), cw);
      } else {
        for (let px = 0; px < this.seqWidth; px++) {
          const col = Math.floor((this.scrollX + px) / cw);
          if (col < 0) continue;  // the margin before the first base
          if (col >= this.length) break;
          callback(col, this.nameWidth + px, 1);
        }
      }
    }

    drawConsensus(first, last, xOf, letters) {
      const ctx = this.ctx;
      const y = RULER + 1;
      this.eachColumn(first, last, xOf, (col, x, w) => {
        const ch = this.consensus[col];
        if (ch === "-" || ch === " ") return;  // a gap, or no sequence there
        ctx.fillStyle = this.colorOf(ch);
        this.colRect(x, y, Math.max(1, w), ROW - 2);
      });
      if (letters) this.drawLetters(first, last, xOf, y + (ROW - 2) / 2, (col) => this.consensus[col], () => "#222");
    }

    drawVariantTrack(first, last, xOf) {
      const top = RULER + ROW, ctx = this.ctx;
      ctx.fillStyle = "#fbf4ee";
      ctx.fillRect(this.nameWidth, top, this.seqWidth, VARIANT);
      const columns = this.variantColumns;
      for (let i = lowerBound(columns, first); i < columns.length && columns[i] < last; i++) {
        const x = xOf(columns[i]);
        ctx.fillStyle = this.variantAt.get(columns[i])?.type?.includes("indel") || this.variantAt.get(columns[i])?.type === "Indel" ? "#8053bb" : "#d66a29";
        this.colRect(x, top + 1, Math.max(2, this.charWidth), VARIANT - 2);
      }
    }

    drawIdentity(first, last, xOf) {
      const top = RULER + ROW + this.variantHeight;
      const cw = this.charWidth;
      if (cw >= 1) {
        for (let col = first; col < last; col++) this.identityBar(xOf(col), cw, this.identity[col], top);
      } else {  // zoomed out: one summary bar per pixel
        const perPixel = 1 / cw;
        for (let px = 0; px < this.seqWidth; px++) {
          const from = Math.floor((this.scrollX + px) * perPixel);
          if (from < 0) continue;  // the margin before the first base
          if (from >= this.length) break;
          const to = Math.max(from + 1, Math.min(this.length, Math.floor((this.scrollX + px + 1) * perPixel)));
          this.summaryBar(this.nameWidth + px, top, IDENTITY, from, to);
        }
      }
    }

    // Several columns in one pixel: a green bar with a cap as high as the share
    // of columns that vary (red if the columns are on average under 30 % identical)
    summaryBar(x, top, height, from, to) {
      let sum = 0, varying = 0;
      for (let col = from; col < to; col++) {
        sum += this.identity[col];
        if (this.identity[col] < 1) varying++;
      }
      const ctx = this.ctx;
      ctx.fillStyle = IDENTITY_COLORS.full;
      this.colRect(x, top + 2, 1, height - 2);
      if (!varying) return;
      ctx.fillStyle = sum / (to - from) < 0.3 ? IDENTITY_COLORS.low : IDENTITY_COLORS.part;
      this.colRect(x, top + 2, 1, Math.max(2, Math.round((varying / (to - from)) * (height - 2))));
    }

    // A rectangle as wide as some columns, its left and right edges on whole screen
    // pixels, so side-by-side columns meet without a light half-painted seam (uneven
    // white bands, worst on displays scaled to 125 %, where 1 px is 1.25 screen px)
    colRect(x, y, w, h) {
      const d = this.dpr || 1;
      const x0 = Math.round(x * d) / d, x1 = Math.round((x + w) * d) / d;
      this.ctx.fillRect(x0, y, Math.max(1 / d, x1 - x0), h);
    }

    identityBar(x, w, identity, top) {
      const ctx = this.ctx;
      ctx.fillStyle = identity >= 1 ? IDENTITY_COLORS.full : identity >= 0.3 ? IDENTITY_COLORS.part : IDENTITY_COLORS.low;
      const h = Math.max(2, Math.round(identity * (IDENTITY - 2)));
      this.colRect(x, top + IDENTITY - h, Math.max(1, w), h);
    }

    drawRow(row, y, first, last, xOf, letters) {
      const consensusLook = this.aligned || (this.single && this.mapping);
      this.drawSequenceRow({
        base: (col) => row.seq[col],
        first: row.first,
        last: row.last,
        segments: row.segments,
        mismatches: consensusLook ? new Set(this.mismatchesIn(row, first, last)) : new Set(),
        line: consensusLook,
      }, y, ROW, first, last, xOf, letters);
    }

    // One row of bases, as the alignment draws it (a read uses it too): grey
    // letters where they match the consensus, coloured where they differ; zoomed
    // out, a grey bar with coloured ticks. band: {base(col), first, last,
    // segments, mismatches, line}. height: the row's (the alignment's ROW; a read's)
    drawSequenceRow(band, y, height, first, last, xOf, letters) {
      const ctx = this.ctx;
      const cw = this.charWidth;
      const base = band.base;
      const blank = (col) => col < band.first || col > band.last;  // terminal gaps
      const consensusLook = this.aligned || (this.single && this.mapping);  // a mapping as an alignment
      if (letters && consensusLook) {
        // Bases that differ get their colour; the rest are grey letters
        for (const col of band.mismatches) {
          const ch = base(col);
          if (!ch || ch === "-") continue;
          ctx.fillStyle = this.colorOf(ch);
          this.colRect(xOf(col), y + 1, cw, height - 2);
        }
        this.drawLetters(first, last, xOf, y + height / 2, (col) => (blank(col) ? "" : base(col) || "-"),
                         (col) => (band.mismatches.has(col) ? "#111" : base(col) === "-" ? "#bbb" : "#888"));
      } else if (letters) {
        // Not aligned: every base in its colour, as in Geneious's sequence view
        for (let col = first; col < last; col++) {
          const ch = base(col);
          if (!ch || ch === "-") continue;
          ctx.fillStyle = this.colorOf(ch);
          this.colRect(xOf(col), y + 1, cw, height - 2);
        }
        this.drawLetters(first, last, xOf, y + height / 2, (col) => base(col) || "", () => "#111");
        if (this.single && !this.mapping && this.show.complement && !this.protein) {  // the other strand, in grey
          this.drawLetters(first, last, xOf, y + height + height / 2, (col) => COMPLEMENT[base(col)] || "", () => "#999");
        }
      } else {
        // Zoomed out: a grey bar where there are bases, coloured ticks where they
        // differ; gaps a thin line between the bars, missing ends nothing, as in Geneious
        if (band.line && band.last >= band.first) {
          ctx.fillStyle = "#9a9a9a";
          this.colRect(xOf(band.first), y + height / 2 - 0.5, (band.last - band.first + 1) * cw, 1);
        }
        const segment = Math.max(1, Math.min(6, height - 2));
        ctx.fillStyle = "#c8c8c8";
        for (const [start, end] of band.segments) {
          if (end <= first || start >= last) continue;
          this.colRect(xOf(start), y + (height - segment) / 2, Math.max(1, (end - start) * cw), segment);
        }
        // Less than a pixel per column: half transparent, or they hide the row
        ctx.globalAlpha = cw < 1 ? 0.5 : 1;
        const tick = Math.max(1, height - 2);
        for (const col of band.mismatches) {
          ctx.fillStyle = this.colorOf(base(col));
          ctx.fillRect(Math.floor(xOf(col)), y + (height - tick) / 2, Math.max(1, cw), tick);
        }
        ctx.globalAlpha = 1;
      }
    }

    // A CDS's amino acids in its track, one box per codon
    drawCodons(row, f, y, first, last, xOf) {
      const cw = this.charWidth;
      const ctx = this.ctx;
      ctx.font = SMALL_FONT;
      const aaColor = this.options.aaColor || (() => "#ddd");
      for (const codon of this.codonsOf(row, f)) {
        if (codon.c1 < first || codon.c0 >= last) continue;
        // a split codon: each piece drawn, the letter in the larger one
        const pieces = codon.pieces || [[codon.c0, codon.c1]];
        const widest = pieces.reduce((a, b) => (b[1] - b[0] > a[1] - a[0] ? b : a));
        for (const [c0, c1] of pieces) {
          const x = xOf(c0), w = (c1 - c0 + 1) * cw;
          ctx.fillStyle = aaColor(codon.aa);
          ctx.fillRect(x + 0.5, y + 1, w - 1, TRANS - 2);
          if (w >= 8 && c0 === widest[0]) {
            ctx.fillStyle = "#111";
            ctx.fillText(codon.aa, x + w / 2, y + TRANS / 2);
          }
        }
      }
    }

    // Features under a row, in lanes, in the editor's colours: arrows pointing
    // to the 3' end, a thin line across introns, the name inside
    drawFeatures(row, y, first, last, xOf) {
      const ctx = this.ctx;
      const cw = this.charWidth;
      const laneHeight = this.laneHeight;
      const h = laneHeight - 4;
      const r = this.rows.indexOf(row), s = this.selection;
      const tracks = this.tracksOn();
      ctx.textBaseline = "middle";
      ctx.textAlign = "center";
      for (const f of row.features) {
        if (f.c1 < first || f.c0 >= last) continue;
        const top = y + 2 + row.laneTops[f.lane];
        if (tracks && f.type === "CDS") this.drawCodons(row, f, top + h + 1, first, last, xOf);
        ctx.font = this.aligned ? SMALL_FONT : LABEL_FONT;
        const selected = s && s.c0 === f.c0 && s.c1 === f.c1 && r >= s.r0 && r <= s.r1 && (!s.rows || s.rows.includes(r));
        const style = featureStyle(f, f === this.hoverFeature, selected);
        ctx.fillStyle = style.edge;
        for (const [a, b] of introns(f, row.bases)) ctx.fillRect(xOf(a.c1 + 1), top + Math.floor(h / 2), (b.c0 - a.c1 - 1) * cw, 1);
        const head = arrowPart(f);
        for (const p of f.parts) {
          const x = xOf(Math.min(p.c0, p.c1));
          const w = Math.max(1, (Math.abs(p.c1 - p.c0) + 1) * cw);
          if (x > this.width || x + w < this.nameWidth) continue;
          ctx.fillStyle = style.fill;
          if (w < 4) { ctx.fillRect(x, top, w, h); continue; }
          this.arrowShape(x, w, top, h, p === head ? f.strand : 0);
          ctx.fill();
          ctx.strokeStyle = style.edge;
          ctx.lineWidth = style.edgeWidth;
          ctx.stroke();
        }
        ctx.lineWidth = 1;
        // after an internal stop: lighter to the 3' end, a dark red mark at the stop
        const dim = this.afterStop(row, f);
        if (dim) {
          const x0 = xOf(dim.c0), x1 = xOf(dim.c1) + cw;
          ctx.save();
          ctx.beginPath();
          ctx.rect(x0, top - 1, x1 - x0, h + 2);
          ctx.clip();
          ctx.fillStyle = "rgba(255, 255, 255, 0.62)";
          for (const p of f.parts) {
            const x = xOf(Math.min(p.c0, p.c1));
            const w = Math.max(1, (Math.abs(p.c1 - p.c0) + 1) * cw);
            if (w < 4) { ctx.fillRect(x, top, w, h); continue; }
            this.arrowShape(x, w, top, h, p === head ? f.strand : 0);
            ctx.fill();
          }
          ctx.restore();
          // the stop's mark: on each piece of a codon split by an intron, the * on the larger
          const pieces = dim.stop.pieces || [[dim.stop.c0, dim.stop.c1]];
          const widest = pieces.reduce((a, b) => (b[1] - b[0] > a[1] - a[0] ? b : a));
          for (const [c0, c1] of pieces) {
            const sx = xOf(c0), sw = Math.max(2, (c1 - c0 + 1) * cw);
            ctx.fillStyle = STOP;
            ctx.fillRect(sx, top - 2, sw, h + 4);
            if (sw >= 9 && c0 === widest[0]) {
              ctx.fillStyle = "#fff";
              ctx.fillText("*", sx + sw / 2, top + h / 2 + 2);
            }
          }
        }
        // the name inside its widest part in view, shortened if need be
        const pad = Math.min(h * 0.6, 8) + 4;
        let left = 0, right = 0;
        for (const p of f.parts) {
          const l = Math.max(xOf(p.c0), this.nameWidth), rr = Math.min(xOf(p.c1) + cw, this.width);
          if (rr - l > right - left) [left, right] = [l, rr];
        }
        if (right - left < 20) continue;
        const name = fitText(ctx, f.name, right - left - 2 * pad);
        if (name) {
          ctx.fillStyle = style.ink;
          ctx.fillText(name, (left + right) / 2, top + h / 2 + 0.5);
        }
      }
    }

    // A bar from x, w px wide, pointed at the right (point 1) or left (-1) end
    arrowShape(x, w, top, h, point) {
      const ctx = this.ctx;
      const head = point ? Math.min(h * 0.6, w * 0.6, 8) : 0;
      const x0 = x + 0.5, x1 = x + w - 0.5, y0 = top + 0.5, y1 = top + h - 0.5, mid = top + h / 2;
      ctx.beginPath();
      if (point === -1) { ctx.moveTo(x0, mid); ctx.lineTo(x0 + head, y0); } else ctx.moveTo(x0, y0);
      if (point === 1) { ctx.lineTo(x1 - head, y0); ctx.lineTo(x1, mid); ctx.lineTo(x1 - head, y1); }
      else { ctx.lineTo(x1, y0); ctx.lineTo(x1, y1); }
      if (point === -1) ctx.lineTo(x0 + head, y1); else ctx.lineTo(x0, y1);
      ctx.closePath();
    }

    drawMatches(first, last, xOf) {
      if (!this.matches.length) return;
      const ctx = this.ctx;
      this.matches.forEach((m, i) => {
        if (m.c1 < first || m.c0 >= last) return;
        const row = this.rows[m.row];
        const y = this.rowsTop + row.top - this.scrollY;
        ctx.strokeStyle = MATCH;
        ctx.lineWidth = i === this.matchIndex ? 2.5 : 1.2;
        ctx.strokeRect(xOf(m.c0) + 0.5, y + 0.5, (m.c1 - m.c0 + 1) * this.charWidth - 1, ROW - 1);
      });
      ctx.lineWidth = 1;
    }

    drawSelection(xOf) {
      const s = this.selection;
      if (!s || s.consensus) return;  // the consensus: drawColumnSelection
      const ctx = this.ctx;
      // one band for a block of rows, one per row for rows picked apart
      const bands = s.rows ? s.rows.map((r) => [r, r]) : [[s.r0, s.r1]];
      for (const [r0, r1] of bands) {
        const top = this.rowsTop + this.rows[r0].top - this.scrollY;
        const bottom = this.rowsTop + this.rows[r1].top + this.rows[r1].height - this.scrollY;
        for (const [c0, c1] of this.selectionPieces(s)) {
          const x = xOf(c0), w = Math.max(2, (c1 - c0 + 1) * this.charWidth);
          ctx.fillStyle = SELECTION;
          ctx.fillRect(x, top, w, bottom - top);
          ctx.strokeStyle = SELECTION_EDGE;
          ctx.strokeRect(x + 0.5, top + 0.5, w - 1, bottom - top - 1);
        }
      }
    }

    drawCaret(xOf) {
      if (this.caret === null) return;
      const ctx = this.ctx;
      const x = Math.round(xOf(this.caret)) + 0.5;
      const row = this.rows[this.caretRow || 0];
      const top = this.rowsTop + row.top - this.scrollY;
      ctx.strokeStyle = "#111";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, top - 2);
      ctx.lineTo(x, top + row.lanesTop + 2);
      ctx.stroke();
      ctx.lineWidth = 1;
    }

    // The selected columns marked on the ruler (and consensus) too
    drawColumnSelection(xOf) {
      const s = this.selection;
      if (!s) return;
      const ctx = this.ctx;
      ctx.fillStyle = SELECTION;
      for (const [c0, c1] of this.selectionPieces(s)) {
        const x = xOf(c0), w = Math.max(2, (c1 - c0 + 1) * this.charWidth);
        if (s.consensus) {  // the consensus row alone, framed like a selected block
          const top = this.aligned ? RULER : this.mappingTop("consensus");
          ctx.fillRect(x, top, w, ROW);
          ctx.strokeStyle = SELECTION_EDGE;
          ctx.lineWidth = 2;
          ctx.strokeRect(x + 1, top + 1, w - 2, ROW - 2);
          ctx.lineWidth = 1;
        } else if (this.single && this.mapping) {
          // the row's band is drawSelection's; a mark on the ruler only, so the
          // reads and the other rows don't look selected too
          ctx.fillRect(x, RULER - 6, w, 3);
        } else {
          ctx.fillRect(x, RULER - 6, w, this.headerHeight - RULER + 6 - GAP);
        }
      }
    }

    // A row's mismatching columns between first and last (binary search: sorted)
    mismatchesIn(row, first, last) {
      const list = row.mismatches;
      let lo = 0, hi = list.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (list[mid] < first) lo = mid + 1; else hi = mid; }
      const result = [];
      for (let i = lo; i < list.length && list[i] < last; i++) result.push(list[i]);
      return result;
    }

    drawLetters(first, last, xOf, yMiddle, letterOf, colorOf) {
      const ctx = this.ctx;
      ctx.font = FONT;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (let col = first; col < last; col++) {
        ctx.fillStyle = colorOf(col);
        ctx.fillText(letterOf(col), xOf(col) + this.charWidth / 2, yMiddle + 1);
      }
    }

    drawNames() {
      // Keep labels and the read caret on their own canvas. The sequence and
      // read painters use several clips; an accidental clip leak there must
      // never erase the names column or make the caret disappear.
      const ctx = this.namesCtx;
      ctx.clearRect(0, 0, this.width, this.height);
      ctx.save();
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      const bottom = this.single && this.mapping ? this.height - OVERVIEW - STATUS - 2 : this.rowsTop + this.rowsHeight;
      const w = this.namesEdge();  // wider while open (over the sequences), labels in full
      ctx.fillStyle = "#f7f7f9";
      ctx.fillRect(0, 0, this.nameWidth, bottom);
      if (this.namesOpen) {
        ctx.fillRect(0, this.rowsTop, w, this.rowsHeight);
        ctx.fillStyle = "rgba(0, 0, 0, 0.12)";
        ctx.fillRect(w, this.rowsTop, 4, this.rowsHeight);
      }
      ctx.fillStyle = "#e3e3e8";
      ctx.fillRect(this.nameWidth - 1, 0, 1, this.rowsTop);
      ctx.fillRect(w - 1, this.rowsTop, 1, this.rowsHeight);
      if (this.single && this.mapping) {  // the edge beside the reads lane too
        ctx.fillRect(w - 1, this.rowsTop + this.rowsHeight, 1, Math.max(0, bottom - (this.rowsTop + this.rowsHeight)));
      }
      ctx.font = NAME_FONT;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "#555";
      if (this.aligned) {
        ctx.fillText("Consensus", 8, RULER + ROW / 2);
        if (this.variantHeight) ctx.fillText("Variants", 8, RULER + ROW + VARIANT / 2);
        ctx.fillText("Identity", 8, RULER + ROW + this.variantHeight + IDENTITY / 2);
      }
      if (this.single && this.mapping) {
        ctx.fillText("Coverage", 8, RULER + 12);
        ctx.fillStyle = "#888";
        ctx.font = SMALL_FONT;
        ctx.fillText(`max ${this.mapping.maxDepth.toLocaleString()}×`, 8, RULER + 28);
        ctx.font = NAME_FONT;
        ctx.fillStyle = "#555";
        if (this.mapping.agreement) ctx.fillText("Agreement", 8, this.mappingTop("agreement") + AGREE / 2);
        if (this.mapping.clipsAt) ctx.fillText("Clipped", 8, this.mappingTop("clips") + CLIPS / 2);
        if (!this.mapping.coverageOnly) ctx.fillText("Consensus", 8, this.mappingTop("consensus") + ROW / 2);
        if (this.readLane) {
          const shown = this.readShown || 0;
          const seen = this.reads?.in_view || shown;
          const more = this.reads?.more ? "+" : "";
          const sourceSampled = this.reads?.all && this.reads.total > this.reads.kept;
          const readLabel = this.reads?.all
            ? `${shown.toLocaleString()} reads${sourceSampled ? " · source sampled" : ""}`
            : `Reads ${shown}/${seen}${more}`;
          const per = this.readRowsVisible();
          const from = Math.min(this.readOffset, Math.max(0, this.readRows.length - per));
          const w = this.namesOpen ? this.namesEdge() : this.nameWidth;
          ctx.fillText(fitText(ctx, readLabel, w - 14), 8, this.readsTop + 10);
          if (this.readRows.length > per) {  // the rows on a second line, like a row's label
            ctx.fillStyle = "#888";
            ctx.font = SMALL_FONT;
            ctx.fillText(fitText(ctx, rowsText(from, Math.min(from + per, this.readRows.length) - 1, this.readRows.length),
                                 w - 14), 8, this.readsTop + 24);
            ctx.font = NAME_FONT;
            ctx.fillStyle = "#555";
          }
        }
      }
      if (this.single && this.trace) {
        ctx.fillText("Trace", 8, RULER + 14);
        ctx.fillText("Quality", 8, RULER + TRACE + QUAL / 2);
      }
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, this.rowsTop, w - 4, this.rowsHeight);
      ctx.clip();
      const s = this.selection;
      this.forVisibleRows((row, y, i) => {
        if (s && !s.consensus && i >= s.r0 && i <= s.r1 && (!s.rows || s.rows.includes(i)) && !this.single) {
          ctx.fillStyle = "#dcebe5";
          ctx.fillRect(0, y, w - 1, row.height);
        }
        ctx.fillStyle = "#222";
        ctx.fillText(fitText(ctx, this.rowLabel(row), w - 14) || "…", 8, y + ROW / 2 + 1);
      });
      ctx.restore();
      this.drawMappingCaret(ctx);
      this.drawReadCaret(ctx);
      ctx.restore();
    }

    drawMappingCaret(ctx) {
      if (!this.single || !this.mapping || this.caret === null) return;
      const row = this.rows[this.caretRow || 0];
      if (!row) return;
      const first = Math.max(0, Math.floor(this.scrollX / this.charWidth));
      const last = Math.min(this.length, Math.ceil((this.scrollX + this.seqWidth) / this.charWidth));
      if (this.caret < first || this.caret > last) return;
      const x = Math.max(this.nameWidth + 5,
        Math.min(this.width - 5, Math.round(this.nameWidth + this.caret * this.charWidth - this.scrollX)));
      const y = this.rowsTop + row.top - this.scrollY - 2;
      this.drawCaretMark(ctx, x, y, row.lanesTop + 4);
    }

    drawReadCaret(ctx) {
      const caret = this.readCaret;
      if (!caret) return;
      const lane = caret.row;
      let ri = Number.isInteger(lane) && lane >= 0
        ? this.readRows.findIndex((row) => row.lane === lane)
        : -1;
      if (ri < 0) ri = this.readRows.findIndex((row) => row.reads.some((read) => this.sameRead(read, caret.read)));
      const per = this.readRowsVisible();
      const from = Math.min(this.readOffset, Math.max(0, this.readRows.length - per));
      if (ri < from || ri >= from + per) return;
      const first = Math.max(0, Math.floor(this.scrollX / this.charWidth));
      const last = Math.min(this.length, Math.ceil((this.scrollX + this.seqWidth) / this.charWidth));
      if (caret.col < first || caret.col >= last) return;
      const rh = this.readRowHeight();
      const y = this.readsTop + 4 + (ri - from) * rh;
      const x = Math.max(this.nameWidth + 5,
        Math.min(this.width - 5, Math.round(this.nameWidth + caret.col * this.charWidth - this.scrollX)));
      this.drawCaretMark(ctx, x, y, rh);
    }

    // The cursor in a mapping (sequence row or a read): the same thin dark line as a sequence's
    drawCaretMark(ctx, x, y, height) {
      ctx.fillStyle = "#111";
      ctx.fillRect(x - 1, y, 2, height);
    }

    // The selection (or what's under the mouse) in each sequence's own positions
    drawStatus() {
      const ctx = this.ctx;
      const top = this.height - OVERVIEW - STATUS - 2;
      ctx.fillStyle = "#f7f7f9";
      ctx.fillRect(0, top, this.width, STATUS);
      ctx.fillStyle = "#e3e3e8";
      ctx.fillRect(0, top, this.width, 1);
      ctx.font = NAME_FONT;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "#333";
      const hint = this.single ? "Drag to select · click a feature to select it" : "Drag to select · double-click a name to open it";
      const caret = this.caret === null || this.selection ? ""
        : this.aligned ? `Cursor in ${this.rows[this.caretRow].name} before column ${this.caret + 1}`
        : !this.single ? `Cursor in ${this.rows[this.caretRow].name} at ${this.caret}`
        : `Cursor at ${this.caret} (between ${this.caret} and ${this.caret + 1})`;
      const text = this.hoverTrack && this.hover ? this.hover
        : this.selection ? this.selectionText()
        : this.readSel ? this.readSelText()
        : this.hover || (this.readCaret ? this.readCaretText() : caret || hint);
      ctx.fillText(text, 8, top + STATUS / 2 + 1, this.width - 16);
    }

    // The whole length in one strip, with a box for the view: an alignment's
    // identity, else the features
    drawOverview() {
      const ctx = this.ctx;
      const top = this.height - OVERVIEW;
      const w = this.seqWidth;
      ctx.fillStyle = "#f7f7f9";
      ctx.fillRect(0, top - 2, this.width, OVERVIEW + 2);
      ctx.font = NAME_FONT;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "#555";
      // several sequences: the strip shows the rows in view (it can't fit them all)
      const inView = [];
      if (!this.aligned && !this.single) this.forVisibleRows((row, y, i) => inView.push(i));
      const label = inView.length && inView.length < this.rows.length
        ? rowsText(inView[0], inView[inView.length - 1], this.rows.length) : "Overview";
      ctx.fillText(label, 8, top + OVERVIEW / 2, this.nameWidth - 12);
      if (this.aligned) {
        if (!this.overview || this.overview.length !== w) {
          this.overview = new Array(w);  // the columns under each pixel
          for (let px = 0; px < w; px++) {
            const from = Math.min(this.length - 1, Math.floor((px / w) * this.length));
            const to = Math.min(this.length, Math.max(from + 1, Math.floor(((px + 1) / w) * this.length)));
            this.overview[px] = [from, to];
          }
        }
        for (let px = 0; px < w; px++) {
          const [from, to] = this.overview[px];
          this.summaryBar(this.nameWidth + px, top + 2, OVERVIEW - 6, from, to);
        }
      } else if (this.single && this.trace?.quality.length && this.traceFits()) {
        // a Sanger read: its quality along the strip (the lowest in each pixel), coloured as in its track
        const t = this.trace;
        for (let px = 0; px < w; px++) {
          const c0 = Math.floor((px / w) * this.length), c1 = Math.max(c0, Math.floor(((px + 1) / w) * this.length) - 1);
          let q = 99;
          for (let c = c0; c <= c1; c++) q = Math.min(q, t.quality[t.start + c] ?? 99);
          ctx.fillStyle = q >= 30 ? "#7d9cc4" : q >= 20 ? IDENTITY_COLORS.part : IDENTITY_COLORS.low;
          const bh = Math.max(1, Math.round((Math.min(60, q) / 60) * (OVERVIEW - 6)));
          ctx.fillRect(this.nameWidth + px, top + OVERVIEW - 3 - bh, 1, bh);
        }
      } else {
        // The sequence's features, or those of the rows in view, squeezed into the strip
        ctx.fillStyle = "#e4e4e8";
        ctx.fillRect(this.nameWidth, top + OVERVIEW / 2 - 1, w, 2);
        const rows = this.single ? this.rows : inView.slice(0, 12).map((i) => this.rows[i]);
        const h = Math.max(2, Math.floor((OVERVIEW - 6) / rows.length));
        if (this.compare) {  // a genome comparison: each genome in view with its shared blocks
          inView.slice(0, 12).forEach((r, i) => {
            for (const block of this.compare.blocks) {
              for (const p of block.parts) {
                if (p.row !== r) continue;
                const c0 = this.blockColumn(r, p.start), c1 = this.blockColumn(r, p.end);
                ctx.fillStyle = `hsl(${block.hue}, 60%, 60%)`;
                ctx.fillRect(this.nameWidth + (c0 / this.length) * w, top + 3 + i * h, Math.max(1, ((c1 - c0 + 1) / this.length) * w), h - 1);
              }
            }
          });
        } else rows.forEach((row, i) => {
          // Gene spans and joined-feature connectors are context, not foreground.
          // Paint them first so a nested CDS/RNA cannot be hidden by its parent.
          const priority = f => f.type.toLowerCase() === "gene" ? 0 : f.type.toLowerCase() === "intron" ? 1 : 2;
          const features = [...row.features].sort((a, b) => priority(a) - priority(b) || (b.c1 - b.c0) - (a.c1 - a.c0));
          for (const f of features) {
            ctx.fillStyle = f.color || "#bbb";
            for (const [c0, c1] of f.spans || [[f.c0, f.c1]])
              ctx.fillRect(this.nameWidth + (c0 / this.length) * w, top + 3 + i * h + (h - 2) / 2,
                Math.max(1, ((c1 - c0 + 1) / this.length) * w), 1);
          }
          for (const f of features) {
            ctx.fillStyle = f.color || "#bbb";
            for (const p of f.parts) {
              const x = this.nameWidth + (p.c0 / this.length) * w;
              ctx.fillRect(x, top + 3 + i * h, Math.max(1, ((p.c1 - p.c0 + 1) / this.length) * w), h - 1);
            }
          }
        });
      }
      const total = this.length * this.charWidth;
      ctx.fillStyle = "rgba(47, 111, 94, 0.35)";  // the selected columns
      for (const [c0, c1] of this.selectionPieces()) {
        ctx.fillRect(this.nameWidth + (c0 / this.length) * w, top, Math.max(2, ((c1 - c0 + 1) / this.length) * w), OVERVIEW);
      }
      // the box: what's in view; on the circle across the origin, both ends
      let boxes = [[this.scrollX / total, (this.scrollX + this.seqWidth) / total]];
      if (this.circularMap) {
        const range = this.visibleRange(this.circGeometry());
        const L = this.length;
        if (!range) boxes = [[0, 1]];
        else if (range.from < 0) boxes = [[0, range.to / L], [(range.from + L) / L, 1]];
        else if (range.to > L) boxes = [[range.from / L, 1], [0, (range.to - L) / L]];
        else boxes = [[range.from / L, range.to / L]];
      }
      ctx.strokeStyle = "#2f6f5e";
      ctx.lineWidth = 2;
      for (const [a, b] of boxes) {
        const x = this.nameWidth + Math.max(0, a) * w, bw = Math.max(3, (Math.min(1, b) - Math.max(0, a)) * w);
        ctx.strokeRect(x + 1, top + 1, Math.min(bw, w) - 2, OVERVIEW - 3);
      }
      ctx.lineWidth = 1;
    }
  }

  SeqView.looksProtein = looksProtein;
  SeqView.LABEL_FIELDS = LABEL_FIELDS;
  SeqView.STOP = STOP;
  SeqView.IDENTITY_COLORS = IDENTITY_COLORS;
  SeqView.looks = { shade, inkFor, fitText, featureStyle, arrowPart, introns };
  window.SeqView = SeqView;
})();
