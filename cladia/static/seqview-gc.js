// Optional composition tracks. Computation is shared with genome figures;
// this module owns only canvas drawing, track geometry and hover descriptions.
(function () {
  const P = window.SeqView.prototype, TOP = 22;
  const UP = '#367c79', DOWN = '#956aa3', GC_UP = '#426c80', GC_DOWN = '#9aaab3';
  const DATA = '#4f79a6', DATA_DOWN = '#b07a55';  // data tracks (bedGraph / WIG): above / below zero
  const dataText = (v) => Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : Math.abs(v) >= 1 ? String(+v.toFixed(2)) : String(+v.toPrecision(3));
  const valueText = (key, value) => value == null ? 'not available' : key === 'data' ? dataText(value) : key === 'gc'
    ? `${(value * 100).toFixed(1)} %` : `${value > 0 ? '+' : ''}${value.toFixed(3)}`;
  Object.assign(P, {
    gcTrackHeight() { return this.show.gcSize === 'large' ? 64 : 44; },
    gcTrackAt(y) { return y >= TOP ? this.gcTracks()[Math.floor((y - TOP) / this.gcTrackHeight())] : null; },
    gcEnabled() { return this.single && !this.protein && !this.mapping && !this.trace && !this.compare; },
    // A file's values along the sequence ({name, file, intervals [[start, end, value]], min, max}) as tracks
    setDataTracks(list) {
      this.dataTracks = (list || []).map(d => {
        const min = Math.min(0, d.min), top = Math.max(0, d.max);
        return { key: 'data', name: d.name, file: d.file, mean: 0, min, max: top + (top - min) * .05 || 1,
                 windows: d.intervals.map(([start, end, data]) => ({ start, end, data })) };
      });
    },
    gcTracks() {
      if (!this.gcEnabled() || !this.rows[0]) return [];
      const data = (this.dataTracks || []).filter(t => !(this.show.hiddenData || []).includes(t.name));
      if (!(this.show.gc || this.show.gcSkew)) return data;
      const sequence = this.rows[0].seq;
      const size = Number(this.show.gcWindow) || SeqEdit.autoWindow(sequence.length);
      // Rows are rebuilt after edits; unchanged sequence strings reuse the cached windows.
      if (this.gcCache?.sequence !== sequence || this.gcCache?.size !== size) {
        const windows = SeqEdit.gcSkew(sequence, size);
        const called = windows.reduce((n, w) => n + w.called, 0);
        const mean = called ? windows.reduce((n, w) => n + w.g + w.c, 0) / called : null;
        this.gcCache = { sequence, size, windows, mean };
      }
      const { windows, mean } = this.gcCache, tracks = [];
      if (this.show.gc) tracks.push({ key: 'gc', name: 'GC content', mean: mean ?? 0, windows });
      if (this.show.gcSkew) tracks.push({ key: this.show.gcCumulative ? 'cumulative' : 'skew',
        name: this.show.gcCumulative ? 'Cumulative GC skew' : 'GC skew', mean: 0, windows });
      for (const t of tracks) {
        let min = t.mean, max = t.mean;
        for (const w of windows) if (w[t.key] !== null) { min = Math.min(min, w[t.key]); max = Math.max(max, w[t.key]); }
        const pad = Math.max(.02, (max - min) * .1);
        t.min = Math.max(t.key === 'gc' ? 0 : -1, min - pad); t.max = Math.min(1, max + pad);
        if (t.key !== 'gc') { const limit = Math.max(Math.abs(t.min), Math.abs(t.max)); t.min = -limit; t.max = limit; }
      }
      return [...tracks, ...data];
    },
    gcHeight() { return this.gcTracks().length * this.gcTrackHeight(); },
    gcSummary(t) {
      const first = Math.max(0, Math.floor(this.scrollX / this.charWidth));
      const last = Math.min(this.length, Math.ceil((this.scrollX + this.seqWidth) / this.charWidth));
      if (t.key === 'data') {  // the visible stretch: mean over the bases with values, highest
        let sum = 0, bases = 0, high = null;
        for (const w of t.windows) {
          if (w.end <= first || w.start >= last) continue;
          const n = Math.min(w.end, last) - Math.max(w.start, first);
          sum += w.data * n; bases += n; high = high === null ? w.data : Math.max(high, w.data);
        }
        return `${t.name}${t.file ? ` (${t.file})` : ''} · visible ${first + 1}–${last}: ` +
          (bases ? `mean ${dataText(sum / bases)}, highest ${dataText(high)}` : 'no values');
      }
      const region = this.rows[0].seq.slice(first, last);
      const visible = SeqEdit.gcWindows(region, Math.max(1, region.length))[0]?.gc;
      return `${t.name} · whole sequence GC ${this.gcCache.mean == null ? 'no called bases' : (this.gcCache.mean * 100).toFixed(1) + ' %'} · visible ${first + 1}–${last}: ${visible == null ? 'no called bases' : (visible * 100).toFixed(1) + ' % GC'}`;
    },
    gcTip(t, pos) {
      if (t.key === 'data') {  // the interval holding this position (they are sorted)
        let lo = 0, hi = t.windows.length - 1;
        while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (t.windows[mid].start <= pos) lo = mid; else hi = mid - 1; }
        const w = t.windows[lo];
        return w && w.start <= pos && pos < w.end ? `${t.name}: ${dataText(w.data)} · ${w.start + 1}–${w.end}` : `${t.name}: no value at ${pos + 1}`;
      }
      const w = t.windows[Math.min(t.windows.length - 1, Math.max(0, Math.floor(pos / this.gcCache.size)))];
      if (!w) return '';
      const value = w[t.key];
      const explanation = value == null ? (t.key === 'gc' ? 'no called bases' : 'no called G/C bases') : t.key === 'gc'
        ? `${Math.abs((value - t.mean) * 100).toFixed(1)} percentage points ${value >= t.mean ? 'above' : 'below'} genome mean ${valueText('gc', t.mean)}`
        : t.key === 'skew' ? `${value > 0 ? 'G exceeds C' : value < 0 ? 'C exceeds G' : 'G and C balanced'} · (G − C) / (G + C)`
        : 'running (G − C) / genome length; rises with G, falls with C';
      return `${t.name}: ${valueText(t.key, value)} · ${w.start + 1}–${w.end} · ${explanation}`;
    },
    drawGC(first, last, xOf) {
      const ctx = this.ctx, H = this.gcTrackHeight();
      this.gcTracks().forEach((t, index) => {
        const top = TOP + index * H, bottom = top + H - 6;
        const yOf = v => bottom - (v - t.min) / (t.max - t.min) * (H - 12), zero = yOf(t.mean);
        ctx.save(); ctx.beginPath(); ctx.rect(this.nameWidth, top, this.seqWidth, H); ctx.clip();
        // Aggregate to screen pixels when several windows share a pixel.
        const bins = new Map();
        for (const w of t.windows) {
          if (w.end <= first || w.start >= last || w[t.key] === null) continue;
          const left = xOf(w.start), right = xOf(w.end), key = Math.floor(left);
          const b = bins.get(key);
          if (b) { b.sum += w[t.key]; b.n++; b.right = right; b.last = w[t.key]; }
          else bins.set(key, { left, right, sum: w[t.key], n: 1, last: w[t.key] });
        }
        ctx.fillStyle = '#fafbfc'; ctx.fillRect(this.nameWidth, top, this.seqWidth, H - 1);
        let previous = null;
        for (const b of bins.values()) {
          const value = t.key === 'cumulative' ? b.last : b.sum / b.n, y = yOf(value);
          ctx.fillStyle = t.key === 'data' ? value >= 0 ? DATA : DATA_DOWN : t.key === 'gc' ? value >= t.mean ? GC_UP : GC_DOWN : value >= 0 ? UP : DOWN;
          if (t.key === 'cumulative') {
            const x = Math.min(this.width, b.right);
            ctx.strokeStyle = ctx.fillStyle; ctx.lineWidth = 1.4; ctx.beginPath();
            ctx.moveTo(previous?.x ?? b.left, previous?.y ?? y); ctx.lineTo(x, y); ctx.stroke(); previous = { x, y };
          } else ctx.fillRect(b.left, Math.min(y, zero), Math.max(1, b.right - b.left), Math.max(.5, Math.abs(zero - y)));
        }
        ctx.strokeStyle = '#64727a'; ctx.lineWidth = .7; ctx.setLineDash([3, 3]); ctx.beginPath();
        ctx.moveTo(this.nameWidth, zero); ctx.lineTo(this.width, zero); ctx.stroke(); ctx.restore();
        ctx.save(); ctx.font = "9px 'Segoe UI', sans-serif"; ctx.textAlign = 'right'; ctx.textBaseline = 'alphabetic';
        for (const [v, y] of [[t.max, top + 10], [t.min, bottom]]) {
          const text = valueText(t.key, v), width = ctx.measureText(text).width;
          ctx.fillStyle = 'rgba(255,255,255,.9)'; ctx.fillRect(this.width - width - 8, y - 9, width + 7, 11);
          ctx.fillStyle = '#52616b'; ctx.fillText(text, this.width - 4, y);
        } ctx.restore();
      });
    },
    drawCircGC(g) {
      const ctx = this.ctx, rings = this.circRings(g), half = this.show.gcSize === 'large' ? 12 : 7;
      for (const t of rings.gc || []) {
        const middle = (t.outer + t.inner) / 2;
        let previous = null;
        ctx.strokeStyle = '#aaa'; ctx.lineWidth = .5;
        ctx.beginPath(); ctx.arc(g.cx, g.cy, middle, 0, Math.PI * 2); ctx.stroke();
        for (const w of t.windows) {
          if (w[t.key] === null) continue;
          const delta = (w[t.key] - t.mean) / Math.max(t.max - t.mean, t.mean - t.min) * half;
          ctx.fillStyle = t.key === 'data' ? (delta >= 0 ? DATA : DATA_DOWN) : t.key === 'gc' ? (delta >= 0 ? GC_UP : GC_DOWN) : delta >= 0 ? UP : DOWN;
          if (t.key === 'cumulative') {
            ctx.strokeStyle = ctx.fillStyle; ctx.lineWidth = 1.3; ctx.beginPath();
            const a = this.pointAt(g, this.angleOf(w.start), middle + delta), b = this.pointAt(g, this.angleOf(w.end), middle + delta);
            ctx.moveTo(...(previous || a)); ctx.lineTo(...a); ctx.lineTo(...b); ctx.stroke(); previous = b;
          } else {
            this.arcBand(g, this.angleOf(w.start), this.angleOf(w.end),
              Math.min(middle, middle + delta), Math.max(middle + .4, middle + delta)); ctx.fill();
          }
        }
      }
      ctx.save(); ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
      (rings.gc || []).forEach((t, i) => {
        const y = 17 + i * 33;
        ctx.fillStyle = 'rgba(255,255,255,.92)'; ctx.fillRect(7, y - 12, 285, 32);
        ctx.font = "11px 'Segoe UI', sans-serif"; ctx.fillStyle = '#34434b';
        ctx.fillText(`${i ? 'Inner' : 'Outer'} ring: ${t.name}${t.key === 'gc' ? ' · mean ' + valueText('gc', this.gcCache.mean) : ''}`, 12, y);
        ctx.font = "10px 'Segoe UI', sans-serif";
        ctx.fillText(t.key === 'data' ? `Outward: higher · ${dataText(t.min)} to ${dataText(t.max)}` : t.key === 'gc' ? 'Outward: above mean · inward: below mean' : t.key === 'skew'
          ? 'Outward: G > C · inward: C > G' : 'Outward: positive total · inward: negative total', 12, y + 13);
      }); ctx.restore();
    }
  });
  const rings = P.circRings;
  P.circRings = function (g) {
    if (this.mapping) return rings.call(this, g);
    let r = g.radius - 38;
    const height = this.show.gcSize === 'large' ? 28 : 18;
    const gc = this.gcTracks().map(t => { const band = { ...t, outer: r, inner: r - height }; r -= height + 5; return band; });
    return { gc, inner: r };
  };
  const names = P.drawNames;
  P.drawNames = function () {
    names.call(this);
    const ctx = this.namesCtx;
    ctx.save(); ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'; ctx.fillStyle = '#333';
    this.gcTracks().forEach((t, i) => {
      const H = this.gcTrackHeight();
      const top = TOP + i * H;
      ctx.font = "11px 'Segoe UI', sans-serif"; ctx.fillText(t.key === 'cumulative' ? 'Cumulative skew' : t.name, 8, top + 12);
      if (t.key === 'data') {  // a file's values: where they came from and their range
        ctx.font = "10px 'Segoe UI', sans-serif"; ctx.fillStyle = '#59666e';
        if (t.file) ctx.fillText(t.file, 8, top + 24);
        ctx.fillText(`${dataText(t.min)} – ${dataText(t.max)}`, 8, top + 35);
        ctx.fillStyle = '#333';
        return;
      }
      ctx.font = "10px 'Segoe UI', sans-serif";
      ctx.fillStyle = '#59666e';
      ctx.fillText(t.key === 'gc' ? `mean ${valueText('gc', this.gcCache.mean)}` : t.key === 'skew' ? '0 = G and C balanced' : 'running G − C / length', 8, top + 24);
      ctx.fillStyle = t.key === 'gc' ? GC_UP : UP;
      ctx.fillText(t.key === 'gc' ? '↑ GC-rich' : t.key === 'cumulative' ? 'rises with G' : '↑ G > C', 8, top + 35);
      ctx.fillStyle = t.key === 'gc' ? '#657782' : DOWN;
      ctx.fillText(t.key === 'gc' ? '↓ AT-rich' : t.key === 'cumulative' ? 'falls with C' : '↓ C > G', 78, top + 35);
      ctx.fillStyle = '#333';
    }); ctx.restore();
  };
})();
