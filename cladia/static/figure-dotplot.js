// Dot plot figure (kind "dotplot" of figure.js): two sequences, one on each
// axis, a mark wherever they share a word (dotplot.js), forward and reverse-
// complement matches in two colours. Drawn as SVG: each row of marked cells
// becomes rectangles, so PDF stays vector. Settings: page width, word size,
// resolution (cells along the longer sequence), colours, axis names and ticks.
// source (app-tools.js showFigure): { a: { name, seq }, b: { name, seq } }.

(function () {
  window.makeDotplotFigure = function (h) {
    const { PT_PER_MM, FONTS, esc, num, textWidth, markupRuns } = h;
    const DotPlot = window.DotPlot;  // (dotplot.js, loaded first)

    const DEFAULTS = {
      page: { width: 120, margin: 4 },  // mm
      font: "Arial", fontSize: 8, lineWidth: 0.5,  // pt
      word: 0, resolution: 400,  // word 0: automatic
      colors: { forward: "#1f5fa8", reverse: "#c0392b" },
      names: { a: "", b: "" },  // empty: the sequences' names (*italic* allowed)
      ticks: true, frame: true,
    };

    const runsOut = (runs) => runs.map((r) => (r.italic || r.bold
      ? `<tspan${r.italic ? ' font-style="italic"' : ""}${r.bold ? ' font-weight="bold"' : ""}>${esc(r.text)}</tspan>` : esc(r.text))).join("");

    // the grid for these settings, computed once per word / size
    function gridFor(source, s, cols, rows) {
      const key = `${s.word}|${cols}|${rows}`;
      if (source.cache?.key !== key) source.cache = { key, grid: DotPlot.grid({ a: source.a.seq, b: source.b.seq, word: s.word, cols, rows }) };
      return source.cache.grid;
    }

    // Marked cells as rectangles, runs along each row merged
    function cellsPath(grid, cols, rows, x0, y0, cw, ch) {
      const out = [];
      for (let y = 0; y < rows; y++) {
        for (let x = 0; x < cols; x++) {
          if (!grid[y * cols + x]) continue;
          let end = x;
          while (end + 1 < cols && grid[y * cols + end + 1]) end++;
          out.push(`M${num(x0 + x * cw)} ${num(y0 + y * ch)}h${num((end - x + 1) * cw)}v${num(ch)}h${num(-(end - x + 1) * cw)}z`);
          x = end;
        }
      }
      return out.join("");
    }

    const number = (path, label, min, max, step = 1) => ({ path, label, type: "number", min, max, step });

    return {
      defaults: DEFAULTS,
      hint: "Settings are in the panel",
      prepare(source) {
        return { a: { ...source.a, seq: DotPlot.strip(source.a.seq) }, b: { ...source.b, seq: DotPlot.strip(source.b.seq) }, cache: null };
      },
      select() { return null; },
      action(name, s) {
        if (name === "singleColumn") s.page.width = 84;
        if (name === "fullWidth") s.page.width = 174;
      },
      panel(s, sel, source) {
        const self = source.a.name === source.b.name && source.a.seq === source.b.seq;
        return [
          { title: "Page", fields: [number(["page", "width"], "Width (mm)", 30, 400),
            { type: "button", label: "84 mm", action: "singleColumn" }, { type: "button", label: "174 mm", action: "fullWidth" },
            { path: ["font"], label: "Font", type: "select", options: FONTS.map((f) => [f, f]) },
            number(["fontSize"], "Text size (pt)", 3, 18, .5), number(["lineWidth"], "Frame line (pt)", .1, 3, .05)] },
          { title: "Dots", fields: [
            { path: ["word"], label: "Word (bases in common)", type: "select",
              options: [["0", `Automatic (${DotPlot.autoWord(Math.max(source.a.seq.length, source.b.seq.length))})`], ...[8, 10, 12, 14, 16].map((w) => [String(w), String(w)])] },
            number(["resolution"], "Cells along the longer sequence", 50, 2000, 10),
            { type: "note", label: "More cells show finer detail; each cell is marked when any shared word falls in it." },
            { path: ["colors", "forward"], label: "Same strand", type: "color" },
            { path: ["colors", "reverse"], label: "Reverse complement (inversions)", type: "color" }] },
          { title: "Axes", fields: [
            { path: ["names", "a"], label: `Name across (empty: ${source.a.name})`, type: "text" },
            ...(self ? [] : [{ path: ["names", "b"], label: `Name down (empty: ${source.b.name})`, type: "text" }]),
            { path: ["ticks"], label: "Ticks and positions", type: "check" },
            { path: ["frame"], label: "Frame", type: "check" }] },
        ];
      },
      draw(s, source) {
        const fs = s.fontSize, font = (size) => `font-family="${esc(s.font)}" font-size="${num(size)}"`;
        const lenA = source.a.seq.length, lenB = source.b.seq.length;
        const nameA = markupRuns(s.names.a || source.a.name), nameB = markupRuns(s.names.b || source.b.name);
        const W = s.page.width * PT_PER_MM, m = s.page.margin * PT_PER_MM;
        // room for the names and tick labels at the top and left
        const tickW = s.ticks ? textWidth(`${(Math.max(lenA, lenB) / 1000).toLocaleString()} kb`, fs * .85, s.font) + 4 : 0;
        const left = m + fs * 1.4 + tickW, top = m + fs * 1.4 + (s.ticks ? fs * 1.2 : 0);
        const plotW = Math.max(20, W - left - m), scale = plotW / lenA, plotH = lenB * scale;
        const H = top + plotH + m;
        const longer = Math.max(lenA, lenB), res = Math.max(10, Math.min(2000, s.resolution || 400));
        const cols = Math.max(1, Math.round(res * lenA / longer)), rows = Math.max(1, Math.round(res * lenB / longer));
        const out = [];
        if (lenA && lenB) {
          const grid = gridFor(source, { word: Number(s.word) || 0 }, cols, rows);
          const cw = plotW / grid.cols, ch = plotH / grid.rows;
          if (s.colors.reverse) out.push(`<path fill="${esc(s.colors.reverse)}" d="${cellsPath(grid.reverse, grid.cols, grid.rows, left, top, cw, ch)}"/>`);
          if (s.colors.forward) out.push(`<path fill="${esc(s.colors.forward)}" d="${cellsPath(grid.forward, grid.cols, grid.rows, left, top, cw, ch)}"/>`);
        }
        if (s.frame) out.push(`<rect x="${num(left)}" y="${num(top)}" width="${num(plotW)}" height="${num(plotH)}" fill="none" stroke="#000" stroke-width="${num(s.lineWidth)}"/>`);
        if (s.ticks) {
          const small = fs * .85, tick = 2;
          const axis = (length, size, across) => {
            const step = DotPlot.tickStep(length, Math.max(2, Math.floor(size / 60)));
            for (let v = across ? 0 : step; v <= length; v += step) {  // (0 once, across the top)
              const p = (across ? left : top) + v * scale, text = esc(DotPlot.tickText(v, step));
              out.push(across
                ? `<line x1="${num(p)}" y1="${num(top)}" x2="${num(p)}" y2="${num(top - tick)}" stroke="#000" stroke-width="${num(s.lineWidth)}"/>` +
                  `<text x="${num(p)}" y="${num(top - tick - 1.5)}" ${font(small)} text-anchor="middle">${text}</text>`
                : `<line x1="${num(left)}" y1="${num(p)}" x2="${num(left - tick)}" y2="${num(p)}" stroke="#000" stroke-width="${num(s.lineWidth)}"/>` +
                  `<text x="${num(left - tick - 1.5)}" y="${num(p + small * .35)}" ${font(small)} text-anchor="end">${text}</text>`);
            }
          };
          axis(lenA, plotW, true);
          axis(lenB, plotH, false);
        }
        out.push(`<text x="${num(left + plotW / 2)}" y="${num(m + fs)}" ${font(fs)} text-anchor="middle">${runsOut(nameA)}</text>`);
        const cy = top + plotH / 2;
        out.push(`<text transform="translate(${num(m + fs)} ${num(cy)}) rotate(-90)" ${font(fs)} text-anchor="middle">${runsOut(nameB)}</text>`);
        return { body: out.join("\n"), width: W, height: H };
      },
    };
  };
})();
