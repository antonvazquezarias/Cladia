// Cladia sequence viewer: the circular map (for one sequence). It extends
// SeqView (seqview.js), so selection, find, copy, features, translations, the
// status line and the overview are the same as on the linear map.
//
// The position at the top of the circle is `rotation` (in bases). Unzoomed the
// whole circle fits; Ctrl+wheel makes the circle bigger (radius = base x zoom),
// keeping the top of its rings at the top of the view, so zooming in shows an
// ever flatter arc. When a base gets ~8 px, bases and translations are drawn
// along the circle. The wheel rotates it.

(function () {
  const SeqView = window.SeqView;
  const looks = SeqView.looks;
  const COMPLEMENT = { A: "T", C: "G", G: "C", T: "A", U: "A", N: "N" };
  const BASE_COLORS = { A: "#ef6f6f", C: "#8080f0", G: "#f2c94c", T: "#62cc62", U: "#62cc62" };
  const SELECTION = "rgba(47, 111, 94, 0.18)", SELECTION_EDGE = "#2f6f5e", MATCH = "#e67e22";
  const STATUS = 20, OVERVIEW = 30;
  const LANE = 19, BASE_RING = 15, TRANS_RING = 13, TICKS = 34;
  const LETTERS_FROM = 8;   // px per base on the circle from which bases are drawn
  const MAX_PX_PER_BASE = 18;
  const FONT = "12px Consolas, 'Courier New', monospace";
  const LABEL_FONT = "11px 'Segoe UI', system-ui, sans-serif";
  const SMALL_FONT = "10px 'Segoe UI', system-ui, sans-serif";
  const TAU = 2 * Math.PI;

  Object.assign(SeqView.prototype, {
    setCircular(on) {
      this.cancelNavigation();
      this.circularMap = on && this.single && !this.protein;
      if (this.circularMap && this.circZoom === undefined) this.resetCircular();
      this.draw();
    },

    resetCircular() {
      this.circZoom = 1;
      this.rotation = 0;
    },

    // ── Geometry ─────────────────────────────────────────────────────────
    // (a method, not a getter: Object.assign would copy a getter's value once)
    circArea() { return { top: 0, height: Math.max(50, this.height - STATUS - OVERVIEW - 2) }; },

    // Rings for the bases: the sequence, and its complementary strand if shown
    baseRings() { return this.show.complement ? 2 : 1; },

    ringSpace() {
      return 8 + (this.circLetters ? BASE_RING * this.baseRings() : 0) + this.circLanes().total + 6;
    },

    // Each lane's distance from the first lane; a lane with a CDS is wider when
    // translations are shown (the ring inside its arcs)
    circLanes() {
      const row = this.rows[0];
      const track = this.circLetters && this.show.translations ? TRANS_RING : 0;
      const tops = [], tracks = [];
      let total = 0;
      for (let lane = 0; this.show.features && lane < (row.lanes || 0); lane++) {
        tops.push(total);
        tracks.push(row.cdsLanes.has(lane) ? track : 0);
        total += LANE + tracks[lane];
      }
      return { tops, tracks, total };
    },

    // Radius of the backbone and the centre, from the zoom
    circGeometry() {
      const { top, height } = this.circArea();
      const w = this.width;
      this.circLetters = false;
      let ring = this.ringSpace();
      const base = Math.max(40 + (this.gcHeight?.() || 0), Math.min(w, height) / 2 - ring - 20);
      const radius = base * this.circZoom;
      this.circLetters = (TAU * radius) / this.length >= LETTERS_FROM;
      ring = this.ringSpace();
      const outer = radius + ring;
      // the whole circle fits: centred; else the top of the rings near the top
      const cy = 2 * outer + 40 <= height ? top + height / 2 : top + 20 + outer;
      return { cx: w / 2, cy, radius, outer, base };
    },

    angleOf(pos) { return ((pos - this.rotation) / this.length) * TAU; },  // 0 = top, clockwise
    pointAt(g, angle, r) { return [g.cx + r * Math.sin(angle), g.cy - r * Math.cos(angle)]; },

    // Positions in view (zoomed in, only part of the circle is on screen)
    visibleRange(g) {
      if (g.cy - g.outer >= 0 && g.cy + g.outer <= this.circArea().height && g.outer * 2 <= this.width) return null;  // all of it
      const half = Math.asin(Math.min(1, (this.width / 2 + 40) / g.radius));
      const span = Math.min(this.length, Math.ceil((half / Math.PI) * this.length) + 2);
      return { from: this.rotation - span, to: this.rotation + span };
    },

    inView(range, start, end) {
      if (!range) return true;
      const L = this.length;
      if (end < start) end += L;  // a range written across the origin
      // Intersect the complete interval with any periodic copy of the viewport.
      // Normalising endpoints independently loses the middle of long features.
      const firstTurn = Math.ceil((range.from - end) / L);
      const lastTurn = Math.floor((range.to - start) / L);
      return firstTurn <= lastTurn;
    },

    // ── Zoom and rotation ────────────────────────────────────────────────
    circularZoom(factor) {
      const g = this.circGeometry();
      const maxZoom = Math.max(1, (MAX_PX_PER_BASE * this.length) / TAU / g.base);
      const previous = this.navigation?.target.circZoom ?? this.circZoom;
      const circZoom = Math.min(maxZoom, Math.max(1, previous * factor));
      if (circZoom === previous) return;
      const target = { circZoom };
      if (this.selection) target.rotation = this.selectionMiddle(this.selection);
      else if (this.caret !== null) target.rotation = this.caret;
      this.navigate(target);
    },

    rotateBy(px) {
      this.cancelNavigation();
      const g = this.circGeometry();
      const bases = (px / g.radius / TAU) * this.length;  // the circle's edge moves `px`
      this.rotation = ((this.rotation + bases) % this.length + this.length) % this.length;
      this.draw();
    },

    rotateTo(pos, animate = true) {
      this.navigate({ rotation: ((pos % this.length) + this.length) % this.length }, animate);
    },

    // ── Mouse ─────────────────────────────────────────────────────────────
    circPosition(x, y, g = this.circGeometry()) {
      const angle = Math.atan2(x - g.cx, g.cy - y);  // 0 at the top, clockwise
      const pos = Math.floor(this.rotation + (angle / TAU) * this.length);
      return { pos: ((pos % this.length) + this.length) % this.length, r: Math.hypot(x - g.cx, y - g.cy) };
    },

    circFeatureAt(g, pos, r) {
      if (!this.show.features) return null;
      const d = r - this.lanesRadius(g);
      const lanes = this.circLanes();
      const lane = d < 0 || d > lanes.total ? -1 : lanes.tops.findLastIndex((t) => t <= d);  // (past the last lane: nothing)
      if (lane < 0) return null;
      return this.rows[0].features.find((f) => f.lane === lane && f.parts.some((p) => pos >= p.start && pos <= p.end)) || null;
    },

    lanesRadius(g) {
      const row = this.rows[0];
      return g.radius + 8 + (this.circLetters ? BASE_RING * this.baseRings() : 0);
    },

    circMouseDown(e) {
      const { x, y } = this.point(e);
      if (y > this.height - OVERVIEW) return false;  // the overview: as on the linear map
      const g = this.circGeometry();
      const start = this.circPosition(x, y, g);
      const anchor = e.shiftKey && this.selection ? this.selection.c0 : start.pos;
      const L = this.length;
      let moved = false;
      let last = start.pos, reach = anchor;  // reach: where the drag got to, counting turns past the origin
      const origin = { x: e.clientX, y: e.clientY };
      this.drag((ev) => {
        moved = moved || Math.abs(ev.clientX - origin.x) + Math.abs(ev.clientY - origin.y) > 3;
        if (!moved) return;
        const p = this.point(ev);
        const pos = this.circPosition(p.x, p.y).pos;
        let step = pos - last;
        if (step > L / 2) step -= L;
        if (step < -L / 2) step += L;
        last = pos;
        reach = Math.max(anchor - L + 1, Math.min(anchor + L - 1, reach + step));
        // across the origin (a circular sequence): a selection that wraps
        if (this.circularSequence && reach >= L) this.select(anchor, reach - L, 0, 0, true);
        else if (this.circularSequence && reach < 0) this.select(reach + L, anchor, 0, 0, true);
        else this.select(anchor, Math.max(0, Math.min(L - 1, reach)), 0, 0);
      }, () => {
        if (moved) return;
        if (e.shiftKey && this.selection) return this.select(anchor, start.pos, 0, 0);
        const feature = this.circFeatureAt(g, start.pos, start.r);
        const wrap = feature && this.circularSequence && feature.parts.length === 2 &&
          feature.parts.find((p) => p.end === this.length - 1) && feature.parts.find((p) => p.start === 0);
        if (wrap) this.select(feature.parts.find((p) => p.end === this.length - 1).start,
                              feature.parts.find((p) => p.start === 0).end, 0, 0, true);  // a feature across the origin
        else if (feature) this.selectFeature(feature, 0);
        else if (start.r > g.radius - TICKS && start.r < this.lanesRadius(g)) {  // on the bases: the cursor
          const angle = Math.atan2(x - g.cx, g.cy - y);
          const pos = Math.round(this.rotation + (angle / TAU) * this.length);
          this.setCaret(((pos % this.length) + this.length) % this.length);
        } else this.clearSelection();
      }, true);
      return true;
    },

    circHover(e) {
      const { x, y } = this.point(e);
      let tip = "", feature = null;
      this.hoverTrack = false;
      if (y < this.height - OVERVIEW - STATUS) {
        const g = this.circGeometry();
        const { pos, r } = this.circPosition(x, y, g);
        const rings = this.circRings(g);
        const inRing = (band) => band && r >= band.inner && r <= band.outer;
        if (this.mapping && rings) {
          this.hoverTrack = true;
          if (inRing(rings.cover)) tip = this.mappingTrackTip("coverage", pos);
          else if (inRing(rings.agree)) tip = this.mappingTrackTip("agreement", pos);
          else if (inRing(rings.clips)) tip = this.mappingTrackTip("clips", pos);
          else if (r >= rings.inner && r <= rings.cover.outer) tip = this.mappingTip(pos);
        }
        for (const track of rings.gc || []) if (inRing(track)) { tip = this.gcTip(track, pos); this.hoverTrack = true; }
        if (!tip && r > g.radius - TICKS && r < g.outer + 10) {
          tip = `Position ${pos + 1}: ${this.rows[0].seq[pos] || ""}`;
          feature = this.circFeatureAt(g, pos, r);
          if (feature) tip += ` · ${feature.type} ${feature.name}${this.stopTip(this.rows[0], feature)}`;
        }
      }
      this.setHoverFeature(feature);
      if (tip !== this.hover) {
        this.hover = tip;
        this.drawStatus();
      }
    },

    // ── Drawing ───────────────────────────────────────────────────────────
    drawCircular() {
      const ctx = this.ctx;
      const g = this.circGeometry();
      const range = this.visibleRange(g);
      const row = this.rows[0];
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, this.width, this.circArea().height);
      ctx.clip();

      // backbone (a thin band: see arcBand); a linear sequence's ends: a gap at the origin
      ctx.fillStyle = "#888";
      this.arcBand(g, -Math.PI, Math.PI, g.radius - 0.75, g.radius + 0.75);
      ctx.fill();
      if (this.circularSequence === false) {
        const gap = 6 / g.radius, a = this.angleOf(0);
        ctx.fillStyle = "white";
        this.arcBand(g, a - gap, a + gap, g.radius - 3, g.radius + 3);
        ctx.fill();
        ctx.fillStyle = "#b0171f";
        for (const side of [a - gap, a + gap]) {
          this.arcBand(g, side - 1 / g.radius, side + 1 / g.radius, g.radius - 7, g.radius + 7);
          ctx.fill();
        }
      }

      this.drawCircTicks(g, range);
      if (this.mapping) this.drawCircCoverage(g, range);
      this.drawCircGC?.(g);
      let r = g.radius + 8;
      if (this.circLetters) {
        this.drawCircBases(g, range, r);
        r += BASE_RING * this.baseRings();
      }
      if (this.show.features) this.drawCircFeatures(g, range, r);
      this.drawCircMatches(g, range);
      this.drawCircSelection(g);
      this.drawCircCaret(g);
      this.drawCircCentre(g);
      ctx.restore();
    },

    drawCircTicks(g, range) {
      const ctx = this.ctx;
      const perBase = (TAU * g.radius) / this.length;
      const steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000];
      const step = steps.find((s) => s * perBase >= 70) || 500000;
      ctx.strokeStyle = "#999";
      ctx.fillStyle = "#555";
      ctx.font = SMALL_FONT;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      // ticks at multiples of the step (1-based positions); zoomed in, only those in
      // view, which may be on either side of the origin
      const L = this.length;
      const ticks = new Set([0]);  // base 1 is always included, whatever the tick interval
      for (const lap of range ? [-1, 0, 1] : [0]) {
        const from = range ? range.from - lap * L + 1 : 1, to = range ? range.to - lap * L + 1 : L;
        for (let t = Math.max(step, Math.ceil(from / step) * step); t <= Math.min(L, to); t += step) ticks.add(t - 1);
      }
      for (const pos of ticks) {
        if (range && !this.inView(range, pos, pos)) continue;
        const a = this.angleOf(pos + 0.5);
        const [x0, y0] = this.pointAt(g, a, g.radius);
        const [x1, y1] = this.pointAt(g, a, g.radius - 6);
        ctx.beginPath();
        ctx.moveTo(x0, y0);
        ctx.lineTo(x1, y1);
        ctx.stroke();
        const originDistance = Math.min(pos + .5, L - pos - .5) / L * TAU * g.radius;
        if (pos === 0 || originDistance > 20) this.textAt(g, a, g.radius - 16, (pos + 1).toLocaleString());
      }
    },

    // Upright text centred at angle a, radius r, along the tangent
    textAt(g, a, r, text) {
      const ctx = this.ctx;
      const [x, y] = this.pointAt(g, a, r);
      const norm = ((a % TAU) + TAU) % TAU;
      const flip = norm > Math.PI / 2 && norm < (3 * Math.PI) / 2;  // bottom half: turned round to read upright
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(flip ? a + Math.PI : a);
      ctx.fillText(text, 0, 0);
      ctx.restore();
    },

    // Bases (and the complementary strand) along the circle, coloured
    drawCircBases(g, range, r) {
      const ctx = this.ctx;
      const seq = this.rows[0].seq;
      const perBase = (TAU * g.radius) / this.length;
      const from = range ? Math.floor(range.from) : 0, to = range ? Math.ceil(range.to) : this.length - 1;
      ctx.font = FONT;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (let p = from; p <= to; p++) {
        const pos = ((p % this.length) + this.length) % this.length;
        const ch = seq[pos];
        const a0 = this.angleOf(pos), a1 = this.angleOf(pos + 1);
        // the sequence on the outer ring (on top, as on the linear map), coloured;
        // the complementary strand inside it, in grey
        ctx.fillStyle = BASE_COLORS[ch] || "#d6a6e6";
        const out = r + BASE_RING * (this.baseRings() - 1);  // the sequence's ring
        this.arcBand(g, a0, a1, out, out + BASE_RING - 2);
        ctx.fill();
        ctx.fillStyle = "#111";
        if (perBase >= LETTERS_FROM) this.textAt(g, (a0 + a1) / 2, out + BASE_RING / 2 - 1, ch);
        if (!this.show.complement) continue;
        ctx.fillStyle = "#999";
        this.textAt(g, (a0 + a1) / 2, r + BASE_RING / 2 - 1, COMPLEMENT[ch] || "N");
      }
    },

    // A CDS's amino acids in its ring, from radius r
    drawCircCodons(g, range, f, r) {
      const ctx = this.ctx;
      const aaColor = this.options.aaColor || (() => "#ddd");
      ctx.font = SMALL_FONT;
      for (const codon of this.codonsOf(this.rows[0], f)) {
        if (!this.inView(range, codon.c0, codon.c1)) continue;
        // a codon split by an intron (or the origin): each piece, the letter in the larger one
        const pieces = codon.pieces || [[codon.c0, codon.c1]];
        const widest = pieces.reduce((a, b) => (b[1] - b[0] > a[1] - a[0] ? b : a));
        for (const [c0, c1] of pieces) {
          const a0 = this.angleOf(c0), a1 = this.angleOf(c1 + 1);
          ctx.fillStyle = aaColor(codon.aa);
          this.arcBand(g, a0, a1, r, r + TRANS_RING - 2);
          ctx.fill();
          if (c0 !== widest[0]) continue;
          ctx.fillStyle = "#111";
          this.textAt(g, (a0 + a1) / 2, r + TRANS_RING / 2 - 1, codon.aa);
        }
      }
    },

    // A ring segment between radii r0 and r1, from angle a0 to a1 (clockwise).
    // Canvas arcs fail on huge radii (zoomed in on a large genome), so there the
    // part on screen is drawn as a polygon of short straight pieces.
    // point 1 / -1 makes it an arrow pointing to a1 / a0, `head` (an angle) long.
    arcBand(g, a0, a1, r0, r1, point = 0, head = 0) {
      const ctx = this.ctx;
      const tip = (a) => ctx.lineTo(...this.pointAt(g, a, (r0 + r1) / 2));
      ctx.beginPath();
      if (r1 < 5000) {
        const b0 = point === -1 ? a0 + head : a0, b1 = point === 1 ? a1 - head : a1;
        ctx.arc(g.cx, g.cy, r1, b0 - Math.PI / 2, b1 - Math.PI / 2);
        if (point === 1) tip(a1);
        ctx.arc(g.cx, g.cy, r0, b1 - Math.PI / 2, b0 - Math.PI / 2, true);
        if (point === -1) tip(a0);
        ctx.closePath();
        return;
      }
      const limit = Math.asin(Math.min(1, (this.width / 2 + 60) / r1)) + 0.0001;
      // The visible window repeats every turn. A long band can intersect it
      // twice (one portion on each side of the origin), so clip each copy.
      const first = Math.ceil((a0 - limit) / TAU), last = Math.floor((a1 + limit) / TAU);
      for (let turn = first; turn <= last; turn++) {
        const start = a0 - turn * TAU, end = a1 - turn * TAU;
        const b0 = Math.max(point === -1 ? start + head : start, -limit);
        const b1 = Math.min(point === 1 ? end - head : end, limit);
        if (b1 <= b0) continue;
        const pieces = Math.min(400, Math.max(1, Math.ceil(((b1 - b0) * r1) / 40)));
        for (let i = 0; i <= pieces; i++) {
          const [x, y] = this.pointAt(g, b0 + ((b1 - b0) * i) / pieces, r1);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        if (point === 1 && end >= -limit && end <= limit) tip(end);
        for (let i = pieces; i >= 0; i--) ctx.lineTo(...this.pointAt(g, b0 + ((b1 - b0) * i) / pieces, r0));
        if (point === -1 && start >= -limit && start <= limit) tip(start);
        ctx.closePath();
      }
    },

    // Clip each exon at the label's actual radius, rather than the backbone's
    // approximate visible range. This also handles two visible pieces at origin.
    visibleLabelArc(g, parts, radius) {
      const area = this.circArea(), inset = 9;
      const left = inset, right = this.width - inset, top = area.top + inset, bottom = area.top + area.height - inset;
      const boundaries = [];
      for (const x of [left, right]) {
        const v = (x - g.cx) / radius;
        if (Math.abs(v) <= 1) { const a = Math.asin(v); boundaries.push(a, Math.PI - a); }
      }
      for (const y of [top, bottom]) {
        const v = (g.cy - y) / radius;
        if (Math.abs(v) <= 1) { const a = Math.acos(v); boundaries.push(a, -a); }
      }
      let best = null;
      for (const p of parts) {
        const start = this.angleOf(p.start), end = this.angleOf(p.end + 1), cuts = [start, end];
        for (const a of boundaries) for (let turn = Math.ceil((start - a) / TAU); turn <= Math.floor((end - a) / TAU); turn++)
          cuts.push(a + turn * TAU);
        cuts.sort((a, b) => a - b);
        let visible = null;
        for (let i = 0; i + 1 < cuts.length; i++) {
          const a = cuts[i], b = cuts[i + 1];
          if (b - a < 1e-12) continue;
          const [x, y] = this.pointAt(g, (a + b) / 2, radius);
          if (x >= left && x <= right && y >= top && y <= bottom) {
            if (visible && Math.abs(visible.end - a) < 1e-10) visible.end = b;
            else visible = { start: a, end: b };
            if (!best || visible.end - visible.start > best.end - best.start) best = { ...visible };
          } else visible = null;
        }
      }
      return best;
    },

    // Features as arcs pointing to their 3' end, names along the arc
    drawCircFeatures(g, range, r0) {
      const ctx = this.ctx;
      const row = this.rows[0], s = this.selection;
      const h = LANE - 5;
      const lanes = this.circLanes();
      ctx.font = LABEL_FONT;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const f of row.features) {
        const parts = f.parts.filter((p) => this.inView(range, p.start, p.end));
        if (!parts.length) continue;
        const r = r0 + lanes.tops[f.lane] + lanes.tracks[f.lane];  // the arc, outside its translation
        if (lanes.tracks[f.lane] && f.type === "CDS") this.drawCircCodons(g, range, f, r0 + lanes.tops[f.lane] + 1);
        ctx.font = LABEL_FONT;
        const selected = s && s.c0 === f.c0 && s.c1 === f.c1;
        const style = looks.featureStyle(f, f === this.hoverFeature, selected);
        ctx.fillStyle = style.edge;
        for (const [a, b] of looks.introns(f, this.length)) {
          this.arcBand(g, this.angleOf(a.end + 1), this.angleOf(b.start), r + h / 2 - 0.5, r + h / 2 + 0.5);
          ctx.fill();
        }
        const tipPart = looks.arrowPart(f);
        for (const p of parts) {
          const a0 = this.angleOf(p.start), a1 = this.angleOf(p.end + 1);
          ctx.fillStyle = style.fill;
          const px = (a1 - a0) * (r + h);
          const head = p === tipPart && px >= 4 ? Math.min(h * 0.6, px * 0.6, 8) / (r + h / 2) : 0;
          this.arcBand(g, a0, a1, r, r + h, head ? f.strand : 0, head);
          ctx.fill();
          if (px < 4) continue;
          ctx.strokeStyle = style.edge;
          ctx.lineWidth = style.edgeWidth;
          ctx.stroke();
          ctx.lineWidth = 1;
        }
        // after an internal stop: lighter to the 3' end, a dark red mark at the stop
        const dim = this.afterStop(row, f);
        if (dim) {
          ctx.fillStyle = "rgba(255, 255, 255, 0.62)";
          for (const p of parts) {
            const from = Math.max(p.start, dim.c0), to = Math.min(p.end, dim.c1);
            if (from > to) continue;
            const a0 = this.angleOf(from), a1 = this.angleOf(to + 1);
            const px = (this.angleOf(p.end + 1) - this.angleOf(p.start)) * (r + h);
            const tipHere = p === tipPart && px >= 4 && (f.strand === -1 ? from === p.start : to === p.end);
            const head = tipHere ? Math.min(h * 0.6, px * 0.6, 8) / (r + h / 2) : 0;
            this.arcBand(g, a0, a1, r, r + h, head ? f.strand : 0, head);
            ctx.fill();
          }
          for (const [c0, c1] of dim.stop.pieces || [[dim.stop.c0, dim.stop.c1]]) {  // (each piece of a split codon)
            const a0 = this.angleOf(c0), a1 = this.angleOf(c1 + 1);
            const min = 2 / (r + h);  // at least 2 px
            const mid = (a0 + a1) / 2, half = Math.max(min, a1 - a0) / 2;
            ctx.fillStyle = SeqView.STOP;
            this.arcBand(g, mid - half, mid + half, r - 2, r + h + 2);
            ctx.fill();
          }
        }
        // Centre the name in the widest visible exon section, not at the cursor
        // or the midpoint of an exon whose larger portion is off-screen.
        const labelRadius = r + h / 2 + 0.5;
        const visible = this.visibleLabelArc(g, parts, labelRadius);
        if (!visible) continue;
        const length = (visible.end - visible.start) * labelRadius;
        if (length < 24) continue;
        const name = looks.fitText(ctx, f.name, length - 2 * (Math.min(h * 0.6, 8) + 4));
        if (!name) continue;
        ctx.fillStyle = style.ink;
        this.textAlong(g, (visible.start + visible.end) / 2, labelRadius, name);
      }
    },

    // Upright text centred at angle a, radius r, each letter following the curve
    textAlong(g, a, r, text) {
      const ctx = this.ctx;
      const norm = ((a % TAU) + TAU) % TAU;
      const flip = norm > Math.PI / 2 && norm < (3 * Math.PI) / 2;  // bottom half: read the other way
      const widths = [...text].map((ch) => ctx.measureText(ch).width);
      const total = widths.reduce((x, y) => x + y, 0);
      let along = -total / 2;
      [...text].forEach((ch, i) => {
        const at = a + ((flip ? -1 : 1) * (along + widths[i] / 2)) / r;
        along += widths[i];
        const [x, y] = this.pointAt(g, at, r);
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(flip ? at + Math.PI : at);
        ctx.fillText(ch, 0, 0);
        ctx.restore();
      });
    },

    // A mapping's rings inside the ticks, outside in: coverage (the highest depth
    // in each piece of the arc; red where it drops below the minimum depth), then
    // for mappings that have them agreement and clipped reads, drawn as on the
    // linear map's rows (circRings: their radii, also for the hover text)
    circRings(g) {
      const m = this.mapping;
      const cover = { outer: g.radius - TICKS - 4 };
      cover.inner = cover.outer - 34;
      let r = cover.inner - 5;  // under the red line
      const ring = (on) => {
        if (!on) return null;
        const band = { outer: r, inner: r - 10 };
        r = band.inner - 3;
        return band;
      };
      const agree = ring(!!m.agreement), clips = ring(!!m.clipsAt);
      return { cover, agree, clips, inner: r };
    },

    // The pieces of the arc in view a ring is drawn in: (from, to) each, about 2 px
    circBins(g, range, radius, each) {
      const L = this.length;
      const from = range ? range.from : 0, to = range ? range.to : L;
      const span = to - from;
      const bins = Math.max(1, Math.min(2000, Math.ceil(((span / L) * TAU * radius) / 2)));
      for (let b = 0; b < bins; b++) {
        const p0 = from + (span * b) / bins, p1 = from + (span * (b + 1)) / bins;
        const positions = [];
        for (let p = Math.floor(p0); p < Math.max(Math.floor(p0) + 1, Math.floor(p1)); p++) positions.push(((p % L) + L) % L);
        each(this.angleOf(p0), this.angleOf(p1), positions);
      }
      return { from, to };
    },

    drawCircCoverage(g, range) {
      const ctx = this.ctx, m = this.mapping;
      const rings = this.circRings(g);
      if (rings.cover.inner < 30) return;
      const { inner, outer } = rings.cover, h = outer - inner;
      const { from, to } = this.circBins(g, range, outer, () => {});
      ctx.fillStyle = "#f3f4f7";
      this.arcBand(g, this.angleOf(from), this.angleOf(to), inner, outer);
      ctx.fill();
      this.circBins(g, range, outer, (a0, a1, positions) => {
        let hi = 0, lo = Infinity;
        for (const pos of positions) {
          const d = m.depth[pos] || 0;
          if (d > hi) hi = d;
          if (d < lo && m.consensus[pos] !== "*") lo = d;  // a deletion isn't missing coverage
        }
        if (hi) {
          ctx.fillStyle = "#5b8fd6";
          this.arcBand(g, a0, a1, inner, inner + Math.max(1, (hi / m.maxDepth) * h));
          ctx.fill();
        }
        if (lo < m.minDepth) {
          ctx.fillStyle = "#d9534f";
          this.arcBand(g, a0, a1, inner - 3, inner);
          ctx.fill();
        }
      });
      // agreement: green from 95 %, olive to 80 %, red under, as high as the share (from 50 %)
      const colors = SeqView.IDENTITY_COLORS;
      if (rings.agree && rings.agree.inner > 30) {
        const { inner: i0, outer: o0 } = rings.agree, ah = o0 - i0;
        this.circBins(g, range, o0, (a0, a1, positions) => {
          let low = 100;
          for (const pos of positions) if (m.agreement[pos] < low) low = m.agreement[pos];
          ctx.fillStyle = low >= 95 ? colors.full : low >= 80 ? colors.part : colors.low;
          const bh = low >= 95 ? ah : Math.max(2, ((Math.max(50, low) - 50) / 45) * ah);
          this.arcBand(g, a0, a1, i0, i0 + bh);
          ctx.fill();
        });
      }
      // clipped reads: dark red, as high as the count (square root, so a few show)
      if (rings.clips && rings.clips.inner > 30) {
        const { inner: i0, outer: o0 } = rings.clips, ch = o0 - i0;
        ctx.fillStyle = SeqView.STOP;
        this.circBins(g, range, o0, (a0, a1, positions) => {
          let n = 0;
          for (const pos of positions) n += m.clipsAt[pos];
          if (!n) return;
          this.arcBand(g, a0, a1, i0, i0 + Math.max(2, Math.sqrt(Math.min(1, n / m.maxClips)) * ch));
          ctx.fill();
        });
      }
    },

    drawCircMatches(g, range) {
      const ctx = this.ctx;
      ctx.strokeStyle = MATCH;
      this.matches.forEach((m, i) => {
        if (!this.inView(range, m.c0, m.c1)) return;
        ctx.lineWidth = i === this.matchIndex ? 3 : 1.5;
        this.arcBand(g, this.angleOf(m.c0), this.angleOf(m.c1 + 1), g.radius - 3, g.radius + 5);
        ctx.stroke();
      });
      ctx.lineWidth = 1;
    },

    drawCircSelection(g) {
      const s = this.selection;
      if (!s) return;
      const ctx = this.ctx;
      const a0 = this.angleOf(s.c0), a1 = this.angleOf(s.c1 + 1 + (s.wraps ? this.length : 0));
      ctx.fillStyle = SELECTION;
      this.arcBand(g, a0, a1, Math.max(0, g.radius - TICKS), g.outer);
      ctx.fill();
      ctx.strokeStyle = SELECTION_EDGE;
      ctx.stroke();
    },

    drawCircCaret(g) {
      if (this.caret === null) return;
      const ctx = this.ctx;
      const a = this.angleOf(this.caret);
      const [x0, y0] = this.pointAt(g, a, g.radius - 10);
      const [x1, y1] = this.pointAt(g, a, this.lanesRadius(g) - 2);
      ctx.strokeStyle = "#111";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
      ctx.stroke();
      ctx.lineWidth = 1;
    },

    drawCircCentre(g) {
      if (g.cy - 20 > this.circArea().height) return;  // the centre is off screen
      const ctx = this.ctx;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "#222";
      ctx.font = "15px 'Segoe UI', system-ui, sans-serif";
      ctx.fillText(this.rows[0].name, g.cx, g.cy - 10);
      ctx.fillStyle = "#666";
      ctx.font = LABEL_FONT;
      ctx.fillText(`${this.length.toLocaleString()} bp`, g.cx, g.cy + 10);
      if (this.circularSequence === false) {  // a linear sequence drawn as a circle
        ctx.fillStyle = "#b0171f";
        ctx.fillText("linear sequence: its ends aren't joined", g.cx, g.cy + 28);
      }
    },
  });
})();
