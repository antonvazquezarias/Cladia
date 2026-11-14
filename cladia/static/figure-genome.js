// A single-genome figure kind. The editor supplies its text/measurement helpers;
// all interaction changes settings, never the underlying sequence or annotations.
(function () {
  window.makeGenomeFigure = function (h) {
    const { PT_PER_MM, FONTS, esc, num, clone, textWidth, markupRuns, speciesRuns, runsWidth } = h;
    const TAU = Math.PI * 2, DEG = Math.PI / 180;
    const clamp = (x, a, b) => Math.min(b, Math.max(a, Number(x) || 0));
    const classes = [
      ["psa", "Photosystem I", "^psa", "#176b4b"], ["psb", "Photosystem II", "^psb", "#70a741"],
      ["pet", "Cytochrome b6f", "^pet", "#a4b341"], ["atp", "ATP synthase", "^atp", "#d5a533"],
      ["nad", "NADH dehydrogenase", "^(nad|ndh|nd[0-9])", "#dfc35b"],
      ["cox", "Respiratory chain", "^(cob|cytb|cyb$|cox|co[123i]|cyc)", "#cd7049"],
      ["rbc", "RuBisCO", "^rbc", "#358c9a"], ["rp", "Ribosomal proteins", "^(rps|rpl)", "#4479ac"],
      ["rpo", "RNA polymerase", "^rpo", "#9a7fb2"], ["rrna", "Ribosomal RNA", "^(rRNA$|rrn|rnl|rns)", "#c94e7b"],
      ["trna", "Transfer RNA", "^(tRNA|trn)", "#895ca5"],
      ["other", "Transport / other proteins", "^(sec|tat|ycf|ccm)", "#698e83"],
      ["orf", "Open reading frames", "^orf", "#969696"], ["unknown", "Other / unknown", ".*", "#c4c4c4"],
    ].map(([id, name, pattern, color]) => ({ id, name, pattern, color }));
    const DEFAULTS = {
      page: { width: 174, height: 0, margin: 5 }, font: "Arial", fontSize: 6, lineWidth: 0.45,
      shape: "circular", outside: "+", track: { height: 6 },
      types: { CDS: true, rRNA: true, tRNA: true, ORF: true, gene: false, other: false },
      labels: { show: true, size: 6, angle: 45, shift: 12, circular: "horizontal" },
      ticks: { show: true, step: 0 }, gc: { show: false, window: 500, mean: true },
      centre: { organism: "", length: "", gc: "", size: 9 },
      range: { from: 1, to: 0 }, legend: { show: true, position: "below" },
      classes, hiddenClasses: [], featureEdits: {}, regions: [],
    };
    // Overrides intentionally stop applying when a feature moves, changes type
    // or is renamed. File indices are not identities; reordering is harmless.
    const featureKey = f => `${f.type}|${f.name || ""}|${f.start}|${f.end}`;
    const typeOf = f => Object.hasOwn(DEFAULTS.types, f.type) && f.type !== "other" ? f.type : "other";
    const nameRuns = (f, edit) => markupRuns(edit.label || f.notes?.gene?.[0] || f.name || f.type);
    const geneName = f => f.notes?.gene?.[0] || f.name || f.type;
    function classOf(f, table) {
      for (const c of table) {
        try {
          const re = new RegExp(c.pattern, "i");
          if ([geneName(f), f.name, f.type].some(v => re.test(v || ""))) return c;
        } catch (_) { /* invalid patterns are reported in the settings panel */ }
      }
      return { id: "unknown", name: "Other / unknown", color: "#c4c4c4" };
    }
    function featureArcs(f, n) {
      if (!n) return [];
      const raw = f.locations?.length ? f.locations : [f], parts = [];
      for (const p of raw) {
        if (!Number.isFinite(p.start) || !Number.isFinite(p.end)) continue;
        const a = clamp(p.start, 0, n - 1), b = clamp(p.end, 0, n - 1);
        if (a <= b) parts.push({ start: a, end: b + 1 });
        else parts.push({ start: a, end: n }, { start: 0, end: b + 1 });
      }
      const start = clamp(f.start, 0, n - 1), terminal = f.strand === -1 ? start : clamp(f.end, 0, n - 1);
      return parts.sort((a, b) => ((a.start - start + n) % n) - ((b.start - start + n) % n)).map(p => ({ ...p,
        head: f.strand === -1 ? p.start === terminal && !f.partial?.left : p.end - 1 === terminal && !f.partial?.right,
        partialStart: !!f.partial?.left && p.start === start,
        partialEnd: !!f.partial?.right && p.end - 1 === clamp(f.end, 0, n - 1),
      }));
    }
    function tickMarks(n, from = 0, to = n, requested = 0) {
      const nice = x => {
        const power = 10 ** Math.floor(Math.log10(Math.max(x, 1)));
        return [1, 2, 5, 10].find(v => v * power >= x) * power;
      };
      const step = Math.max(1, Math.round(requested || nice((to - from) / 8)), nice((to - from) / 400));
      const minor = Math.max(1, step / 5), out = [];
      for (let p = Math.ceil(from / minor) * minor; p <= to + 1e-6; p += minor) {
        const major = Math.abs(p / step - Math.round(p / step)) < 1e-6;
        out.push({ position: p, major, label: major ? `${Number((p / 1000).toFixed(3))}` : "" });
      }
      return out;
    }
    function prepare(data) {
      const windows = window.SeqEdit.gcWindows(data.sequence || "", Math.max(1, data.sequence?.length || 1));
      return { ...data, features: (data.features || []).map(f => ({ ...f, key: featureKey(f) })),
        meanGC: windows[0]?.gc, gcCache: new Map(), stats: {} };
    }
    function gcData(source, size) {
      size = Math.max(1, Math.round(Number(size) || 500));
      if (!source.gcCache.has(size)) {
        source.gcCache.clear();
        source.gcCache.set(size, window.SeqEdit.gcWindows(source.sequence, size));
      }
      return source.gcCache.get(size);
    }
    function visible(s, source) {
      return source.features.filter(f => s.types[typeOf(f)]).map(f => ({ f, edit: s.featureEdits[f.key] || {}, c: classOf(f, s.classes) }))
        .filter(x => !x.edit.hidden && !s.hiddenClasses.includes(x.c.id));
    }
    function centreLines(s, source) {
      return [
        { key: "organism", runs: s.centre.organism ? markupRuns(s.centre.organism) : source.organism ? speciesRuns(source.organism) : markupRuns(source.name || "Genome") },
        { key: "length", runs: markupRuns(s.centre.length || `${source.sequence.length.toLocaleString("en-US")} bp`) },
        { key: "gc", runs: markupRuns(s.centre.gc || (source.meanGC == null ? "GC: not available" : `${(source.meanGC * 100).toFixed(1)}% GC`)) },
      ];
    }
    // Separate text nodes keep rotated mixed italics correct in svglib/PDF.
    function placed(runs, x, y, size, font, anchor = "start", attrs = "") {
      let at = anchor === "end" ? -runsWidth(runs, size, font) : anchor === "middle" ? -runsWidth(runs, size, font) / 2 : 0;
      return `<g transform="translate(${num(x)},${num(y)})" ${attrs}>` + runs.map(r => {
        const lead = r.text.length - r.text.trimStart().length;
        const px = at + textWidth(r.text.slice(0, lead), size, font, r.italic, r.bold);
        at += textWidth(r.text, size, font, r.italic, r.bold);
        return r.text.trim() ? `<text x="${num(px)}" y="0" font-family="${esc(font)}" font-size="${num(size)}"${r.italic ? ' font-style="italic"' : ""}${r.bold ? ' font-weight="bold"' : ""}>${esc(r.text.trim())}</text>` : "";
      }).join("") + "</g>";
    }
    function wrap(runs, width, size, font) {
      const lines = [[]];
      for (const r of runs) for (const text of r.text.split(/(\s+)/).filter(Boolean)) {
        const next = { ...r, text };
        let line = lines.at(-1);
        if (text.trim() && line.length && runsWidth([...line, next], size, font) > width) { lines.push([]); line = lines.at(-1); }
        if (line.length || text.trim()) line.push(next);
      }
      return lines;
    }
    function wrapName(runs, width, size, font) {
      return wrap(runs, width, size, font).flatMap(line => {
        if (runsWidth(line, size, font) <= width) return [line];
        const result = [[]]; let measured = 0;
        for (const run of line) for (const char of run.text) {
          const w = textWidth(char, size, font, run.italic, run.bold);
          if (measured + w > width && result.at(-1).length) { result.push([]); measured = 0; }
          const last = result.at(-1).at(-1);
          if (last && last.italic === run.italic && last.bold === run.bold) last.text += char;
          else result.at(-1).push({ ...run, text: char });
          measured += w;
        }
        return result;
      });
    }
    // Oriented rectangles, rather than axis-aligned bounds, allow radial labels
    // to fan apart naturally. A greedy pass shifts along the ring, then outwards.
    function labelBox(x, y, angle, width, size, anchor) {
      const left = anchor === "end" ? -width : 0;
      return [[left, -size * .85], [left + width, -size * .85], [left + width, size * .25], [left, size * .25]]
        .map(([a, b]) => [x + a * Math.cos(angle) - b * Math.sin(angle), y + a * Math.sin(angle) + b * Math.cos(angle)]);
    }
    function overlaps(a, b) {
      for (const poly of [a, b]) for (let i = 0; i < 4; i++) {
        const p = poly[i], q = poly[(i + 1) % 4], axis = [p[1] - q[1], q[0] - p[0]];
        const aa = a.map(v => v[0] * axis[0] + v[1] * axis[1]), bb = b.map(v => v[0] * axis[0] + v[1] * axis[1]);
        if (Math.max(...aa) < Math.min(...bb) || Math.max(...bb) < Math.min(...aa)) return false;
      }
      return true;
    }
    function fits(box, bounds, occupied) {
      return box.every(([x, y]) => x >= bounds[0] && x <= bounds[2] && y >= bounds[1] && y <= bounds[3])
        && !occupied.some(b => overlaps(box, b));
    }
    const coords = (cx, cy, r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    const point = p => p.map(num).join(",");
    function arc(cx, cy, r, a, b) {
      const count = Math.max(1, Math.ceil(Math.abs(b - a) / Math.PI)), step = (b - a) / count;
      return Array.from({ length: count }, (_, i) => `A${num(r)},${num(r)} 0 0 ${b > a ? 1 : 0} ${point(coords(cx, cy, r, a + step * (i + 1)))}`).join(" ");
    }
    function sector(cx, cy, r0, r1, a, b, head = 0) {
      const tip = Math.min(Math.abs(b - a) * .6, 5 / Math.max(1, (r0 + r1) / 2));
      if (head > 0) return `M${point(coords(cx, cy, r1, a))} ${arc(cx, cy, r1, a, b - tip)} L${point(coords(cx, cy, (r0 + r1) / 2, b))} L${point(coords(cx, cy, r0, b - tip))} ${arc(cx, cy, r0, b - tip, a)} Z`;
      if (head < 0) return `M${point(coords(cx, cy, r1, a + tip))} ${arc(cx, cy, r1, a + tip, b)} L${point(coords(cx, cy, r0, b))} ${arc(cx, cy, r0, b, a + tip)} L${point(coords(cx, cy, (r0 + r1) / 2, a))} Z`;
      return `M${point(coords(cx, cy, r1, a))} ${arc(cx, cy, r1, a, b)} L${point(coords(cx, cy, r0, b))} ${arc(cx, cy, r0, b, a)} Z`;
    }
    const regionParts = (r, n) => Number(r.start) <= Number(r.end) ? [[clamp(r.start, 1, n) - 1, clamp(r.end, 1, n)]]
      : [[clamp(r.start, 1, n) - 1, n], [0, clamp(r.end, 1, n)]];
    function legendPlan(s, used, width) {
      if (!s.legend.show || !used.length) return { items: [], height: 0 };
      const size = s.fontSize, cell = Math.min(width, Math.max(95, ...used.map(c => textWidth(c.name, size, s.font) + 18)));
      const columns = s.legend.position === "right" ? 1 : Math.max(1, Math.floor(width / cell));
      const cellWidth = width / columns;
      const lines = used.map(c => wrap([{ text: c.name }], cellWidth - 17, size, s.font));
      const rowHeight = Math.max(...lines.map(l => l.length)) * size * 1.25 + 6;
      return { height: Math.ceil(used.length / columns) * rowHeight + 10,
        items: used.map((c, i) => ({ c, lines: lines[i], x: (i % columns) * cellWidth, y: Math.floor(i / columns) * rowHeight + size })) };
    }
    function drawLegend(s, plan, x, y) {
      return plan.items.map(({ c, lines, x: dx, y: dy }) => `<g data-class="${esc(c.id)}" cursor="pointer"><rect x="${num(x + dx)}" y="${num(y + dy - s.fontSize * .8)}" width="8" height="${num(s.fontSize)}" fill="${esc(c.color || "#bbb")}" stroke="#555" stroke-width=".25"/>` +
        lines.map((line, i) => placed(line, x + dx + 13, y + dy + i * s.fontSize * 1.25, s.fontSize, s.font)).join("") + "</g>").join("");
    }

    function drawCircular(s, source, shown, used, selection) {
      const W = clamp(s.page.width, 30, 400) * PT_PER_MM, m = clamp(s.page.margin, 1, 30) * PT_PER_MM;
      const beside = s.legend.show && s.legend.position === "right", legendWidth = beside ? W * .32 : W - m * 2;
      const D = W - m * 2 - (beside ? legendWidth + 10 : 0), cx = m + D / 2;
      const fs = clamp(s.labels.size, 3, 18), horizontal = s.labels.circular !== "radial", columns = s.labels.circular === "columns";
      const th = clamp(s.track.height, 2, 16), n = source.sequence.length, angle = p => p / n * TAU - Math.PI / 2;
      const labelJobs = s.labels.show ? shown.filter(e => !e.edit.hideLabel).flatMap(({ f, edit }) => {
        const parts = featureArcs(f, n); if (!parts.length) return [];
        let left = parts.reduce((sum, p) => sum + p.end - p.start, 0) / 2, middle = parts[0].start;
        for (const p of parts) { if (left <= p.end - p.start) { middle = p.start + left; break; } left -= p.end - p.start; }
        return [{ f, edit, a: angle(middle % n), runs: nameRuns(f, edit) }];
      }) : [];
      const longest = Math.max(0, ...labelJobs.map(j => runsWidth(j.runs, fs, s.font)));
      const sideWidth = Math.min(Math.max(28, longest), D * .27);
      const reserve = s.labels.show ? horizontal ? sideWidth + 12 : Math.max(22, Math.min(longest + 14, D * .28)) : 12;
      const R = Math.max(18, D / 2 - reserve - (s.regions.length ? 20 : 10));
      const sides = [[], []], lineHeight = fs * 1.2, gap = Math.max(1, fs * .22);
      for (const j of labelJobs) {
        j.lines = wrapName(j.runs, sideWidth, fs, s.font); j.height = j.lines.length * lineHeight;
        sides[Math.cos(j.a) < 0 ? 0 : 1].push(j);
      }
      const contentH = columns ? Math.max(D, ...sides.map(side => side.reduce((h, j) => h + j.height + gap, 8))) : D;
      const cy = m + contentH / 2, out = [], occupied = [], regionLabels = [];
      const legend = legendPlan(s, used, legendWidth), H = Math.max(s.page.height * PT_PER_MM, contentH + m * 2 + (s.gc.show ? 20 : 0) + (beside ? 0 : legend.height + 6));
      out.push(`<circle cx="${num(cx)}" cy="${num(cy)}" r="${num(R)}" fill="none" stroke="#666" stroke-width="${num(s.lineWidth)}"/>`);
      for (const { f, edit, c } of shown) {
        const radius = R + ((f.strand === -1) === (s.outside === "-") ? 1 : -1) * (th / 2 + 1);
        const parts = featureArcs(f, n), color = edit.color || c.color || "#bbb";
        out.push(`<g data-feature="${esc(f.key)}" data-role="feature" data-strand="${f.strand}" cursor="pointer"><title>${esc(`${f.type} ${f.name || ""}`)}</title>`);
        for (let i = 0; i < parts.length; i++) {
          const p = parts[i], a = angle(p.start), b = angle(p.end);
          if (i) {
            const prev = parts[i - 1]; let end = p.start; if (end < prev.end) end += n;
            out.push(`<path data-role="connector" d="M${point(coords(cx, cy, radius, angle(prev.end)))} ${arc(cx, cy, radius, angle(prev.end), angle(end))}" fill="none" stroke="${esc(color)}" stroke-width="${num(s.lineWidth)}"/>`);
          }
          out.push(`<path data-part="${i}" data-arrow="${p.head}" d="${sector(cx, cy, radius - th / 2, radius + th / 2, a, b, p.head ? f.strand : 0)}" fill="${esc(color)}" stroke="#555" stroke-width="${num(s.lineWidth)}"/>`);
          for (const boundary of [p.partialStart ? a : null, p.partialEnd ? b : null].filter(v => v !== null))
            out.push(`<path data-role="partial" d="M${point(coords(cx, cy, radius - th / 2, boundary))} L${point(coords(cx, cy, radius + th / 2, boundary))}" stroke="#fff" stroke-width="${num(s.lineWidth + .8)}"/>`);
        }
        if (selection?.type === "feature" && selection.key === f.key)
          for (const p of parts) out.push(`<path class="fig-sel" d="${sector(cx, cy, radius - th / 2 - 2, radius + th / 2 + 2, angle(p.start), angle(p.end))}" fill="none" stroke="#276bba" stroke-dasharray="2 2" stroke-width=".7"/>`);
        out.push("</g>");

      }
      for (const r of s.regions) {
        for (const [a, b] of regionParts(r, n)) out.push(`<path data-region="${esc(r.id)}" d="M${point(coords(cx, cy, R + th + 7, angle(a)))} ${arc(cx, cy, R + th + 7, angle(a), angle(b))}" fill="none" stroke="${esc(r.color || "#487591")}" stroke-width="2"/>`);
        const span = (Number(r.end) - Number(r.start) + 1 + n) % n || n, a = angle((Number(r.start) - 1 + span / 2) % n);
        const [x, y] = coords(cx, cy, R + th + 12, a);
        const size = Math.max(4, fs - 1), runs = markupRuns(r.name || "Region"), width = runsWidth(runs, size, s.font);
        const box = labelBox(x - width / 2, y, 0, width, size + 2, "start");
        occupied.push(box);
        regionLabels.push({ r, x, y, size, runs, width });
      }
      if (s.ticks.show) for (const t of tickMarks(n, 0, n, s.ticks.step)) {
        if (t.position >= n) continue;
        const a = angle(t.position), p = coords(cx, cy, R - th - 3, a), q = coords(cx, cy, R - th - (t.major ? 8 : 5), a);
        out.push(`<path d="M${point(p)} L${point(q)}" stroke="#777" stroke-width=".4"/>`);
        if (t.major) { const [x, y] = coords(cx, cy, R - th - 15, a); out.push(placed([{ text: t.label }], x, y + 1.5, 4.5, s.font, "middle")); }
      }
      if (s.ticks.show) out.push(placed([{ text: "kb" }], cx, cy - R + th + 24, 4.5, s.font, "middle"));
      const gcR = Math.max(8, R - th - 43), gcH = Math.min(17, gcR * .22);
      if (s.gc.show) {
        out.push(`<path d="${sector(cx, cy, gcR, gcR + gcH, -Math.PI / 2, TAU - Math.PI / 2)}" fill="#f0f1f2"/>`);
        for (const w of gcData(source, s.gc.window)) if (w.gc !== null && w.gc > 0)
          out.push(`<path data-role="gc-window" d="${sector(cx, cy, gcR + Math.min(source.meanGC ?? 0, w.gc) * gcH, gcR + Math.max(source.meanGC ?? 0, w.gc) * gcH, angle(w.start), angle(w.end))}" fill="${w.gc >= (source.meanGC ?? 0) ? "#426c80" : "#9aaab3"}"/>`);
        if (s.gc.mean && source.meanGC != null) out.push(`<circle data-role="gc-mean" cx="${num(cx)}" cy="${num(cy)}" r="${num(gcR + source.meanGC * gcH)}" fill="none" stroke="#355963" stroke-width=".5" stroke-dasharray="2 2"/>`);
      }
      // Centre text is wrapped using real measured runs, preserving species italics.
      const centreSize = clamp(s.centre.size, 4, 24), centreW = Math.max(25, (s.gc.show ? gcR : R - th - 24) * 1.5);
      const lines = centreLines(s, source).flatMap(line => wrap(line.runs, centreW, centreSize, s.font).map(runs => ({ ...line, runs })));
      lines.forEach((line, i) => out.push(placed(line.runs, cx, cy + (i - (lines.length - 1) / 2) * centreSize * 1.45,
        centreSize, s.font, "middle", `data-centre="${line.key}" cursor="pointer"`)));
      const leaderStart = job => {
        const outside = (job.f.strand === -1) === (s.outside === "-");
        // The outward edge of this feature, including features on the inner lane.
        return coords(cx, cy, R + (outside ? th + 1 : -1), job.a);
      };
      let omitted = 0;
      if (columns) {
        for (const [sideIndex, side] of sides.entries()) {
          const left = sideIndex === 0, anchor = left ? "end" : "start";
          const x = cx + (left ? -1 : 1) * (R + th + (s.regions.length ? 23 : 12));
          side.sort((a, b) => Math.sin(a.a) - Math.sin(b.a));
          let edge = m + 4;
          for (const j of side) {
            const desired = cy + Math.sin(j.a + (Number(j.edit.angle) || 0) * DEG) * (R + th + 8) - j.height / 2 + (Number(j.edit.radial) || 0);
            j.y = Math.max(edge, desired); edge = j.y + j.height + gap;
          }
          edge = m + contentH - 4;
          for (const j of [...side].reverse()) { j.y = Math.min(j.y, edge - j.height); edge = j.y - gap; }
          for (const j of side) {
            const width = Math.max(...j.lines.map(line => runsWidth(line, fs, s.font))), x0 = left ? x - width : x;
            const box = [[x0, j.y], [x0 + width, j.y], [x0 + width, j.y + j.height], [x0, j.y + j.height]];
            if (!fits(box, [m, m, m + D, m + contentH], occupied)) { omitted++; continue; }
            occupied.push(box);
            const start = leaderStart(j), targetY = j.y + j.height / 2;
            const endX = x + (left ? 3 : -3), bendX = x + (left ? 7 : -7);
            out.push(`<g data-feature="${esc(j.f.key)}" data-role="label" data-orientation="horizontal" cursor="pointer"><path data-role="leader" d="M${point(start)} L${num(bendX)},${num(targetY)} H${num(endX)}" fill="none" stroke="#a6adb2" stroke-width=".3"/>`);
            j.lines.forEach((line, i) => out.push(placed(line, x, j.y + fs * .85 + i * lineHeight, fs, s.font, anchor)));
            out.push("</g>");
          }
        }
      } else {
        const ordered = labelJobs.sort((a, b) => a.a - b.a);
        for (const [index, job] of ordered.entries()) {
          const width = runsWidth(job.runs, fs, s.font), base = R + th + (s.regions.length ? 23 : 10);
          const before = ordered[(index + ordered.length - 1) % ordered.length].a - (index === 0 ? TAU : 0);
          const after = ordered[(index + 1) % ordered.length].a + (index === ordered.length - 1 ? TAU : 0);
          // Each name stays in its own angular neighbourhood. A forward greedy
          // sweep used to push every later name clockwise to follow the previous one.
          const limit = clamp(s.labels.shift, 0, 45) * DEG;
          const low = Math.max(-limit, (before - job.a) / 2), high = Math.min(limit, (after - job.a) / 2);
          const step = Math.max(.75 * DEG, (fs + 1) / Math.max(1, base));
          const offsets = [0, low, high];
          for (let d = step; d <= limit; d += step) { if (-d > low) offsets.push(-d); if (d < high) offsets.push(d); }
          const candidates = offsets.flatMap(offset => [0, 8, 16, 24, 32, 40, 48].map(extra => ({ offset, extra,
            cost: extra * extra + (base * offset) ** 2 }))).sort((a, b) => a.cost - b.cost);
          let placedLabel = false;
          for (const { offset, extra } of candidates) {
            const a = job.a + offset + (Number(job.edit.angle) || 0) * DEG, left = Math.cos(a) < 0;
            const r = base + extra + (Number(job.edit.radial) || 0), [x, y] = coords(cx, cy, r, a);
            const rotation = horizontal ? 0 : a + (left ? Math.PI : 0), anchor = left ? "end" : "start";
            const box = labelBox(x, y, rotation, width, fs + 1, anchor);
            if (!fits(box, [m, m, m + D, m + D], occupied)) continue;
            occupied.push(box);
            const start = leaderStart(job);
            out.push(`<g data-feature="${esc(job.f.key)}" data-role="label" data-orientation="${horizontal ? 'horizontal' : 'radial'}" cursor="pointer">`);
            out.push(`<path data-role="leader" data-angle-offset="${num(offset / DEG)}" d="M${point(start)} L${num(x)},${num(y)}" fill="none" stroke="#999" stroke-width=".3"/>`);
            out.push(`<g transform="translate(${num(x)},${num(y)}) rotate(${num(rotation / DEG)})">${placed(job.runs, 0, 0, fs, s.font, anchor)}</g></g>`);
            placedLabel = true; break;
          }
          if (!placedLabel) omitted++;
        }
      }
      for (const { r, x, y, size, runs, width } of regionLabels) {
        out.push(`<g data-region="${esc(r.id)}" cursor="pointer"><rect x="${num(x - width / 2 - 1)}" y="${num(y - size)}" width="${num(width + 2)}" height="${num(size * 1.3)}" fill="#fff"/>${placed(runs, x, y, size, s.font, "middle")}</g>`);
      }
      if (s.gc.show) {
        out.push(placed([{ text: `GC content · 0–100% · genome mean ${source.meanGC == null ? 'not available' : (source.meanGC * 100).toFixed(1) + '%'}` }], m, m + contentH + 5, 5, s.font));
        out.push(placed([{ text: 'Outward: above mean · inward: below mean' }], m, m + contentH + 12, 5, s.font));
      }
      out.push(drawLegend(s, legend, beside ? m + D + 10 : m, beside ? m : m + contentH + (s.gc.show ? 26 : 6)));
      return { body: out.join("\n"), width: W, height: Math.max(H, beside ? m * 2 + legend.height : H), omittedLabels: omitted,
        namedFeatures: labelJobs.length, placedLabels: labelJobs.length - omitted, labelBoxes: occupied.slice(regionLabels.length) };
    }

    function drawLinear(s, source, shown, used, selection) {
      const n = source.sequence.length, W = clamp(s.page.width, 30, 400) * PT_PER_MM, m = clamp(s.page.margin, 1, 30) * PT_PER_MM;
      const from = clamp(s.range.from || 1, 1, n) - 1, to = clamp(s.range.to || n, from + 1, n), span = to - from;
      const x = p => m + (p - from) / span * (W - 2 * m), fs = clamp(s.labels.size, 3, 18), th = clamp(s.track.height, 2, 16);
      const features = shown.map(entry => ({ ...entry, parts: featureArcs(entry.f, n).filter(p => p.end > from && p.start < to) })).filter(e => e.parts.length);
      const laneEnds = { "+": [], "-": [] };
      for (const entry of [...features].sort((a, b) => Math.min(...a.parts.map(p => p.start)) - Math.min(...b.parts.map(p => p.start)))) {
        const way = entry.f.strand === -1 ? "-" : "+", a = Math.max(from, Math.min(...entry.parts.map(p => p.start))), b = Math.min(to, Math.max(...entry.parts.map(p => p.end)));
        let lane = laneEnds[way].findIndex(end => end <= a);
        if (lane < 0) lane = laneEnds[way].length;
        laneEnds[way][lane] = b; entry.lane = lane;
      }
      const pitch = th + 6, plus = Math.max(1, laneEnds["+"].length), minus = Math.max(1, laneEnds["-"].length);
      const centreSize = clamp(s.centre.size, 4, 24), header = centreLines(s, source), out = [];
      let headerY = m + centreSize;
      for (const line of header) for (const runs of wrap(line.runs, W - 2 * m, centreSize, s.font)) {
        out.push(placed(runs, W / 2, headerY, centreSize, s.font, "middle", `data-centre="${line.key}"`)); headerY += centreSize * 1.4;
      }
      const slant = clamp(s.labels.angle, 0, 90) * DEG;
      const maxWidth = Math.max(0, ...features.map(e => runsWidth(nameRuns(e.f, e.edit), fs, s.font)));
      const nameH = s.labels.show ? Math.min(160, maxWidth * Math.sin(slant) + fs * 5) : 8;
      const top = headerY + (s.regions.length ? 25 : 0), axis = top + nameH + plus * pitch + 8;
      const bottomNames = axis + 14 + (minus - 1) * pitch + nameH + fs * 3;
      let gcY, legendY;
      const legend = legendPlan({ ...s, legend: { ...s.legend, position: "below" } }, used, W - 2 * m);
      const mapStart = out.length; out.push('<g data-role="linear-map">');
      out.push(`<path d="M${num(m)},${num(axis)} H${num(W - m)}" stroke="#666" stroke-width="${num(s.lineWidth)}"/>`);
      if (s.ticks.show) for (const t of tickMarks(n, from, to, s.ticks.step)) {
        const at = x(t.position);
        out.push(`<path d="M${num(at)},${num(axis - 2)} v${t.major ? 6 : 4}" stroke="#666" stroke-width=".4"/>`);
        if (t.major) out.push(placed([{ text: t.label }], at, axis + 10, 4.5, s.font, "middle"));
      }
      if (s.ticks.show) out.push(placed([{ text: "kb" }], W - m, axis - 3, 4.5, s.font, "end"));
      const occupied = []; let omitted = 0, named = 0;
      for (const { f, edit, c, parts, lane } of features) {
        const way = f.strand === -1 ? 1 : -1, y = axis + way * (17 + lane * pitch), color = edit.color || c.color || "#bbb";
        out.push(`<g data-feature="${esc(f.key)}" data-role="feature" data-strand="${f.strand}" cursor="pointer"><title>${esc(`${f.type} ${f.name || ""}`)}</title>`);
        const ordered = parts;
        for (let i = 0; i < ordered.length; i++) {
          const p = ordered[i], a = x(Math.max(from, p.start)), b = x(Math.min(to, p.end)), tip = Math.min(5, (b - a) * .6);
          const head = p.head && (f.strand === -1 ? p.start >= from : p.end <= to);
          let points;
          if (head && f.strand === -1) points = [[a, y], [a + tip, y - th / 2], [b, y - th / 2], [b, y + th / 2], [a + tip, y + th / 2]];
          else if (head) points = [[a, y - th / 2], [b - tip, y - th / 2], [b, y], [b - tip, y + th / 2], [a, y + th / 2]];
          else points = [[a, y - th / 2], [b, y - th / 2], [b, y + th / 2], [a, y + th / 2]];
          if (i && ordered[i - 1].end <= p.start) out.push(`<path data-role="connector" d="M${num(x(Math.min(to, ordered[i - 1].end)))},${num(y)} H${num(a)}" stroke="${esc(color)}" stroke-width="${num(s.lineWidth)}"/>`);
          out.push(`<polygon data-part="${i}" data-arrow="${head}" points="${points.map(point).join(" ")}" fill="${esc(color)}" stroke="#555" stroke-width="${num(s.lineWidth)}"/>`);
          for (const boundary of [p.partialStart && p.start >= from ? a : null, p.partialEnd && p.end <= to ? b : null].filter(v => v !== null))
            out.push(`<path data-role="partial" d="M${num(boundary)},${num(y - th / 2)} v${num(th)}" stroke="#fff" stroke-width="${num(s.lineWidth + .8)}"/>`);
          if (selection?.type === "feature" && selection.key === f.key) out.push(`<rect class="fig-sel" x="${num(a - 1)}" y="${num(y - th / 2 - 2)}" width="${num(b - a + 2)}" height="${num(th + 4)}" fill="none" stroke="#276bba" stroke-dasharray="2 2"/>`);
        }
        out.push("</g>");
        if (!s.labels.show || edit.hideLabel) continue;
        named++;
        const runs = nameRuns(f, edit), width = runsWidth(runs, fs, s.font);
        const mid = (Math.max(from, Math.min(...parts.map(p => p.start))) + Math.min(to, Math.max(...parts.map(p => p.end)))) / 2;
        const at = x(mid), left = at + width * Math.cos(slant) > W - m, anchor = left ? "end" : "start";
        const rotation = (left ? -1 : 1) * way * slant;
        let done = false;
        for (let tier = 0; tier < 8 && !done; tier++) for (const dx of [0, 3, -3, 6, -6, 9, -9]) {
          const yy = (way < 0 ? axis - 17 - (plus - 1) * pitch - th / 2 - 3 : axis + 17 + (minus - 1) * pitch + th / 2 + fs * .85 + 3) + way * tier * (fs + 3) + (Number(edit.radial) || 0);
          const xx = at + dx + (Number(edit.angle) || 0), box = labelBox(xx, yy, rotation, width, fs + 1, anchor);
          if (!fits(box, [m, top, W - m, bottomNames], occupied)) continue;
          occupied.push(box);
          out.push(`<g data-feature="${esc(f.key)}" data-role="label"><path d="M${num(at)},${num(y + way * th / 2)} L${num(xx)},${num(yy)}" stroke="#999" stroke-width=".3"/><g transform="translate(${num(xx)},${num(yy)}) rotate(${num(rotation / DEG)})">${placed(runs, 0, 0, fs, s.font, anchor)}</g></g>`);
          done = true; break;
        }
        if (!done) omitted++;
      }
      // Trim unused name space instead of reserving the longest name on both sides.
      const topInk = Math.min(axis - 17 - (plus - 1) * pitch - th / 2, ...occupied.flatMap(box => box.map(p => p[1])));
      const bottomInk = Math.max(axis + 17 + (minus - 1) * pitch + th / 2, ...occupied.flatMap(box => box.map(p => p[1])));
      const shiftY = top - topInk;
      out[mapStart] = `<g data-role="linear-map" transform="translate(0,${num(shiftY)})">`;
      out.push('</g>');
      gcY = bottomInk + shiftY + 16; legendY = (s.gc.show ? gcY + 34 : bottomInk + shiftY) + 10;
      for (const r of s.regions) for (const [a, b] of regionParts(r, n)) {
        if (b <= from || a >= to) continue;
        const x0 = x(Math.max(from, a)), x1 = x(Math.min(to, b)), y = headerY + 14;
        out.push(`<g data-region="${esc(r.id)}"><path d="M${num(x0)},${num(y)} H${num(x1)}" stroke="${esc(r.color || "#487591")}" stroke-width="2"/>${placed(markupRuns(r.name || "Region"), (x0 + x1) / 2, y - 4, fs, s.font, "middle")}</g>`);
      }
      if (s.gc.show) {
        out.push(placed([{ text: `GC content · 0–100% · Up: above genome mean (${source.meanGC == null ? 'not available' : (source.meanGC * 100).toFixed(1) + '%'}) · Down: below mean` }], m, gcY - 4, 5, s.font));
        out.push(`<rect x="${num(m)}" y="${num(gcY)}" width="${num(W - 2 * m)}" height="20" fill="#f0f1f2"/>`);
        for (const w of gcData(source, s.gc.window)) if (w.gc !== null && w.end > from && w.start < to)
          out.push(`<rect data-role="gc-window" x="${num(x(Math.max(from, w.start)))}" y="${num(gcY + 20 - Math.max(source.meanGC ?? 0, w.gc) * 20)}" width="${num(x(Math.min(to, w.end)) - x(Math.max(from, w.start)))}" height="${num(Math.abs(w.gc - (source.meanGC ?? 0)) * 20)}" fill="${w.gc >= (source.meanGC ?? 0) ? "#426c80" : "#9aaab3"}"/>`);
        if (s.gc.mean && source.meanGC != null) out.push(`<path data-role="gc-mean" d="M${num(m)},${num(gcY + 20 - source.meanGC * 20)} H${num(W - m)}" stroke="#355963" stroke-width=".5" stroke-dasharray="2 2"/>`);
      }
      out.push(drawLegend(s, legend, m, legendY));
      return { body: out.join("\n"), width: W, height: Math.max(s.page.height * PT_PER_MM, legendY + legend.height + m),
        omittedLabels: omitted, namedFeatures: named, placedLabels: named - omitted, labelBoxes: occupied.map(box => box.map(([x, y]) => [x, y + shiftY])) };
    }

    // Table controls use the editor's change callback, so each row edit, reorder
    // or button click is one ordinary figure undo step and one debounced save.
    const newId = prefix => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
    function classesControl(s, change) {
      const box = document.createElement("div"); box.className = "fig-genome-table";
      box.innerHTML = s.classes.map((c, i) => `<div class="fig-genome-class" data-row="${i}">
        <input autocomplete="off" aria-label="Class name" data-field="name" value="${esc(c.name)}">
        <input type="color" aria-label="Class colour" data-field="color" value="${esc(c.color || "#bbbbbb")}">
        <button type="button" data-command="up" title="Higher priority">↑</button><button type="button" data-command="down" title="Lower priority">↓</button>
        <button type="button" data-command="remove" title="Remove class">×</button>
        <input autocomplete="off" class="fig-genome-pattern" aria-label="Gene pattern" data-field="pattern" value="${esc(c.pattern)}">
      </div>`).join("") + '<button type="button" data-command="add">Add class</button>';
      box.onchange = e => {
        const row = e.target.closest("[data-row]"); if (!row) return;
        change(st => { st.classes[Number(row.dataset.row)][e.target.dataset.field] = e.target.value; });
      };
      box.onclick = e => {
        const command = e.target.dataset.command; if (!command) return;
        const i = Number(e.target.closest("[data-row]")?.dataset.row);
        change(st => {
          if (command === "add") st.classes.splice(Math.max(0, st.classes.length - 1), 0, { id: newId("class"), name: "New class", pattern: "^newgene", color: "#4889a0" });
          if (command === "remove" && st.classes.length > 1) st.classes.splice(i, 1);
          const j = command === "up" ? i - 1 : command === "down" ? i + 1 : -1;
          if (j >= 0 && j < st.classes.length) [st.classes[i], st.classes[j]] = [st.classes[j], st.classes[i]];
        });
      };
      return box;
    }
    function regionsControl(s, source, change) {
      const box = document.createElement("div"); box.className = "fig-genome-table";
      box.innerHTML = s.regions.map((r, i) => `<div data-row="${i}" class="fig-genome-region">
        <input autocomplete="off" aria-label="Region name" data-field="name" value="${esc(r.name)}">
        <input type="color" aria-label="Region colour" data-field="color" value="${esc(r.color || "#487591")}">
        <button type="button" data-command="remove">×</button>
        <label>From (bp)<input type="number" min="1" max="${source.sequence.length}" data-field="start" value="${r.start}"></label>
        <label>To (bp)<input type="number" min="1" max="${source.sequence.length}" data-field="end" value="${r.end}"></label></div>`).join("") + '<button type="button" data-command="add">Add region</button>';
      box.onchange = e => {
        const i = Number(e.target.closest("[data-row]")?.dataset.row), key = e.target.dataset.field;
        if (!key || !Number.isInteger(i)) return;
        change(st => { st.regions[i][key] = ["start", "end"].includes(key) ? Math.round(clamp(e.target.value, 1, source.sequence.length)) : e.target.value; });
      };
      box.onclick = e => {
        const command = e.target.dataset.command; if (!command) return;
        change(st => {
          if (command === "add") st.regions.push({ id: newId("region"), name: "LSC", start: 1, end: source.sequence.length, color: "#487591" });
          else st.regions.splice(Number(e.target.closest("[data-row]").dataset.row), 1);
        });
      };
      return box;
    }
    const number = (path, label, min, max, step = 1) => ({ path, label, min, max, step, type: "number" });
    const check = (path, label) => ({ path, label, type: "check" });
    const text = (path, label) => ({ path, label, type: "text" });
    const kind = {
      defaults: DEFAULTS, prepare, initial: source => ({ shape: source.circular ? "circular" : "linear" }),
      hint: "click a gene, centre text, legend class or region to set it",
      select(el) {
        if (el.dataset.feature !== undefined) return { type: "feature", key: el.dataset.feature };
        if (el.dataset.centre !== undefined) return { type: "centre", key: el.dataset.centre };
        if (el.dataset.class !== undefined) return { type: "class", key: el.dataset.class };
        if (el.dataset.region !== undefined) return { type: "region", key: el.dataset.region };
        return null;
      },
      action(name, s, sel, source) {
        if (name === "singleColumn") s.page.width = 84;
        if (name === "fullWidth") s.page.width = 174;
        if (name === "resetFeature") delete s.featureEdits[sel.key];
        if (name === "hideFeature") (s.featureEdits[sel.key] ||= {}).hidden = true;
        if (name === "unhide") { s.hiddenClasses = []; for (const e of Object.values(s.featureEdits)) delete e.hidden; }
        if (name === "hideClass") {
          const f = source.features.find(f => f.key === sel.key), id = sel.type === "class" ? sel.key : f && classOf(f, s.classes).id;
          if (id && !s.hiddenClasses.includes(id)) s.hiddenClasses.push(id);
        }
        return ["hideFeature", "hideClass", "resetFeature"].includes(name) ? null : undefined;
      },
      panel(s, sel, source) {
        const bad = s.classes.filter(c => { try { new RegExp(c.pattern, "i"); return false; } catch (_) { return true; } });
        const sections = [
          { title: "Page", fields: [number(["page", "width"], "Width (mm)", 30, 400),
            { type: "note", label: "Journals: about 84 mm for one column, 174 mm for full width. Height grows to fit the drawing." },
            { type: "button", label: "84 mm", action: "singleColumn" }, { type: "button", label: "174 mm", action: "fullWidth" },
            number(["page", "height"], "Minimum height (mm, 0 = as needed)", 0, 600),
            { path: ["font"], label: "Font", type: "select", options: FONTS.map(f => [f, f]) },
            number(["fontSize"], "Legend size (pt)", 3, 18, .5), number(["lineWidth"], "Line width (pt)", .1, 3, .05) ] },
          { title: "Genome map", fields: [
            { path: ["shape"], label: "Layout", type: "select", options: [["circular", "Circular"], ["linear", "Linear"]] },
            { path: ["outside"], label: "Outside strand (circular)", type: "select", options: [["+", "+ strand"], ["-", "− strand"]] },
            number(["track", "height"], "Gene arrow height (pt)", 2, 16, .5),
            ...["CDS", "rRNA", "tRNA", "ORF", "gene", "other"].map(t => check(["types", t], `Show ${t === "other" ? "other features" : t}`)),
            ...(s.shape === "linear" ? [number(["range", "from"], "From (bp, 1-based)", 1, source.sequence.length), number(["range", "to"], "To (bp, 0 = end)", 0, source.sequence.length)] : []),
            ...(s.shape === "circular" && !source.circular ? [{ type: "note", label: "This sequence is linear; the circular layout does not join its ends or change its topology." }] : []),
          ] },
          { title: "Gene names", fields: [check(["labels", "show"], "Show names"), number(["labels", "size"], "Name size (pt)", 3, 18, .5),
            ...(s.shape === "linear" ? [number(["labels", "angle"], "Label slant (degrees)", 0, 90, 5)] : [
              { path: ["labels", "circular"], label: "Circular names", type: "select", options: [["horizontal", "Upright · compact"], ["columns", "Upright · all names"], ["radial", "Radial"]] },
              ...(s.labels.circular !== "columns" ? [number(["labels", "shift"], "Label shift allowed (degrees)", 0, 45)] : [{ type: "note", label: "All names are arranged in two columns. Dense maps grow taller; use Compact for a circular page." }])]),
            { type: "note", label: `${source.stats.omittedLabels || 0} names not shown because they cannot fit. Reduce the name size, enlarge the page, or hide unneeded names.` },
            { type: "button", action: "unhide", label: "Show hidden genes and classes" } ] },
          { title: "Scale and GC", fields: [check(["ticks", "show"], "Ticks and kb scale"), number(["ticks", "step"], "Major tick interval (bp, 0 = automatic)", 0, source.sequence.length),
            check(["gc", "show"], "GC content track"), number(["gc", "window"], "GC window (bp)", 1, Math.max(1, source.sequence.length)),
            check(["gc", "mean"], "Mean GC line"), { type: "note", label: "GC uses non-overlapping windows. Ambiguous bases are excluded; an all-ambiguous window is blank." } ] },
          { title: "Centre / heading", fields: [number(["centre", "size"], "Text size (pt)", 4, 24, .5),
            text(["centre", "organism"], "Organism (empty = automatic; *italic*, **bold**)"), text(["centre", "length"], "Length text (empty = automatic)"), text(["centre", "gc"], "GC text (empty = automatic)")] },
          { title: "Legend", fields: [check(["legend", "show"], "Show classes present"),
            { path: ["legend", "position"], label: "Circular legend position", type: "select", options: [["below", "Below"], ["right", "Right corner"]] } ] },
          { title: "Function classes", fields: [{ type: "note", label: "First match wins. Patterns are case-insensitive regular expressions on gene, name or feature type. Use ↑ / ↓ for priority." },
            ...(bad.length ? [{ type: "note", label: `Invalid patterns (ignored): ${bad.map(c => c.name).join(", ")}` }] : []),
            { type: "custom", render: change => classesControl(s, change) }] },
          { title: "Regions", fields: [{ type: "note", label: "Coordinates are 1-based and inclusive. From > To crosses the origin." }, { type: "custom", render: change => regionsControl(s, source, change) }] },
        ];
        if (sel?.type === "feature") {
          const f = source.features.find(f => f.key === sel.key);
          if (f) sections.unshift({ title: `Selected: ${f.type} ${f.name || ""}`, selection: true, fields: [
            text(["featureEdits", sel.key, "label"], "Name shown (empty = automatic; *italic*)"),
            { path: ["featureEdits", sel.key, "color"], label: "Individual colour", type: "color" },
            check(["featureEdits", sel.key, "hideLabel"], "Hide name"),
            number(["featureEdits", sel.key, "angle"], s.shape === "circular" ? "Name angle nudge (degrees)" : "Name horizontal nudge (pt)", -180, 180),
            number(["featureEdits", sel.key, "radial"], s.shape === "circular" ? "Name radial nudge (pt)" : "Name vertical nudge (pt)", -80, 120),
            { type: "button", label: "Reset this feature", action: "resetFeature" }, { type: "button", label: "Hide this gene", action: "hideFeature" }, { type: "button", label: "Hide all of this class", action: "hideClass" } ] });
        }
        if (sel?.type === "centre") sections.unshift({ title: "Selected centre text", selection: true, fields: [text(["centre", sel.key], "Text (*italic*, **bold**; empty = automatic)")] });
        if (sel?.type === "class") {
          const i = s.classes.findIndex(c => c.id === sel.key);
          if (i >= 0) sections.unshift({ title: `Selected class: ${s.classes[i].name}`, selection: true, fields: [text(["classes", String(i), "name"], "Class name"), { path: ["classes", String(i), "color"], label: "Colour", type: "color" }, { type: "button", action: "hideClass", label: "Hide all of this class" }] });
        }
        if (sel?.type === "region") {
          const i = s.regions.findIndex(r => r.id === sel.key);
          if (i >= 0) sections.unshift({ title: "Selected region", selection: true, fields: [text(["regions", String(i), "name"], "Name"), number(["regions", String(i), "start"], "From (bp)", 1, source.sequence.length), number(["regions", String(i), "end"], "To (bp)", 1, source.sequence.length), { path: ["regions", String(i), "color"], label: "Colour", type: "color" }] });
        }
        return sections;
      },
      draw(s, source, selection) {
        if (!source.sequence.length) return { body: '<text x="15" y="25">This sequence has no bases.</text>', width: 240, height: 60 };
        const shown = visible(s, source), used = [...new Map(shown.map(x => [x.c.id, x.c])).values()];
        const drawing = s.shape === "linear" ? drawLinear(s, source, shown, used, selection) : drawCircular(s, source, shown, used, selection);
        source.stats = { omittedLabels: drawing.omittedLabels };
        return drawing;
      },
      helpers: { classOf, featureKey, featureArcs, tickMarks, overlaps },
    };
    return kind;
  };
})();
