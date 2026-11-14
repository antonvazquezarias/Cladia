// Cladia sequence editing: the open sequence and its features as data, with
// edits that keep the features in place, and undo/redo. The viewer (seqview.js)
// only shows it; app.js connects the two and saves.
//
// Features are in the editor's JSON form (as /api/sequences sends them):
// { id, name, type, strand, start, end, locations?: [{ start, end }], notes }
// with 0-based, inclusive positions. A feature's parts are its `locations`, or
// start..end when it has none.
//
//   const doc = new SeqDocument(data);   // data from /api/sequences
//   doc.insert(pos, "ACGT");             // before base `pos` (0..length)
//   doc.remove(start, end);              // bases start..end, inclusive
//   doc.undo(); doc.redo(); doc.dirty;

(function () {
  const VALID = /^[ACGTURYKMSWBDHVN]+$/;  // letters that can be typed or pasted
  const VALID_AMINO = /^[ACDEFGHIKLMNPQRSTVWYBZXJUO*]+$/;  // ... into a protein

  // Text to bases: letters only, upper case; null if anything else is in it
  function cleanBases(text, protein = false) {
    const bases = text.replace(/[\s\d>]/g, "").toUpperCase();
    return bases && (protein ? VALID_AMINO : VALID).test(bases) ? bases : null;
  }

  const partsOf = (f) => (f.locations && f.locations.length ? f.locations : [{ start: f.start, end: f.end }]);

  // A feature with new parts (start/end follow the first and last part, as the
  // editor's JSON has them; one part = no locations); null when no part is left
  function withParts(f, parts) {
    if (!parts.length) return null;
    const out = { ...f, start: parts[0].start, end: parts[parts.length - 1].end };
    if (parts.length > 1) out.locations = parts;
    else delete out.locations;
    return out;
  }

  // The two edits, on plain data (no undo here)
  function insertBases(state, pos, bases) {
    const k = bases.length;
    return {
      sequence: state.sequence.slice(0, pos) + bases + state.sequence.slice(pos),
      // inside a part it grows; a part after `pos` moves along
      features: state.features.map((f) => withParts(f, partsOf(f).map((p) => {
        if (p.start >= pos) return { start: p.start + k, end: p.end + k };
        if (p.end >= pos) return { start: p.start, end: p.end + k };
        return { ...p };
      }))),
    };
  }

  function removeBases(state, start, end) {
    const d = end - start + 1;
    return {
      sequence: state.sequence.slice(0, start) + state.sequence.slice(end + 1),
      // a part that overlaps gets shorter; one inside the deleted bases goes
      features: state.features.map((f) => withParts(f, partsOf(f).flatMap((p) => {
        if (p.end < start) return [{ ...p }];
        if (p.start > end) return [{ start: p.start - d, end: p.end - d }];
        const s = p.start < start ? p.start : start;
        const e = p.end > end ? p.end - d : start - 1;
        return e >= s ? [{ start: s, end: e }] : [];
      }))).filter(Boolean),
    };
  }

  // A new origin: base p (0-based) becomes the first. Features keep their bases: a
  // part across the new origin is split in two (a join), and the two halves of a
  // feature across the old origin become one part again.
  function rotateBases(state, p) {
    const L = state.sequence.length;
    if (!p) return state;
    const shift = (x) => (x - p + L) % L;
    const features = state.features.map((f) => {
      const pieces = [];  // [start, end, old start, old end]
      for (const part of partsOf(f)) {
        const s = shift(part.start), e = shift(part.end);
        if (s <= e) pieces.push([s, e, part.start, part.end]);
        else pieces.push([s, L - 1, part.start, part.end], [0, e, part.start, part.end]);
      }
      const parts = [];
      for (const [s, e, os, oe] of pieces) {
        const prev = parts[parts.length - 1];
        if (prev && prev.oldEnd === L - 1 && os === 0 && prev.end + 1 === s) {  // halves of the old origin
          prev.end = e;
          prev.oldEnd = oe;
        } else {
          parts.push({ start: s, end: e, oldEnd: oe });
        }
      }
      return withParts(f, parts.map(({ start, end }) => ({ start, end })));
    });
    return { ...state, sequence: state.sequence.slice(p) + state.sequence.slice(0, p), features };
  }

  // Reverse complement of bases a..b (inclusive; the whole sequence = 0..length-1).
  // Features inside it turn round with it (other strand, parts mirrored); features
  // only partly inside keep their place.
  const COMPLEMENT = { A: "T", T: "A", U: "A", G: "C", C: "G", R: "Y", Y: "R", K: "M", M: "K", S: "S", W: "W",
                       B: "V", V: "B", D: "H", H: "D", N: "N" };
  const reverseComplement = (s) => [...s].reverse().map((c) => {
    const u = COMPLEMENT[c.toUpperCase()] || c;
    return c === c.toLowerCase() ? u.toLowerCase() : u;
  }).join("");

  function reverseBases(state, a, b) {
    const s = state.sequence;
    const mirror = (x) => a + b - x;
    const features = state.features.map((f) => {
      const parts = partsOf(f);
      if (!parts.every((p) => p.start >= a && p.end <= b)) return f;
      const turned = parts.map((p) => ({ start: mirror(p.end), end: mirror(p.start) })).reverse();
      const swapped = f.partial ? { partial: { left: !!f.partial.right, right: !!f.partial.left } } : {};  // (< and > change sides)
      return withParts({ ...f, ...swapped, strand: -(f.strand || 1) }, turned);
    });
    return { ...state, sequence: s.slice(0, a) + reverseComplement(s.slice(a, b + 1)) + s.slice(b + 1), features };
  }

  class SeqDocument {
    constructor(data) {
      this.data = data;  // name, description... (kept for saving)
      this.sequence = data.sequence;
      this.circular = !!data.circular;  // the topology (can be changed, so it's part of the state)
      this.features = (data.features || []).map((f) => ({ ...f }));
      this.undoStack = [];
      this.redoStack = [];
      this.version = 0;       // changes with every edit (and undo/redo)
      this.savedVersion = 0;
      this.edits = 0;
      this.nextId = 1;
    }

    get length() { return this.sequence.length; }
    get dirty() { return this.version !== this.savedVersion; }

    // ── Undo/redo: snapshots (strings and arrays are shared, so it's cheap) ─
    snapshot() { return { sequence: this.sequence, features: this.features, circular: this.circular, version: this.version }; }

    // One undo step: `apply` gets the current {sequence, features} and returns new ones
    change(apply) {
      this.undoStack.push(this.snapshot());
      if (this.undoStack.length > 200) this.undoStack.shift();
      this.redoStack = [];
      const next = apply({ sequence: this.sequence, features: this.features, circular: this.circular });
      this.sequence = next.sequence;
      this.features = next.features;
      this.circular = next.circular ?? this.circular;
      this.version = ++this.edits;
      this.onChange?.();  // several sequences edited together: their shared undo
    }

    restore(state) {
      this.sequence = state.sequence;
      this.features = state.features;
      this.circular = state.circular;
      this.version = state.version;
    }

    undo() {
      if (!this.undoStack.length) return false;
      this.redoStack.push(this.snapshot());
      this.restore(this.undoStack.pop());
      return true;
    }

    redo() {
      if (!this.redoStack.length) return false;
      this.undoStack.push(this.snapshot());
      this.restore(this.redoStack.pop());
      return true;
    }

    markSaved() { this.savedVersion = this.version; }

    // ── Bases ─────────────────────────────────────────────────────────────
    insert(pos, bases) {
      if (bases) this.change((s) => insertBases(s, pos, bases));
    }

    remove(start, end) {
      if (end >= start) this.change((s) => removeBases(s, start, end));
    }

    // A selection typed or pasted over: one undo step
    replace(start, end, bases) {
      this.change((s) => insertBases(removeBases(s, start, end), start, bases));
    }

    // A selection across the origin (start > end: start..last base, then 0..end)
    // deleted, or typed over: the new bases go at the end, before the origin.
    // Returns where the cursor goes.
    replaceAcross(start, end, bases = "") {
      this.change((s) => {
        const cut = removeBases(removeBases(s, start, s.sequence.length - 1), 0, end);
        return bases ? insertBases(cut, cut.sequence.length, bases) : cut;
      });
      return this.length;
    }

    // ── Features ──────────────────────────────────────────────────────────
    setFeature(index, feature) {
      this.change((s) => ({ ...s, features: s.features.map((f, i) => (i === index ? feature : f)) }));
    }

    addFeature(feature) {
      this.change((s) => ({ ...s, features: [...s.features, { id: `new${this.nextId++}`, notes: {}, ...feature }] }));
      return this.features.length - 1;
    }

    deleteFeature(index) {
      this.change((s) => ({ ...s, features: s.features.filter((f, i) => i !== index) }));
    }

    // Several at once (one undo step): the features whose index is in `indices`
    deleteFeatures(indices) {
      const gone = new Set(indices);
      this.change((s) => ({ ...s, features: s.features.filter((f, i) => !gone.has(i)) }));
    }

    // What /api/sequences PUT gets
    // Base p becomes the first (circular sequences)
    setOrigin(p) {
      if (p > 0 && p < this.length) this.change((s) => rotateBases(s, p));
    }

    // The genetic code, stored as GenBank does: /transl_table on every CDS
    setGeneticCode(code) {
      this.change((s) => ({ ...s, features: s.features.map((f) => (f.type === "CDS"
        ? { ...f, notes: { ...(f.notes || {}), transl_table: [String(code)] } } : f)) }));
    }

    // Bases a..b turned round (reverse complement); the whole sequence by default
    reverseComplement(a = 0, b = this.length - 1) {
      if (b >= a) this.change((s) => reverseBases(s, a, b));
    }

    setCircular(on) {
      if (on !== this.circular) this.change((s) => ({ ...s, circular: on }));
    }

    toData() {
      return { ...this.data, sequence: this.sequence, features: this.features, circular: this.circular };
    }
  }

  // GenBank-style locations, 1-based: "100..200", "complement(join(1..50,80..120))"
  // A partial end is "<" before the lowest coordinate and ">" before the highest: the feature's
  // `partial` {left, right} (in GenBank's way, whatever the strand)
  function locationText(f) {
    const all = partsOf(f);
    const first = all.reduce((k, p, i) => (p.start < all[k].start ? i : k), 0);
    const last = all.reduce((k, p, i) => (p.end > all[k].end ? i : k), 0);
    const parts = all.map((p, i) => {
      const a = `${f.partial?.left && i === first ? "<" : ""}${p.start + 1}`, b = `${f.partial?.right && i === last ? ">" : ""}${p.end + 1}`;
      return p.start === p.end && a === `${p.start + 1}` && b === `${p.end + 1}` ? a : `${a}..${b}`;
    });
    const core = parts.length > 1 ? `join(${parts.join(",")})` : parts[0];
    return f.strand === -1 ? `complement(${core})` : core;
  }

  // ... and back: { strand, parts, partial } or null if it can't be read
  function parseLocation(text, length) {
    let t = text.replace(/\s/g, "");
    let strand = 1;
    const comp = /^complement\((.*)\)$/i.exec(t);
    if (comp) { strand = -1; t = comp[1]; }
    const join = /^(?:join|order)\((.*)\)$/i.exec(t);
    if (join) t = join[1];
    const parts = [], before = [], after = [];
    for (const piece of t.split(",")) {
      const m = /^(<)?(\d+)(?:\.\.(>)?(\d+))?$/.exec(piece);
      if (!m) return null;
      const start = Number(m[2]) - 1, end = Number(m[4] || m[2]) - 1;
      if (start < 0 || end < start || end >= length) return null;
      parts.push({ start, end });
      before.push(!!m[1]);
      after.push(!!m[3]);
    }
    if (!parts.length) return null;
    const first = parts.reduce((k, p, i) => (p.start < parts[k].start ? i : k), 0);
    const last = parts.reduce((k, p, i) => (p.end > parts[k].end ? i : k), 0);
    const partial = { left: before[first], right: after[last] };
    return { strand, parts, partial: partial.left || partial.right ? partial : undefined };
  }

  // ── Alignments: gap editing ────────────────────────────────────────────
  // Rows are { name, sequence } with gaps ("-"). Only gaps move, so each row's
  // bases (and the features drawn from its source sequence) never change.
  // A block is rows r0..r1, columns c0..c1; a cursor is a block with c1 = c0 - 1,
  // which then stands for the bases after the cursor (after any gaps right
  // there) up to the next gap.
  //
  //   const doc = new AlignmentDocument(rows);
  //   doc.push(r0, r1, c0, c1);   // the block one column right (a gap before it)
  //   doc.pull(r0, r1, c0, c1);   // one column left (the gap before it removed)
  //   doc.squeeze(r0, r1, c0, c1); // the gaps inside it moved to its end
  class AlignmentDocument {
    constructor(rows) {
      this.names = rows.map((r) => r.name);
      this.seqs = rows.map((r) => r.sequence);
      this.undoStack = [];
      this.redoStack = [];
      this.version = 0;
      this.savedVersion = 0;
      this.edits = 0;
    }

    get length() { return Math.max(0, ...this.seqs.map((s) => s.length)); }
    get dirty() { return this.version !== this.savedVersion; }
    rows() { return this.names.map((name, i) => ({ name, sequence: this.seqs[i] })); }
    markSaved() { this.savedVersion = this.version; }

    // One undo step (rows are strings: a snapshot is a copy of the array)
    change(apply) {
      const before = { names: this.names, seqs: this.seqs, version: this.version };
      const seqs = [...this.seqs];
      if (apply(seqs) === false) return false;
      this.undoStack.push(before);
      if (this.undoStack.length > 200) this.undoStack.shift();
      this.redoStack = [];
      this.seqs = tidy(seqs);
      this.version = ++this.edits;
      return true;
    }

    undo() {
      if (!this.undoStack.length) return false;
      this.redoStack.push({ names: this.names, seqs: this.seqs, version: this.version });
      Object.assign(this, this.undoStack.pop());
      return true;
    }

    redo() {
      if (!this.redoStack.length) return false;
      this.undoStack.push({ names: this.names, seqs: this.seqs, version: this.version });
      Object.assign(this, this.redoStack.pop());
      return true;
    }

    removeRows(indices) {
      const drop = new Set(indices);
      if (!drop.size || drop.size >= this.names.length || [...drop].some((i) => i < 0 || i >= this.names.length)) return false;
      this.undoStack.push({ names: this.names, seqs: this.seqs, version: this.version });
      if (this.undoStack.length > 200) this.undoStack.shift();
      this.redoStack = [];
      this.names = this.names.filter((_, i) => !drop.has(i));
      this.seqs = this.seqs.filter((_, i) => !drop.has(i));
      this.version = ++this.edits;
      return true;
    }

    push(r0, r1, c0, c1) {
      return this.change((seqs) => {
        for (let r = r0; r <= r1; r++) {
          const s = seqs[r].padEnd(c0, "-");
          const moved = s.slice(0, c0) + "-" + s.slice(c0);
          // the bases after the block move too, up to the first gap, which takes the shift
          const gap = moved.indexOf("-", c1 < c0 ? basesFrom(moved, c0 + 1) : c1 + 2);
          seqs[r] = gap < 0 ? moved : moved.slice(0, gap) + moved.slice(gap + 1);
        }
      });
    }

    pull(r0, r1, c0, c1) {
      if (c0 < 1) return false;
      for (let r = r0; r <= r1; r++) if ((this.seqs[r][c0 - 1] || "-") !== "-") return false;  // no gap to take
      return this.change((seqs) => {
        for (let r = r0; r <= r1; r++) {
          const s = seqs[r];
          // a cursor: the bases up to the next gap move; a block: just the block
          const next = s.indexOf("-", basesFrom(s, c0));
          const end = c1 >= c0 ? c1 + 1 : next < 0 ? s.length : next;
          seqs[r] = s.slice(0, c0 - 1) + s.slice(c0, end) + "-" + s.slice(end);
        }
      });
    }

    squeeze(r0, r1, c0, c1) {
      return this.change((seqs) => {
        let any = false;
        for (let r = r0; r <= r1; r++) {
          const s = seqs[r], block = s.slice(c0, c1 + 1), bases = block.replace(/-/g, "");
          if (bases.length === block.length) continue;
          any = true;
          seqs[r] = s.slice(0, c0) + bases + "-".repeat(block.length - bases.length) + s.slice(c1 + 1);
        }
        return any;
      });
    }
  }

  // The first base at or after column c (the length if none)
  function basesFrom(s, c) {
    const i = s.slice(c).search(/[^-]/);
    return i < 0 ? s.length : c + i;
  }

  // Rows the same length again; columns with only gaps go
  function tidy(seqs) {
    const length = Math.max(0, ...seqs.map((s) => s.length));
    seqs = seqs.map((s) => s.padEnd(length, "-"));
    const empty = [];
    for (let c = 0; c < length; c++) if (seqs.every((s) => s[c] === "-")) empty.push(c);
    if (!empty.length) return seqs;
    const drop = new Set(empty);
    return seqs.map((s) => [...s].filter((_, c) => !drop.has(c)).join(""));
  }

  // Non-overlapping windows, 0-based half-open coordinates. Ambiguous bases do
  // not enter the denominator; an entirely ambiguous window has gc=null.
  function gcWindows(sequence, size) {
    size = Math.max(1, Math.floor(Number(size) || 1));
    const out = [];
    for (let start = 0; start < sequence.length; start += size) {
      const end = Math.min(sequence.length, start + size);
      let gc = 0, called = 0, g = 0, cCount = 0;
      for (let i = start; i < end; i++) {
        const c = sequence.charCodeAt(i) & ~32;
        if (c === 65 || c === 67 || c === 71 || c === 84 || c === 85) {
          called++; if (c === 67 || c === 71) gc++;
          if (c === 71) g++; if (c === 67) cCount++;
        }
      }
      out.push({ start, end, called, gc: called ? gc / called : null, g, c: cCount });
    }
    return out;
  }

  function autoWindow(length) {
    const target = Math.max(1, length / 500), power = 10 ** Math.floor(Math.log10(target));
    return Math.max(1, [1, 2, 5, 10].map(n => n * power).reduce((a, b) =>
      Math.abs(b - target) < Math.abs(a - target) ? b : a));
  }

  function gcSkew(sequence, size) {
    let sum = 0;
    return gcWindows(sequence, size).map(w => {
      sum += w.g - w.c;
      return { ...w, skew: w.g + w.c ? (w.g - w.c) / (w.g + w.c) : null,
        cumulative: sequence.length ? sum / sequence.length : 0 };
    });
  }

  window.SeqDocument = SeqDocument;
  window.AlignmentDocument = AlignmentDocument;
  window.SeqEdit = { cleanBases, partsOf, withParts, locationText, parseLocation, gcWindows, gcSkew, autoWindow,
                     rotate: (data, p) => rotateBases({ sequence: data.sequence, features: data.features || [] }, p) };
})();
