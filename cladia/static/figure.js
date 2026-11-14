// Cladia figure editor: a tree (and later a genome comparison) drawn for a paper.
//
// A figure is SVG: the page sized in millimetres, everything on it in points
// (1 pt = 1/72 inch, as font sizes and line widths are given in journals' rules).
// The editor shows the figure as it will be exported, a side panel of settings
// (built from each kind's field list), and selections: click a branch for its
// clade, a tip name for that tip, and the panel shows what can be set for it.
// Every change is one undo step and is saved (options.onSave, debounced).
//
//   const editor = new FigureEditor(container, { onSave, onExport, lookup });
//   editor.open({ of: "tree", settings, title }, { nodes });   // nodes: /api/tree
//
// Exports (options.onExport(format, svg)) get the SVG made for export: no
// selection marks.

(function () {
  const PT_PER_MM = 72 / 25.4;
  const FONTS = ["Arial", "Helvetica", "Calibri", "Times New Roman", "Georgia", "Courier New"];
  const SAVE_DELAY = 500;  // ms after the last change

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const num = (x) => (Math.round(x * 100) / 100).toString();  // SVG coordinates, 0.01 pt
  const clone = (x) => JSON.parse(JSON.stringify(x));

  // A value in nested settings by a dotted path ("clades.<key>.color"); keys may
  // contain dots or tabs, so a path is an array
  function getPath(obj, path) {
    return path.reduce((o, k) => (o == null ? undefined : o[k]), obj);
  }
  function setPath(obj, path, value) {
    let o = obj;
    for (const k of path.slice(0, -1)) o = o[k] ??= {};
    if (value === undefined) delete o[path[path.length - 1]];
    else o[path[path.length - 1]] = value;
  }
  // Defaults under the user's settings (theirs win), deep
  function merged(defaults, settings) {
    const out = clone(defaults);
    for (const [k, v] of Object.entries(settings || {})) {
      out[k] = v && typeof v === "object" && !Array.isArray(v) && out[k] && typeof out[k] === "object" ? merged(out[k], v) : v;
    }
    return out;
  }

  // Text widths, measured once per font (for the space names take)
  const measurer = document.createElement("canvas").getContext("2d");
  function textWidth(text, size, font, italic = false, bold = false) {
    measurer.font = `${italic ? "italic " : ""}${bold ? "bold " : ""}${size}px "${font}"`;
    return measurer.measureText(text).width;
  }

  // ── Text in runs: species names in italics by the rules, *markup* ──────
  // A label is runs [{ text, italic, bold }], measured and drawn as tspans.
  const UPRIGHT = new Set(["sp.", "spp.", "cf.", "aff.", "nr.", "var.", "subsp.", "ssp.", "f.", "forma", "indet.", "gen.",
                           "nov.", "sect.", "ser.", "s.l.", "s.s.", "agg.", "x", "×"]);
  const EPITHET_NEXT = new Set(["var.", "subsp.", "ssp.", "f.", "forma", "cf.", "aff."]);  // an epithet may follow (italic)
  const BEFORE_GENUS = new Set(["uncultured", "unidentified", "unclassified", "environmental"]);  // upright, a genus may follow

  // A species name in italics as the naming rules have it: the genus and its
  // epithets italic, the rest upright (Gracilaria sp. 5 TNE, Gracilaria cf.
  // tenuistipitata, Mastocarpus papillatus var. x; not "uncultured", strain codes)
  function speciesRuns(text, extra = {}) {
    const words = String(text).split(/(\s+)/);
    let wordIndex = 0, genus = false, epithet = false;
    const flags = words.map((w) => {
      if (/^\s+$/.test(w) || !w) return null;  // spaces: decided below, from the words around them
      const lower = w.toLowerCase();
      let italic = false;
      if (wordIndex === 0 && BEFORE_GENUS.has(lower)) return false;  // (the next word is still the first)
      if (wordIndex === 0) italic = genus = /^[A-Z][a-z]+(-[a-z]+)?$/.test(w);
      else if (UPRIGHT.has(lower)) epithet = EPITHET_NEXT.has(lower);
      else if (/^[a-z][a-z-]+$/.test(w) && ((wordIndex === 1 && genus) || epithet)) { italic = true; epithet = false; }
      else epithet = false;
      wordIndex++;
      return italic;
    });
    const runs = [];
    words.forEach((w, k) => {
      if (!w) return;
      const italic = flags[k] ?? (flags[k - 1] && flags[k + 1]);  // a space between two italic words is italic
      const last = runs[runs.length - 1];
      if (last && last.italic === !!italic) last.text += w;
      else runs.push({ text: w, italic: !!italic, ...extra });
    });
    return runs;
  }

  // Text with *italic* and **bold** parts (base: the style of the rest)
  function markupRuns(text, base = {}) {
    const runs = [];
    for (const piece of String(text).split(/(\*\*[^*]+\*\*|\*[^*]+\*)/)) {
      if (!piece) continue;
      if (/^\*\*[^*]+\*\*$/.test(piece)) runs.push({ ...base, text: piece.slice(2, -2), bold: true });
      else if (/^\*[^*]+\*$/.test(piece)) runs.push({ ...base, text: piece.slice(1, -1), italic: !base.italic });
      else runs.push({ ...base, text: piece });
    }
    return runs;
  }
  const runsWidth = (runs, size, font) => runs.reduce((w, r) => w + textWidth(r.text, size, font, r.italic, r.bold), 0);
  const runsSVG = (runs) => runs.map((r) => (r.italic || r.bold
    ? `<tspan${r.italic ? ' font-style="italic"' : ""}${r.bold ? ' font-weight="bold"' : ""}>${esc(r.text)}</tspan>` : esc(r.text))).join("");
  const LABEL_STYLES = [["bold", "Bold"], ["normal", "Normal"], ["italic", "Italic"], ["bold italic", "Bold italic"]];
  const styleBase = (style = "bold") => ({ bold: style.includes("bold"), italic: style.includes("italic") });

  // ── The tree figure ────────────────────────────────────────────────────
  const TREE_DEFAULTS = {
    page: { width: 174, height: 0, margin: 4 },  // mm; height 0: as the tips need
    font: "Arial", fontSize: 8, lineWidth: 0.75,  // pt
    tips: { label: "name", italic: "none", align: false, spacing: 0, gap: 3 },  // spacing 0: from the font size
    branches: { lengths: true, ladderize: "none" },
    support: { show: "values", threshold: 70, max: 0, part: "all", size: 6.5 },  // max 0: no upper limit
    shape: "rectangular", mirror: false,  // mirror: the root at the right, the names at the left
    circle: { open: 20 },  // circular: degrees left open (at the right), where the root's side is
    scale: { show: true, length: 0 },  // length 0: a round number, about a sixth of the tree
    clades: {},   // clade key -> { color, highlight, label, labelStyle, bar, collapsed }
    rotated: [],  // clade keys whose children are drawn the other way round
    tipEdits: {}, // tip name -> { text (*italic* marks), italic, color }
    bars: [],     // bars for any tips: { tips: [names], label, labelStyle, color }
  };

  const LABELS = { name: "Name", organism: "Organism", "organism name": "Organism and name", description: "Description" };

  // nodes: [{ name, length, support, parent }], parents first (library.tree_nodes)
  function treeOf(nodes) {
    const kids = nodes.map(() => []);
    nodes.forEach((n, i) => { if (n.parent >= 0) kids[n.parent].push(i); });
    const tips = nodes.map(() => []);  // each node's tip names
    for (let i = nodes.length - 1; i >= 0; i--) {
      if (!kids[i].length) tips[i] = [nodes[i].name];
      if (nodes[i].parent >= 0) tips[nodes[i].parent].push(...tips[i]);
    }
    // a clade is known by its tips: settings stay with it when the tree is rerooted or ladderized
    const keys = tips.map((t) => [...t].sort().join("\t"));
    return { nodes, kids, tips, keys, byKey: new Map(keys.map((k, i) => [k, i])) };
  }

  const treeFigure = {
    defaults: TREE_DEFAULTS,

    prepare: (source) => ({ tree: treeOf(source.nodes) }),

    // A click: a branch selects its clade, a name its tip (Shift: the tips from
    // the one selected to this one, in their order on the page), a bar the bar
    select(el, source, prev, e, drawn) {
      if (el.dataset.bar !== undefined) return { type: "bar", index: Number(el.dataset.bar) };
      if (el.dataset.tip !== undefined) {
        const name = el.dataset.tip, anchor = prev?.anchor ?? prev?.name;
        if (e.shiftKey && anchor !== undefined && (prev.type === "tip" || prev.type === "tips")) {
          const order = drawn.tipOrder, a = order.indexOf(anchor), b = order.indexOf(name);
          if (a >= 0 && b >= 0) {
            const names = order.slice(Math.min(a, b), Math.max(a, b) + 1);
            return names.length > 1 ? { type: "tips", names, anchor } : { type: "tip", name };
          }
        }
        return { type: "tip", name };
      }
      if (el.dataset.node !== undefined) return { type: "clade", key: source.tree.keys[Number(el.dataset.node)] };
      return null;
    },
    hint: "click a branch, a name or a bar to set it; Shift+click names for the tips between",

    // The settings panel: sections of fields ({ path, label, type, ... })
    panel(s, sel, source) {
      const tree = source.tree;
      const sections = [
        { title: "Page", fields: [
          { path: ["page", "width"], label: "Width (mm)", type: "number", min: 30, max: 400, step: 1,
            help: "Journals: about 84 mm for one column, 174 mm for the full width" },
          { path: ["page", "height"], label: s.shape === "circular" ? "Height (mm, 0 = as wide)" : "Height (mm, 0 = as the tips need)",
            type: "number", min: 0, max: 600, step: 1 },
          { path: ["font"], label: "Font", type: "select", options: FONTS.map((f) => [f, f]) },
          { path: ["fontSize"], label: "Font size (pt)", type: "number", min: 4, max: 24, step: 0.5 },
          { path: ["lineWidth"], label: "Line width (pt)", type: "number", min: 0.1, max: 4, step: 0.05 },
        ] },
        { title: "Layout", fields: [
          { path: ["shape"], label: "Shape", type: "select", options: [["rectangular", "Rectangular"], ["circular", "Circular"]] },
          { path: ["mirror"], label: "Mirrored: the root at the right, the names at the left", type: "check",
            hidden: s.shape === "circular" },
          { path: ["circle", "open"], label: "Opening of the circle (degrees, 0 = closed)", type: "number", min: 0, max: 180, step: 1,
            hidden: s.shape !== "circular" },
        ] },
        { title: "Tips", fields: [
          { path: ["tips", "label"], label: "Names", type: "select", options: Object.entries(LABELS) },
          { path: ["tips", "italic"], label: "Italic", type: "select",
            options: [["none", "None"], ["organism", "Species names (genus and epithets, not sp., cf., codes)"], ["all", "All of the name"]] },
          { path: ["tips", "align"], label: "Names in one column (dotted lines to them)", type: "check" },
          { path: ["tips", "spacing"], label: "Space between tips (pt, 0 = from the font)", type: "number", min: 0, max: 40, step: 0.5,
            hidden: s.shape === "circular" },
        ] },
        { title: "Branches", fields: [
          { path: ["branches", "lengths"], label: "Branch lengths (a phylogram; off: a cladogram)", type: "check" },
          { path: ["branches", "ladderize"], label: "Order of clades", type: "select",
            options: [["none", "As in the tree"], ["up", "Smaller clades first"], ["down", "Larger clades first"]] },
          { path: ["scale", "show"], label: "Scale bar", type: "check", hidden: !s.branches.lengths },
          { path: ["scale", "length"], label: "Scale bar length (0 = a round number)", type: "number", min: 0, step: "any",
            hidden: !s.branches.lengths || !s.scale.show },
        ] },
        { title: "Support", fields: [
          { path: ["support", "show"], label: "Show", type: "select",
            options: [["values", "Values"], ["circles", "Filled circles"], ["none", "Nothing"]] },
          { path: ["support", "threshold"], label: "Only from (e.g. 70)", type: "number", min: 0, step: "any",
            hidden: s.support.show === "none" },
          { path: ["support", "max"], label: "Only up to (e.g. 99 leaves out the 100s; 0 = no limit)", type: "number", min: 0, step: "any",
            hidden: s.support.show === "none" },
          { path: ["support", "part"], label: "With two values (SH-aLRT/UFBoot)", type: "select", hidden: s.support.show === "none",
            options: [["all", "Both (each over the threshold)"], ["first", "The first only"], ["second", "The second only"]] },
          { path: ["support", "size"], label: "Size (pt)", type: "number", min: 3, max: 16, step: 0.5, hidden: s.support.show === "none" },
        ] },
      ];
      if (sel?.type === "clade" && tree.byKey.has(sel.key)) {
        const i = tree.byKey.get(sel.key), n = tree.tips[i].length;
        const base = ["clades", sel.key];
        sections.unshift({ title: `Selected clade (${n} tip${n === 1 ? "" : "s"})`, selection: true, fields: [
          { path: [...base, "color"], label: "Branch colour", type: "color" },
          { path: [...base, "highlight"], label: "Background colour", type: "color" },
          { path: [...base, "label"], label: "Clade label (a bar with this name at the right; *italic*)", type: "text" },
          { path: [...base, "labelStyle"], label: "Label style", type: "select", options: LABEL_STYLES },
          { path: [...base, "bar"], label: "A bar even without a label", type: "check" },
          { path: [...base, "collapsed"], label: "Collapse into a triangle", type: "check" },
          { label: "Swap its two sides", type: "button", action: "rotate" },
          { label: "Clear this clade's settings", type: "button", action: "clear" },
        ] });
      } else if (sel?.type === "tip") {
        const base = ["tipEdits", sel.name];
        sections.unshift({ title: `Selected tip: ${sel.name}`, selection: true, fields: [
          { path: [...base, "text"], label: "Text (empty: as the other tips; *italic*, **bold**)", type: "text" },
          { path: [...base, "italic"], label: "Italic", type: "select",
            options: [["", "As the other tips"], ["none", "None"], ["organism", "Species names"], ["all", "All of it"]] },
          { path: [...base, "color"], label: "Colour", type: "color" },
          { type: "note", label: "Shift+click another name to select the tips between, e.g. for a bar." },
        ] });
      } else if (sel?.type === "tips") {
        sections.unshift({ title: `${sel.names.length} tips selected`, selection: true, fields: [
          { label: "Add a bar for these tips", type: "button", action: "addBar" },
          { type: "note", label: "A bar at the right, like a clade's: for a group that isn't one clade in the tree." },
        ] });
      } else if (sel?.type === "bar" && s.bars[sel.index]) {
        const base = ["bars", sel.index];
        sections.unshift({ title: `Selected bar (${s.bars[sel.index].tips.length} tips)`, selection: true, fields: [
          { path: [...base, "label"], label: "Label (empty: just the bar; *italic*)", type: "text" },
          { path: [...base, "labelStyle"], label: "Label style", type: "select", options: LABEL_STYLES },
          { path: [...base, "color"], label: "Colour", type: "color" },
          { label: "Delete this bar", type: "button", action: "deleteBar" },
        ] });
      }
      return sections;
    },

    // Panel buttons on the selection
    action(name, s, sel) {
      if (name === "rotate") {
        const at = s.rotated.indexOf(sel.key);
        if (at >= 0) s.rotated.splice(at, 1); else s.rotated.push(sel.key);
      } else if (name === "clear") {
        delete s.clades[sel.key];
        s.rotated = s.rotated.filter((k) => k !== sel.key);
      } else if (name === "addBar") {
        s.bars.push({ tips: sel.names });
        return { type: "bar", index: s.bars.length - 1 };  // the new bar selected
      } else if (name === "deleteBar") {
        s.bars.splice(sel.index, 1);
        return null;
      }
    },

    // The figure as SVG markup; sel: drawn marked (not when exporting)
    draw(s, source, sel, lookup) {
      const tree = source.tree;
      const { nodes, kids, tips, keys } = tree;
      const fs = s.fontSize, lw = s.lineWidth;
      const W = s.page.width * PT_PER_MM, margin = s.page.margin * PT_PER_MM;
      const spacing = s.tips.spacing || fs * 1.3;

      // the order of children: rotated clades turned round, then ladderized
      const order = kids.map((k, i) => {
        let c = [...k];
        if (s.branches.ladderize !== "none") c.sort((a, b) => (tips[a].length - tips[b].length) * (s.branches.ladderize === "up" ? 1 : -1));
        if (s.rotated.includes(keys[i])) c.reverse();
        return c;
      });
      const style = (i) => s.clades[keys[i]] || {};
      const collapsed = (i) => kids[i].length && style(i).collapsed;

      // tip labels as runs: the text chosen, its species names in italics (or all, or none)
      const words = (name) => String(name).replace(/_/g, " ");
      const info = (name) => lookup(name) || {};
      function tipRuns(name) {
        const edit = s.tipEdits[name] || {};
        const mode = edit.italic || s.tips.italic;
        if (edit.text) return markupRuns(edit.text, { italic: mode === "all" });
        const org = info(name).organism || "", label = s.tips.label;
        const text = label === "organism" ? (org || words(name))
          : label === "organism name" ? (org ? `${org} ${words(name)}` : words(name))
          : label === "description" ? (info(name).description || words(name)) : words(name);
        if (mode === "all") return [{ text, italic: true }];
        if (mode !== "organism") return [{ text }];
        if (label === "organism name" && org) return [...speciesRuns(org), { text: ` ${words(name)}` }];
        return speciesRuns(text);
      }
      const labelWidth = (runs) => runsWidth(runs, fs, s.font);

      // rows: tips, and collapsed clades (one row each, their label beside the triangle)
      const rowOf = new Map(), visible = [];
      let rows = 0;
      (function walk(root) {
        const stack = [root];
        while (stack.length) {
          const i = stack.pop();
          visible.push(i);
          if (!kids[i].length || collapsed(i)) { rowOf.set(i, rows++); continue; }
          stack.push(...[...order[i]].reverse());
        }
      })(0);
      const labelOf = new Map();  // tip or collapsed clade -> label runs
      for (const [i] of rowOf) {
        labelOf.set(i, kids[i].length ? markupRuns(style(i).label || `${tips[i].length} tips`) : tipRuns(nodes[i].name));
      }
      const maxLabel = Math.max(0, ...[...labelOf.values()].map(labelWidth));
      const top = margin + fs * 0.7;
      const y = new Float64Array(nodes.length);
      for (const [i, r] of rowOf) y[i] = top + r * spacing;
      for (let k = visible.length - 1; k >= 0; k--) {
        const i = visible[k];
        if (rowOf.has(i)) continue;
        const c = order[i];
        y[i] = (y[c[0]] + y[c[c.length - 1]]) / 2;
      }
      const rowsEnd = (i) => {  // a clade's first and last row
        let a = Infinity, b = -Infinity;
        const stack = [i];
        while (stack.length) {
          const j = stack.pop();
          if (rowOf.has(j)) { a = Math.min(a, y[j]); b = Math.max(b, y[j]); continue; }
          stack.push(...order[j]);
        }
        return [a, b];
      };
      // bars at the right: labelled clades and bars for any tips, each in the first
      // column out that's free along its rows (a genus nearer the tips, its family further)
      const tipNode = new Map(nodes.map((n, i) => [n.name, i]).filter(([n, i]) => !kids[i].length));
      const rowNode = (i) => {  // the row a tip is drawn in: its own, or its collapsed clade's
        let found = rowOf.has(i) ? i : null;
        for (let j = i; j > 0;) { j = nodes[j].parent; if (rowOf.has(j)) found = j; }
        return found;
      };
      const groups = [];
      for (const i of visible) {
        const st = style(i);
        if (kids[i].length && !collapsed(i) && (st.label || st.bar)) {
          groups.push({ node: i, span: rowsEnd(i), runs: st.label ? markupRuns(st.label, styleBase(st.labelStyle)) : [], color: null });
        }
      }
      s.bars.forEach((bar, k) => {
        const ys = bar.tips.map((t) => tipNode.get(t)).filter((i) => i !== undefined).map(rowNode).filter((i) => i !== null).map((i) => y[i]);
        if (ys.length) groups.push({ bar: k, span: [Math.min(...ys), Math.max(...ys)], runs: bar.label ? markupRuns(bar.label, styleBase(bar.labelStyle)) : [], color: bar.color || "#000" });
      });
      groups.sort((g, h) => (g.span[1] - g.span[0]) - (h.span[1] - h.span[0]) || g.span[0] - h.span[0]);
      const placed = [];
      for (const g of groups) {
        const over = placed.filter((h) => h.span[0] <= g.span[1] + spacing * 0.5 && g.span[0] <= h.span[1] + spacing * 0.5);
        g.column = over.length ? 1 + Math.max(...over.map((h) => h.column)) : 0;
        placed.push(g);
      }
      const columns = groups.length ? 1 + Math.max(...groups.map((g) => g.column)) : 0;
      const columnWidth = Array.from({ length: columns }, (_, c) =>
        fs * 1.1 + Math.max(0, ...groups.filter((g) => g.column === c && g.runs.length).map((g) => fs * 0.5 + runsWidth(g.runs, fs, s.font))));
      const barColumn = columns ? fs * 0.6 + columnWidth.reduce((a, b) => a + b, 0) : 0;

      // x: branch lengths (or levels for a cladogram), in points
      const depth = new Float64Array(nodes.length);
      const levels = new Int32Array(nodes.length);  // cladogram: tips at the right, each node a level before its deepest child
      for (let i = 1; i < nodes.length; i++) depth[i] = depth[nodes[i].parent] + (s.branches.lengths ? Math.max(0, nodes[i].length || 0) : 0);
      for (let i = nodes.length - 1; i >= 0; i--) {  // (a collapsed clade keeps its depth: its triangle reaches the tips)
        if (kids[i].length) levels[i] = 1 + Math.max(...kids[i].map((k) => levels[k]));
      }
      const deepest = new Float64Array(nodes.length);  // the deepest tip under each node (a triangle's far end)
      for (let i = nodes.length - 1; i >= 0; i--) {
        deepest[i] = Math.max(depth[i], ...kids[i].map((k) => deepest[k]));
      }
      const gap = s.tips.gap;
      const maxDepth = s.branches.lengths ? Math.max(1e-9, ...[...rowOf.keys()].map((i) => (collapsed(i) ? deepest[i] : depth[i]))) : levels[0];
      const frac = (i) => (s.branches.lengths ? depth[i] / maxDepth : (levels[0] - levels[i]) / levels[0]);  // 0 root .. 1 the deepest tip
      const farFrac = (i) => (s.branches.lengths ? deepest[i] / maxDepth : 1);  // a collapsed clade's triangle: to its deepest tip

      // colours flow down a clade, unless a clade inside sets its own
      const color = new Array(nodes.length);
      for (let i = 0; i < nodes.length; i++) color[i] = style(i).color || (i ? color[nodes[i].parent] : null) || "#000";

      // support shown: values from the threshold, and up to the maximum (e.g. 99: no 100s)
      function supportText(i) {
        const v = nodes[i].support;
        if (!i || !kids[i].length || v == null || v === "") return null;
        const parts = String(v).split("/").map(Number);
        const chosen = s.support.part === "first" ? [parts[0]] : s.support.part === "second" ? [parts[parts.length - 1]] : parts;
        if (chosen.some((p) => Number.isNaN(p) || p < s.support.threshold || (s.support.max && p > s.support.max))) return null;
        return s.support.part === "all" ? String(v) : String(chosen[0]);
      }
      // the scale bar's length: a round number of substitutions per site, about a sixth of the tree
      function scaleLength() {
        if (s.scale.length) return s.scale.length;
        const target = maxDepth / 6, p = 10 ** Math.floor(Math.log10(target));
        return [1, 2, 5, 10].map((m) => m * p).reduce((a, b) => (Math.abs(b - target) < Math.abs(a - target) ? b : a));
      }
      const out = [];
      const font = `font-family="${esc(s.font)}" font-size="${num(fs)}"`;
      const visibleSet = new Set(visible);
      const marked = new Set(sel?.type === "tip" ? [sel.name] : sel?.type === "tips" ? sel.names : []);
      const tipOrder = [...rowOf.keys()].filter((i) => !kids[i].length).map((i) => nodes[i].name);  // for Shift+click
      const scaleRoom = s.branches.lengths && s.scale.show ? fs * 2.6 : 0;
      const tipName = (i) => (kids[i].length ? null : nodes[i].name);
      const tipColor = (i) => (tipName(i) && s.tipEdits[tipName(i)]?.color) || (kids[i].length ? color[i] : "#000");
      const which = (i) => (tipName(i) !== null ? `data-tip="${esc(tipName(i))}"` : `data-node="${i}"`);

      if (s.shape === "circular") {
        // ── Circular: rows become angles (clockwise from the right, an opening
        // left there), depth the distance from the centre. Names point outwards,
        // turned round on the left half so they read left to right.
        const open = Math.min(180, Math.max(0, s.circle.open));
        const step = (360 - open) / Math.max(1, rows);
        const angle = (yv) => open / 2 + step / 2 + ((yv - top) / spacing) * step;  // degrees
        const ring = gap + maxLabel + barColumn;  // names and bars outside the tree
        const H0 = W;  // a square page (plus the scale bar)
        const R = Math.max(20, W / 2 - margin - ring);
        const cx = W / 2, cy = H0 / 2;
        const H = s.page.height ? s.page.height * PT_PER_MM : H0 + scaleRoom;
        const rad = (d) => (d * Math.PI) / 180;
        const pt = (r, d) => [cx + r * Math.cos(rad(d)), cy + r * Math.sin(rad(d))];
        const P = (r, d) => pt(r, d).map(num).join(",");
        const rOf = (i) => R * frac(i);
        const arc = (r, a, b) => {  // a path piece along the circle from angle a to b
          const [x, yy] = pt(r, b);
          return `A${num(r)},${num(r)} 0 ${Math.abs(b - a) > 180 ? 1 : 0} ${b > a ? 1 : 0} ${num(x)},${num(yy)}`;
        };
        const sector = (r0, r1, a, b) => `M${P(r1, a)} ${arc(r1, a, b)} L${P(r0, b)} ${arc(r0, b, a)} Z`;
        // text along a radius at angle d, from radius r outwards (anchored at its inner end)
        const radial = (r, d, inner) => {
          const [x, yy] = pt(r, d), left = d > 90 && d < 270;
          return { open: `<g transform="translate(${num(x)},${num(yy)}) rotate(${num(left ? d - 180 : d)})">`,
                   anchor: left ? "end" : "start", dx: left ? -inner : inner };
        };
        // runs as texts of their own, each placed by its measured width: the PDF export
        // (svglib) misplaces the tspans of a partly italic name inside a turned group
        const placedRuns = (runs, g, attrs) => {
          let x = g.anchor === "end" ? g.dx - runsWidth(runs, fs, s.font) : g.dx;
          return runs.map((r) => {  // (spaces at a piece's ends as distance: svglib drops them)
            const lead = r.text.length - r.text.trimStart().length;
            const at = x + textWidth(r.text.slice(0, lead), fs, s.font, r.italic, r.bold);
            x += textWidth(r.text, fs, s.font, r.italic, r.bold);
            return r.text.trim() ? `<text x="${num(at)}" y="${num(fs * 0.35)}" ${font}${r.italic ? ' font-style="italic"' : ""}${r.bold ? ' font-weight="bold"' : ""} ${attrs}>${esc(r.text.trim())}</text>` : "";
          }).join("");
        };

        for (const i of visible) {  // backgrounds of highlighted clades
          const h = style(i).highlight;
          if (!h || !kids[i].length) continue;
          const [a, b] = rowsEnd(i);
          const r0 = i ? (rOf(nodes[i].parent) + rOf(i)) / 2 : 0;
          out.push(`<path d="${sector(r0, R + gap + maxLabel + gap / 2, angle(a) - step / 2, angle(b) + step / 2)}" fill="${esc(h)}" fill-opacity="0.25"/>`);
        }
        if (sel?.type === "clade" && tree.byKey.has(sel.key)) {  // the selection's mark
          const i = tree.byKey.get(sel.key);
          if (visible.includes(i)) {
            const [a, b] = kids[i].length && !collapsed(i) ? rowsEnd(i) : [y[i], y[i]];
            out.push(`<path class="fig-sel" d="${sector(Math.max(0, rOf(i) - 3), R + gap + maxLabel + 2, angle(a) - step / 2, angle(b) + step / 2)}" fill="none" stroke="#2f6fd6" stroke-width="0.8" stroke-dasharray="3 2"/>`);
          }
        }
        // branches: a radial line to each node, an arc joining a node's children
        for (const i of visible) {
          const d = angle(y[i]), r = rOf(i);
          if (i > 0) {
            const [x1, y1] = pt(rOf(nodes[i].parent), d), [x2, y2] = pt(r, d);
            out.push(`<line x1="${num(x1)}" y1="${num(y1)}" x2="${num(x2)}" y2="${num(y2)}" stroke="${color[i]}" stroke-width="${num(lw)}" stroke-linecap="square" data-node="${i}"/>`);
          }
          if (kids[i].length && !collapsed(i)) {
            for (const k of order[i].filter((c) => visibleSet.has(c))) {  // each child's colour on its part
              out.push(`<path d="M${P(r, d)} ${arc(r, d, angle(y[k]))}" fill="none" stroke="${color[k]}" stroke-width="${num(lw)}" data-node="${k}"/>`);
            }
          }
          if (collapsed(i)) {  // a wedge to the deepest tip
            const far = Math.max(R * farFrac(i), r + fs), h = step * 0.42;
            out.push(`<polygon points="${P(r, d)} ${P(far, d - h)} ${P(far, d + h)}" fill="${color[i]}" fill-opacity="0.35" stroke="${color[i]}" stroke-width="${num(lw)}" data-node="${i}"/>`);
          }
        }
        for (const i of visible) {  // invisible wide strokes: easier to click
          if (!i) continue;
          const d = angle(y[i]), [x1, y1] = pt(rOf(nodes[i].parent), d), [x2, y2] = pt(rOf(i), d);
          out.push(`<line class="fig-hit" x1="${num(x1)}" y1="${num(y1)}" x2="${num(x2)}" y2="${num(y2)}" stroke="transparent" stroke-width="6" data-node="${i}"/>`);
        }
        const tipR = (i) => (s.tips.align ? R : collapsed(i) ? Math.max(R * farFrac(i), rOf(i) + fs) : rOf(i));
        if (s.tips.align) {  // dotted guides to names on one circle
          for (const [i] of rowOf) {
            const d = angle(y[i]), end = collapsed(i) ? R * farFrac(i) : rOf(i);
            if (R - end > 2) {
              const [x1, y1] = pt(end + 1, d), [x2, y2] = pt(R, d);
              out.push(`<line x1="${num(x1)}" y1="${num(y1)}" x2="${num(x2)}" y2="${num(y2)}" stroke="#999" stroke-width="${num(lw * 0.5)}" stroke-dasharray="1 1.5"/>`);
            }
          }
        }
        for (const [i] of rowOf) {  // tip names
          const t = labelOf.get(i), g = radial(tipR(i), angle(y[i]), gap);
          let mark = "";
          if (marked.has(tipName(i))) {
            const w = labelWidth(t);
            mark = `<rect class="fig-sel" x="${num(g.anchor === "end" ? g.dx - w - 1 : g.dx - 1)}" y="${num(-fs * 0.65)}" width="${num(w + 2)}" height="${num(fs * 1.3)}" fill="none" stroke="#2f6fd6" stroke-width="0.8" stroke-dasharray="3 2"/>`;
          }
          out.push(`${g.open}${placedRuns(t, g, `fill="${esc(tipColor(i))}" ${which(i)}`)}${mark}</g>`);
        }
        if (s.support.show !== "none") {  // support at the nodes
          const ss = s.support.size;
          for (const i of visible) {
            const text = supportText(i);
            if (text === null) continue;
            const [x, yy] = pt(rOf(i), angle(y[i]));
            out.push(s.support.show === "circles"
              ? `<circle cx="${num(x)}" cy="${num(yy)}" r="${num(ss * 0.32)}" fill="#000"/>`
              : `<text x="${num(x - 1)}" y="${num(yy - lw - 1)}" font-family="${esc(s.font)}" font-size="${num(ss)}" text-anchor="end" fill="#333">${esc(text)}</text>`);
          }
        }
        // bars: arcs outside the names, in their columns, their names beside them
        let colR = R + gap + maxLabel + fs * 0.6;
        const columnR = columnWidth.map((w) => { const r = colR; colR += w; return r; });
        for (const g of groups) {
          const [a, b] = g.span, r = columnR[g.column], c = g.color || color[g.node];
          const at = g.bar !== undefined ? `data-bar="${g.bar}"` : `data-node="${g.node}"`;
          const a0 = angle(a) - step * 0.35, a1 = angle(b) + step * 0.35;
          out.push(`<path d="M${P(r, a0)} ${arc(r, a0, a1)}" fill="none" stroke="${esc(c)}" stroke-width="${num(lw * 2)}" ${at}/>`);
          out.push(`<path class="fig-hit" d="M${P(r, a0)} ${arc(r, a0, a1)}" fill="none" stroke="transparent" stroke-width="6" ${at}/>`);
          if (g.runs.length) {
            const t = radial(r, (a0 + a1) / 2, fs * 0.5);
            out.push(`${t.open}${placedRuns(g.runs, t, `fill="${esc(c)}" ${at}`)}</g>`);
          }
          if (sel?.type === "bar" && sel.index === g.bar) {
            out.push(`<path class="fig-sel" d="${sector(r - 2, r + columnWidth[g.column] - 2, a0 - step * 0.15, a1 + step * 0.15)}" fill="none" stroke="#2f6fd6" stroke-width="0.8" stroke-dasharray="3 2"/>`);
          }
        }
        if (s.branches.lengths && s.scale.show) {  // scale bar under the circle
          const len = scaleLength(), w = (len / maxDepth) * R, sy = H - margin - fs * 1.4;
          out.push(`<line x1="${num(margin)}" y1="${num(sy)}" x2="${num(margin + w)}" y2="${num(sy)}" stroke="#000" stroke-width="${num(lw)}"/>`);
          out.push(`<text x="${num(margin + w / 2)}" y="${num(sy + fs * 1.15)}" ${font} text-anchor="middle">${esc(+len.toPrecision(3))}</text>`);
        }
        return { body: out.join("\n"), width: W, height: H, tipOrder };
      }

      // ── Rectangular: the root at the left, the tips at the right; mirrored,
      // everything's x is taken from the right edge (X) and texts end where they started
      const mirror = !!s.mirror;
      const X = (x) => (mirror ? W - x : x);
      const anchor = (a) => (!mirror ? a : a === "start" ? "end" : a === "end" ? "start" : a);
      const box = (x, w) => (mirror ? W - x - w : x);  // a rectangle's left edge
      const treeWidth = Math.max(40, W - 2 * margin - maxLabel - gap - barColumn);
      const xOf = (i) => margin + frac(i) * treeWidth;
      // where a collapsed clade's triangle ends: its deepest tip, or (a cladogram) the tips' column
      const farEnd = (i) => margin + farFrac(i) * treeWidth;
      const autoHeight = top + (rows - 1) * spacing + fs * 0.7 + scaleRoom + margin;
      const H = s.page.height ? s.page.height * PT_PER_MM : autoHeight;
      const tipX = s.tips.align ? margin + treeWidth : null;
      const labelX = (i) => (tipX ?? (collapsed(i) ? farEnd(i) : xOf(i))) + gap;
      const line = (x1, y1, x2, y2, rest) => `<line x1="${num(X(x1))}" y1="${num(y1)}" x2="${num(X(x2))}" y2="${num(y2)}" ${rest}/>`;

      // backgrounds of highlighted clades, under everything
      for (const i of visible) {
        const h = style(i).highlight;
        if (!h || !kids[i].length) continue;
        const [a, b] = rowsEnd(i);
        const x0 = (i ? (xOf(nodes[i].parent) + xOf(i)) / 2 : margin - 2);
        const x1 = margin + treeWidth + gap + maxLabel + gap / 2;
        out.push(`<rect x="${num(box(x0, x1 - x0))}" y="${num(a - spacing / 2)}" width="${num(x1 - x0)}" height="${num(b - a + spacing)}" fill="${esc(h)}" fill-opacity="0.25"/>`);
      }
      // a mark on the selection (the editor only)
      if (sel?.type === "clade" && tree.byKey.has(sel.key)) {
        const i = tree.byKey.get(sel.key);
        if (visible.includes(i)) {
          const [a, b] = kids[i].length && !collapsed(i) ? rowsEnd(i) : [y[i], y[i]];
          const x0 = xOf(i) - 3, w = margin + treeWidth + gap + maxLabel - x0 + 2;
          out.push(`<rect class="fig-sel" x="${num(box(x0, w))}" y="${num(a - spacing / 2)}" width="${num(w)}" height="${num(b - a + spacing)}" fill="none" stroke="#2f6fd6" stroke-width="0.8" stroke-dasharray="3 2"/>`);
        }
      }
      // branches: a horizontal line to each node, a vertical one joining a node's children
      for (const i of visible) {
        const x = xOf(i);
        if (i > 0) out.push(line(xOf(nodes[i].parent), y[i], x, y[i], `stroke="${color[i]}" stroke-width="${num(lw)}" stroke-linecap="square" data-node="${i}"`));
        if (kids[i].length && !collapsed(i)) {
          // the vertical piece takes each child's colour on its half
          for (const k of order[i].filter((c) => visibleSet.has(c))) {
            out.push(line(x, y[i], x, y[k], `stroke="${color[k]}" stroke-width="${num(lw)}" stroke-linecap="square" data-node="${k}"`));
          }
        }
        if (collapsed(i)) {  // a triangle to the deepest tip
          const x1 = Math.max(farEnd(i), x + fs), h = spacing * 0.42;
          out.push(`<polygon points="${num(X(x))},${num(y[i])} ${num(X(x1))},${num(y[i] - h)} ${num(X(x1))},${num(y[i] + h)}" fill="${color[i]}" fill-opacity="0.35" stroke="${color[i]}" stroke-width="${num(lw)}" data-node="${i}"/>`);
        }
      }
      // invisible wide strokes over the branches: easier to click
      for (const i of visible) {
        if (i) out.push(line(xOf(nodes[i].parent), y[i], xOf(i), y[i], `class="fig-hit" stroke="transparent" stroke-width="6" data-node="${i}"`));
      }
      // dotted guides to names in one column
      if (tipX !== null) {
        for (const [i] of rowOf) {
          const end = collapsed(i) ? farEnd(i) : xOf(i);
          if (tipX - end > 2) out.push(line(end + 1, y[i], tipX, y[i], `stroke="#999" stroke-width="${num(lw * 0.5)}" stroke-dasharray="1 1.5"`));
        }
      }
      // tip names
      for (const [i] of rowOf) {
        const t = labelOf.get(i), x = labelX(i), base = y[i] + fs * 0.35;
        out.push(`<text x="${num(X(x))}" y="${num(base)}" ${font} text-anchor="${anchor("start")}" fill="${esc(tipColor(i))}" ${which(i)}>${runsSVG(t)}</text>`);
        if (marked.has(tipName(i))) {
          const w = labelWidth(t) + 2;
          out.push(`<rect class="fig-sel" x="${num(box(x - 1, w))}" y="${num(y[i] - fs * 0.65)}" width="${num(w)}" height="${num(fs * 1.3)}" fill="none" stroke="#2f6fd6" stroke-width="0.8" stroke-dasharray="3 2"/>`);
        }
      }
      // support values or circles, at the nodes they belong to
      if (s.support.show !== "none") {
        const ss = s.support.size;
        for (const i of visible) {
          const text = supportText(i);
          if (text === null) continue;
          if (s.support.show === "circles") {
            out.push(`<circle cx="${num(X(xOf(i)))}" cy="${num(y[i])}" r="${num(ss * 0.32)}" fill="#000"/>`);
          } else {
            out.push(`<text x="${num(X(xOf(i) - 1))}" y="${num(y[i] - lw - 1)}" font-family="${esc(s.font)}" font-size="${num(ss)}" text-anchor="${anchor("end")}" fill="#333">${esc(text)}</text>`);
          }
        }
      }
      // bars: along their rows, in their columns, their names beside them
      const columnX = [];
      for (let c = 0, x = margin + treeWidth + gap + maxLabel + fs * 0.6; c < columns; c++) { columnX.push(x); x += columnWidth[c]; }
      for (const g of groups) {
        const [a, b] = g.span, x = columnX[g.column], c = g.color || color[g.node];
        const at = g.bar !== undefined ? `data-bar="${g.bar}"` : `data-node="${g.node}"`;
        out.push(line(x, a - spacing * 0.35, x, b + spacing * 0.35, `stroke="${esc(c)}" stroke-width="${num(lw * 2)}" ${at}`));
        out.push(line(x, a - spacing * 0.35, x, b + spacing * 0.35, `class="fig-hit" stroke="transparent" stroke-width="6" ${at}`));
        if (g.runs.length) out.push(`<text x="${num(X(x + fs * 0.5))}" y="${num((a + b) / 2 + fs * 0.35)}" ${font} text-anchor="${anchor("start")}" fill="${esc(c)}" ${at}>${runsSVG(g.runs)}</text>`);
        if (sel?.type === "bar" && sel.index === g.bar) {
          out.push(`<rect class="fig-sel" x="${num(box(x - 2, columnWidth[g.column]))}" y="${num(a - spacing * 0.5)}" width="${num(columnWidth[g.column])}" height="${num(b - a + spacing)}" fill="none" stroke="#2f6fd6" stroke-width="0.8" stroke-dasharray="3 2"/>`);
        }
      }
      // scale bar: a round number of substitutions per site (under the root's side)
      if (s.branches.lengths && s.scale.show) {
        const len = scaleLength(), w = (len / maxDepth) * treeWidth, sy = H - margin - fs * 1.4;
        out.push(line(margin, sy, margin + w, sy, `stroke="#000" stroke-width="${num(lw)}"`));
        out.push(`<text x="${num(X(margin + w / 2))}" y="${num(sy + fs * 1.15)}" ${font} text-anchor="middle">${esc(+len.toPrecision(3))}</text>`);
      }
      return { body: out.join("\n"), width: W, height: H, tipOrder };
    },
  };

  // ── The genome-comparison figure ───────────────────────────────────────
  // Each genome a line with its features as arrows, all at one scale (bp per
  // mm), names at the left; between neighbouring genomes, bands for the blocks
  // progressiveMauve found or for genes of the same name (twisted where one of
  // the two runs the other way). A genome can be flipped (drawn reverse
  // complemented), moved, hidden or renamed; a feature relabelled or recoloured.
  const COMPARE_DEFAULTS = {
    page: { width: 174, height: 0, margin: 4 },
    font: "Arial", fontSize: 7, lineWidth: 0.5,
    names: { label: "name", italic: "organism" },
    order: [], hidden: [], flipped: [], flipsChosen: false, genomeEdits: {},  // genomes: by their index in the comparison; flips: see flipsOf
    track: { height: 6, spacing: 24 },  // pt: the arrows, the room for bands under them
    types: { CDS: true, rRNA: true, tRNA: true, gene: false, other: false },
    colors: { CDS: "#f2d024", rRNA: "#d93b30", tRNA: "#8e44ad", gene: "#3fa34d", other: "#9e9e9e" },
    labels: { show: "genes", size: 5, angle: 45 },
    filters: { minLength: 0, minLabelLength: 0 },  // bp: shorter features not drawn; shorter ones without names
    genes: {},  // gene (geneKey of its name, in every genome) -> { hidden, noLabel, label (its name even if the rules leave it out) }
    links: { mode: "blocks", opacity: 0.3, color: "blocks" },
    scale: { show: true, length: 0 },  // bp; 0: a round number
    featureEdits: {}, blockColors: {},
  };
  // The genomes drawn reverse complemented: the user's choice (any flip or unflip
  // made, flipsChosen), else each genome that mostly runs the other way from the
  // first one shown (by the blocks' lengths), so the bands don't all cross
  function flipsOf(s, source, first) {
    if (s.flipsChosen || s.flipped.length) return s.flipped;
    const way = {};  // genome -> bp in blocks on the same strand as the first, minus the other way
    for (const b of source.blocks || []) {
      const ref = b.parts.find((p) => p.genome === first);
      if (!ref) continue;
      for (const p of b.parts) way[p.genome] = (way[p.genome] || 0) + (p.strand === ref.strand ? 1 : -1) * (p.end - p.start);
    }
    return Object.keys(way).map(Number).filter((g) => way[g] < 0);
  }
  const TYPE_NAMES = { CDS: "CDS", rRNA: "rRNA", tRNA: "tRNA", gene: "gene", other: "other features" };
  const typeOf = (f) => (TYPE_NAMES[f.type] && f.type !== "other" ? f.type : "other");
  const blockHue = (k) => (k * 137.508 + 20) % 360;
  const hsl2hex = (h, s, l) => {  // colours as #rrggbb (colour inputs and PDF want them)
    s /= 100; l /= 100;
    const f = (n) => { const k = (n + h / 30) % 12, c = l - s * Math.min(l, 1 - l) * Math.max(-1, Math.min(k - 3, 9 - k, 1));
                       return Math.round(255 * c).toString(16).padStart(2, "0"); };
    return `#${f(0)}${f(8)}${f(4)}`;
  };

  // Gene names compared across genomes: organelle annotations mix conventions
  // (ND5 / nad5, CYTB / cob, COI / COX1 / cox1, rrnL / rnl). Not 16S/23S/12S:
  // 16S is the large rRNA in mitochondria but the small one in plastids.
  function geneKey(name) {
    return String(name).toLowerCase().replace(/[\s_-]+/g, "")
      .replace(/^nd(\d)/, "nad$1").replace(/^(cytb|cyb)$/, "cob")
      .replace(/^coiii$/, "cox3").replace(/^coii$/, "cox2").replace(/^coi$/, "cox1").replace(/^co(\d)$/, "cox$1")
      .replace(/^(rrnl|lsu)$/, "rnl").replace(/^(rrns|ssu)$/, "rns");
  }

  // What a feature shows, most specific first: the feature's own settings
  // (featureEdits), then its gene's in every genome (genes), then the rules
  // (types shown, sizes, which names)
  const featureLength = (f) => (f.locations?.length ? f.locations : [f]).reduce((n, p) => n + p.end - p.start + 1, 0);
  const geneOf = (s, f) => (f.name ? s.genes[geneKey(f.name)] || {} : {});
  const featureShown = (s, f) => s.types[typeOf(f)] && featureLength(f) >= (s.filters.minLength || 0) && !geneOf(s, f).hidden;
  const ruleNamed = (s, f) => s.labels.show !== "none" && !!f.name && (s.labels.show === "all" || ["CDS", "rRNA"].includes(f.type))
    && featureLength(f) >= (s.filters.minLabelLength || 0);
  function featureLabel(s, key, f) {
    const edit = s.featureEdits[key] || {}, gene = geneOf(s, f);
    if (edit.hideLabel) return "";
    if (edit.label) return edit.label;
    if (gene.noLabel) return "";
    return gene.label || ruleNamed(s, f) ? f.name || "" : "";
  }

  // The genes in the figure (of the types shown), each once across genomes
  function genesIn(s, source) {
    const genes = new Map();
    source.genomes.forEach((g, gi) => {
      if (s.hidden.includes(gi)) return;
      g.data.features.forEach((f) => {
        if (!f.name || !s.types[typeOf(f)]) return;
        const key = geneKey(f.name);
        const gene = genes.get(key) || { key, name: f.name, names: new Map(), genomes: new Set(), longest: 0, named: false };
        gene.names.set(f.name.toLowerCase(), gene.names.get(f.name.toLowerCase()) || f.name);  // ND5, nad5: the ways it's written
        gene.genomes.add(gi);
        gene.longest = Math.max(gene.longest, featureLength(f));
        gene.named ||= featureLabel(s, `${gi}:${g.data.features.indexOf(f)}`, f) !== "";
        genes.set(key, gene);
      });
    });
    for (const g of genes.values()) g.name = [...g.names.values()].join(" · ");
    return [...genes.values()].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }));
  }

  // The Genes list in the panel: shown / name for each gene, in every genome at once
  let geneFilter = "";  // kept while the panel is redrawn
  function geneList(s, source, change) {
    const box = document.createElement("div");
    box.className = "fig-genes";
    const genes = genesIn(s, source);
    box.innerHTML = `<input autocomplete="off" type="search" placeholder="Filter (${genes.length} genes)" value="${esc(geneFilter)}">
      <table><tr><th>Gene</th><th title="Drawn">Shown</th><th title="Its name drawn on it">Name</th></tr>${genes.map((g) => {
        const gene = s.genes[g.key] || {}, short = g.longest < (s.filters.minLength || 0);
        return `<tr data-gene="${esc(g.key)}" data-text="${esc(`${g.name} ${g.key}`.toLowerCase())}"><td${short ? ' class="muted" title="Shorter than the size limit"' : ""}>${esc(g.name)}` +
          `${g.genomes.size > 1 ? ` <span class="muted">×${g.genomes.size}</span>` : ""}</td>` +
          `<td><input type="checkbox" data-what="shown"${gene.hidden ? "" : " checked"}></td>` +
          `<td><input type="checkbox" data-what="name"${g.named ? " checked" : ""}></td></tr>`;
      }).join("")}</table>`;
    const filter = box.querySelector("input[type=search]");
    const apply = () => {
      for (const tr of box.querySelectorAll("tr[data-gene]")) tr.hidden = !!geneFilter && !tr.dataset.text.includes(geneFilter.toLowerCase());
    };
    filter.oninput = () => { geneFilter = filter.value; apply(); };
    apply();
    box.querySelector("table").onchange = (e) => {
      const key = e.target.closest("tr").dataset.gene, on = e.target.checked;
      change((st) => {
        const gene = st.genes[key] ||= {};
        const flag = (k, v) => { if (v) gene[k] = true; else delete gene[k]; };
        if (e.target.dataset.what === "shown") flag("hidden", !on);
        else { flag("noLabel", !on); flag("label", on); }  // as ticked, whatever the rules say
        if (!Object.keys(gene).length) delete st.genes[key];
      });
    };
    return box;
  }

  const comparisonFigure = {
    defaults: COMPARE_DEFAULTS,
    prepare: (source) => source,  // the comparison: { genomes: [{ name, data }], blocks }
    hint: "click a genome's name, a feature or a band to set it",

    select(el) {
      if (el.dataset.genome !== undefined) return { type: "genome", index: Number(el.dataset.genome) };
      if (el.dataset.feature !== undefined) return { type: "feature", key: el.dataset.feature };
      if (el.dataset.block !== undefined) return { type: "block", index: Number(el.dataset.block) };
      return null;
    },

    // The genomes drawn, top to bottom
    shown(s, source) {
      const all = source.genomes.map((g, i) => i);
      const order = [...s.order.filter((i) => i < all.length), ...all.filter((i) => !s.order.includes(i))];
      return order.filter((i) => !s.hidden.includes(i));
    },

    panel(s, sel, source) {
      const sections = [
        { title: "Page", fields: [
          { path: ["page", "width"], label: "Width (mm)", type: "number", min: 30, max: 400, step: 1,
            help: "Journals: about 84 mm for one column, 174 mm for the full width" },
          { path: ["page", "height"], label: "Height (mm, 0 = as needed)", type: "number", min: 0, max: 600, step: 1 },
          { path: ["font"], label: "Font", type: "select", options: FONTS.map((f) => [f, f]) },
          { path: ["fontSize"], label: "Names: font size (pt)", type: "number", min: 4, max: 24, step: 0.5 },
          { path: ["lineWidth"], label: "Line width (pt)", type: "number", min: 0.1, max: 4, step: 0.05 },
        ] },
        { title: "Genomes", fields: [
          { path: ["names", "label"], label: "Names", type: "select", options: Object.entries(LABELS).filter(([k]) => k !== "description") },
          { path: ["names", "italic"], label: "Italic", type: "select",
            options: [["none", "None"], ["organism", "Species names (genus and epithets, not sp., cf., codes)"], ["all", "All of the name"]] },
          { path: ["track", "height"], label: "Arrow height (pt)", type: "number", min: 2, max: 30, step: 0.5 },
          { path: ["track", "spacing"], label: "Space between genomes (pt)", type: "number", min: 4, max: 120, step: 1 },
          { path: ["scale", "show"], label: "Scale bar", type: "check" },
          { path: ["scale", "length"], label: "Scale bar length (bp, 0 = a round number)", type: "number", min: 0, step: 1, hidden: !s.scale.show },
        ] },
        { title: "Features", fields: [...Object.entries(TYPE_NAMES).flatMap(([t, name]) => [
          { path: ["types", t], label: `Show ${name}`, type: "check" },
          { path: ["colors", t], label: `${/^[a-z]+/.test(name) ? name[0].toUpperCase() + name.slice(1) : name} colour`, type: "color", hidden: !s.types[t] },
        ]),
          { path: ["filters", "minLength"], label: "Hide features shorter than (bp)", type: "number", min: 0, step: 10 },
        ] },
        { title: "Feature names", fields: [
          { path: ["labels", "show"], label: "Show", type: "select",
            options: [["genes", "Genes (CDS and rRNA names)"], ["all", "All shown features with a name"], ["none", "None"]] },
          { path: ["filters", "minLabelLength"], label: "Only on features of at least (bp; e.g. 300 leaves tRNAs out)", type: "number",
            min: 0, step: 10, hidden: s.labels.show === "none" },
          { path: ["labels", "size"], label: "Size (pt)", type: "number", min: 3, max: 14, step: 0.5, hidden: s.labels.show === "none" },
          { path: ["labels", "angle"], label: "Slant (degrees)", type: "number", min: 0, max: 90, step: 5, hidden: s.labels.show === "none" },
        ] },
        { title: "Links between genomes", fields: [
          { path: ["links", "mode"], label: "Bands for", type: "select",
            options: [["blocks", "The blocks they share (progressiveMauve)"], ["genes", "Genes of the same name"], ["none", "Nothing"]] },
          { path: ["links", "color"], label: "Colours", type: "select", hidden: s.links.mode !== "blocks",
            options: [["blocks", "A colour per block"], ["grey", "Grey"]] },
          { path: ["links", "opacity"], label: "Opacity (0–1)", type: "number", min: 0.05, max: 1, step: 0.05, hidden: s.links.mode === "none" },
        ] },
        { title: "Genes (in every genome)", fields: [
          { type: "note", label: "Untick a gene to hide it, or its name, in all genomes; ND5 and nad5 count as one gene." },
          { type: "custom", render: (change) => geneList(s, source, change) },
        ] },
      ];
      if (sel?.type === "genome") {
        const g = source.genomes[sel.index], base = ["genomeEdits", String(sel.index)];
        sections.unshift({ title: `Selected genome: ${g.name}`, selection: true, fields: [
          { path: [...base, "text"], label: "Name shown (empty: as the others; *italic*, **bold**)", type: "text" },
          { label: flipsOf(s, source, this.shown(s, source)[0]).includes(sel.index) ? "Unflip (back to its own strand)" : "Flip (draw it reverse complemented)", type: "button", action: "flip" },
          { label: "Move up", type: "button", action: "up" },
          { label: "Move down", type: "button", action: "down" },
          { label: "Hide it", type: "button", action: "hide" },
        ] });
      } else if (sel?.type === "feature") {
        const [gi, fi] = sel.key.split(":").map(Number), f = source.genomes[gi]?.data.features[fi];
        const base = ["featureEdits", sel.key];
        sections.unshift({ title: `Selected: ${f?.type || ""} ${f?.name || ""}`, selection: true, fields: [
          { path: [...base, "label"], label: "Name shown (empty: its own)", type: "text" },
          { path: [...base, "hideLabel"], label: "No name on it", type: "check" },
          { path: [...base, "color"], label: "Colour", type: "color" },
          ...(f?.name ? [
            { label: s.genes[geneKey(f.name)]?.noLabel ? `Names of ${f.name} back in every genome` : `Hide the name of ${f.name} in every genome`, type: "button", action: "geneName" },
            { label: `Hide ${f.name} in every genome`, type: "button", action: "geneHide" }] : []),
        ] });
      } else if (sel?.type === "block") {
        sections.unshift({ title: `Selected block ${sel.index + 1}`, selection: true, fields: [
          { path: ["blockColors", String(sel.index)], label: "Colour", type: "color" },
        ] });
      }
      if (s.hidden.length) {
        sections.push({ title: "Hidden genomes", fields: [{ label: `Show the ${s.hidden.length} hidden again`, type: "button", action: "unhide" }] });
      }
      return sections;
    },

    action(name, s, sel, source) {
      const order = this.shown({ ...s, hidden: [] }, source);  // the whole order, hidden ones included
      if (name === "flip") {
        const now = flipsOf(s, source, order.find((i) => !s.hidden.includes(i)));
        s.flipped = now.includes(sel.index) ? now.filter((i) => i !== sel.index) : [...now, sel.index];
        s.flipsChosen = true;
      } else if (name === "up" || name === "down") {
        const at = order.indexOf(sel.index), to = at + (name === "up" ? -1 : 1);
        if (to >= 0 && to < order.length) [order[at], order[to]] = [order[to], order[at]];
        s.order = order;
      } else if (name === "hide") {
        s.hidden = [...s.hidden, sel.index];
      } else if (name === "unhide") {
        s.hidden = [];
      } else if (name === "geneName" || name === "geneHide") {  // the selected feature's gene, in every genome
        const [gi, fi] = sel.key.split(":").map(Number), f = source.genomes[gi].data.features[fi];
        const gene = s.genes[geneKey(f.name)] ||= {};
        if (name === "geneHide") { gene.hidden = true; return null; }
        if (gene.noLabel) delete gene.noLabel; else gene.noLabel = true;
        delete gene.label;
      }
    },

    draw(s, source, sel) {
      const shown = this.shown(s, source);
      const fs = s.fontSize, lw = s.lineWidth, ls = s.labels.size;
      const W = s.page.width * PT_PER_MM, margin = s.page.margin * PT_PER_MM;
      const genomes = source.genomes;
      // names in a column at the left
      const nameOf = (i) => {  // runs: species names in italics (or all, or none); an edited name with *markup*
        const g = genomes[i], edit = s.genomeEdits[i] || {}, mode = s.names.italic;
        if (edit.text) return markupRuns(edit.text, { italic: mode === "all" });
        const org = g.data.organism || "", words = g.name.replace(/_/g, " ");
        const text = s.names.label === "organism" ? (org || words) : s.names.label === "organism name" ? (org ? `${org} ${words}` : words) : words;
        if (mode === "all") return [{ text, italic: true }];
        if (mode !== "organism") return [{ text }];
        return s.names.label === "organism name" && org ? [...speciesRuns(org), { text: ` ${words}` }] : speciesRuns(text);
      };
      const width = (runs) => runsWidth(runs, fs, s.font);
      const names = new Map(shown.map((i) => [i, nameOf(i)]));
      const nameW = Math.max(0, ...[...names.values()].map(width)) + fs;
      const x0 = margin + nameW, drawW = Math.max(40, W - x0 - margin);
      const maxLen = Math.max(1, ...shown.map((i) => genomes[i].data.sequence.length));
      const perBp = drawW / maxLen;
      const flips = flipsOf(s, source, shown[0]), flipped = (i) => flips.includes(i);
      const len = (i) => genomes[i].data.sequence.length;
      // a genome's stretch from..to (0-based, end exclusive) as x0..x1, left to right
      const span = (i, a, b) => (flipped(i) ? [x0 + (len(i) - b) * perBp, x0 + (len(i) - a) * perBp] : [x0 + a * perBp, x0 + b * perBp]);

      // which features, their names
      const shownFeature = (f) => featureShown(s, f);
      const labelOf = (gi, fi, f) => featureLabel(s, `${gi}:${fi}`, f);
      // rows: the names' room above the arrows (slanted names rise), the arrows, the room for bands
      const th = s.track.height;
      const rad = (s.labels.angle * Math.PI) / 180;
      const rise = (i) => {
        let most = 0;
        genomes[i].data.features.forEach((f, fi) => {
          const text = shownFeature(f) && labelOf(i, fi, f);
          if (text) most = Math.max(most, textWidth(text, ls, s.font) * Math.sin(rad) + ls * Math.cos(rad));
        });
        return most ? most + 2 : 0;
      };
      const rows = new Map();
      let y = margin;
      for (const i of shown) {
        const up = rise(i);
        rows.set(i, { top: y, mid: y + up + th / 2 });
        y += up + th + s.track.spacing;
      }
      y -= s.track.spacing;
      const scaleRoom = s.scale.show ? fs * 3 : 0;
      const H = s.page.height ? s.page.height * PT_PER_MM : y + fs + scaleRoom + margin;
      const out = [];
      const font = (size) => `font-family="${esc(s.font)}" font-size="${num(size)}"`;

      // bands between neighbouring genomes, under everything else
      const band = (a, b, ya, yb, crossed, fill, attrs) => {
        const [a0, a1] = a, [b0, b1] = b;
        out.push(`<polygon points="${num(a0)},${num(ya)} ${num(a1)},${num(ya)} ${num(crossed ? b0 : b1)},${num(yb)} ${num(crossed ? b1 : b0)},${num(yb)}" ` +
                 `fill="${fill}" fill-opacity="${num(s.links.opacity)}" ${attrs}/>`);
      };
      for (let k = 0; k + 1 < shown.length; k++) {
        const ga = shown[k], gb = shown[k + 1];
        const ya = rows.get(ga).mid + th / 2, yb = rows.get(gb).mid - th / 2;
        if (s.links.mode === "blocks") {
          source.blocks.forEach((block, bi) => {
            const pa = block.parts.find((p) => p.genome === ga), pb = block.parts.find((p) => p.genome === gb);
            if (!pa || !pb) return;
            const crossed = (pa.strand * (flipped(ga) ? -1 : 1)) !== (pb.strand * (flipped(gb) ? -1 : 1));
            const fill = s.blockColors[bi] || (s.links.color === "grey" ? "#888888" : hsl2hex(blockHue(bi), 60, 55));
            const marked = sel?.type === "block" && sel.index === bi;
            band(span(ga, pa.start - 1, pa.end), span(gb, pb.start - 1, pb.end), ya, yb, crossed, fill,
                 `data-block="${bi}"${marked ? ' stroke="#2f6fd6" stroke-width="0.8" stroke-dasharray="3 2"' : ""}`);
          });
        } else if (s.links.mode === "genes") {
          const byName = new Map();
          genomes[gb].data.features.forEach((f) => {
            if (shownFeature(f) && f.name && !byName.has(geneKey(f.name) + f.type)) byName.set(geneKey(f.name) + f.type, f);
          });
          const seen = new Set();
          genomes[ga].data.features.forEach((f, fi) => {
            const key = f.name && geneKey(f.name) + f.type;  // the same gene, as the same kind of feature
            const other = key && shownFeature(f) && byName.get(key);
            if (!other || seen.has(key)) return;
            seen.add(key);
            const crossed = (f.strand * (flipped(ga) ? -1 : 1)) !== (other.strand * (flipped(gb) ? -1 : 1));
            const color = s.featureEdits[`${ga}:${fi}`]?.color || s.colors[typeOf(f)];
            band(span(ga, f.start, f.end + 1), span(gb, other.start, other.end + 1), ya, yb, crossed, color, "");
          });
        }
      }
      // each genome: its name, its line, its features as arrows, their names
      for (const i of shown) {
        const { mid } = rows.get(i), t = names.get(i);
        const baseY = mid + fs * 0.35;
        out.push(`<text x="${num(margin)}" y="${num(baseY)}" ${font(fs)} data-genome="${i}">${runsSVG(t)}</text>`);
        if (sel?.type === "genome" && sel.index === i) {
          out.push(`<rect x="${num(margin - 1)}" y="${num(mid - th / 2 - 2)}" width="${num(x0 + len(i) * perBp - margin + 2)}" height="${num(th + 4)}" fill="none" stroke="#2f6fd6" stroke-width="0.8" stroke-dasharray="3 2"/>`);
        }
        const [l0, l1] = span(i, 0, len(i));
        out.push(`<line x1="${num(l0)}" y1="${num(mid)}" x2="${num(l1)}" y2="${num(mid)}" stroke="#777" stroke-width="${num(lw)}" data-genome="${i}"/>`);
        const labels = [];
        genomes[i].data.features.forEach((f, fi) => {
          if (!shownFeature(f)) return;
          const key = `${i}:${fi}`, edit = s.featureEdits[key] || {};
          const color = edit.color || s.colors[typeOf(f)];
          const parts = (f.locations && f.locations.length ? f.locations : [f]).map((p) => span(i, p.start, p.end + 1)).sort((a, b) => a[0] - b[0]);
          const strand = f.strand * (flipped(i) ? -1 : 1);  // the way the arrow points on the page
          const tip = strand === 1 ? parts.length - 1 : strand === -1 ? 0 : -1;
          parts.forEach(([a, b], pi) => {
            const w = Math.max(0.3, b - a), head = pi === tip ? Math.min(th * 0.7, w * 0.6) : 0;
            const t0 = mid - th / 2, t1 = mid + th / 2;
            const pts = strand === 1 && head ? [[a, t0], [b - head, t0], [b, mid], [b - head, t1], [a, t1]]
              : strand === -1 && head ? [[a + head, t0], [b, t0], [b, t1], [a + head, t1], [a, mid]]
                : [[a, t0], [a + w, t0], [a + w, t1], [a, t1]];
            out.push(`<polygon points="${pts.map(([x, yy]) => `${num(x)},${num(yy)}`).join(" ")}" fill="${esc(color)}" stroke="#333" stroke-width="${num(lw * 0.6)}" data-feature="${key}"/>`);
          });
          for (let pi = 1; pi < parts.length; pi++) {  // introns: a thin line between the parts
            out.push(`<line x1="${num(parts[pi - 1][1])}" y1="${num(mid)}" x2="${num(parts[pi][0])}" y2="${num(mid)}" stroke="${esc(color)}" stroke-width="${num(lw)}"/>`);
          }
          if (sel?.type === "feature" && sel.key === key) {
            out.push(`<rect x="${num(parts[0][0] - 1.5)}" y="${num(mid - th / 2 - 1.5)}" width="${num(parts[parts.length - 1][1] - parts[0][0] + 3)}" height="${num(th + 3)}" fill="none" stroke="#2f6fd6" stroke-width="0.8" stroke-dasharray="3 2"/>`);
          }
          const text = labelOf(i, fi, f);
          if (text) labels.push({ text, x: (parts[0][0] + parts[parts.length - 1][1]) / 2, key });
        });
        const ly = mid - th / 2 - 1.5;
        for (const l of labels) {
          const transform = s.labels.angle ? ` transform="rotate(${num(-s.labels.angle)} ${num(l.x)} ${num(ly)})"` : "";
          out.push(`<text x="${num(l.x)}" y="${num(ly)}" ${font(ls)}${s.labels.angle ? "" : ' text-anchor="middle"'}${transform} data-feature="${l.key}">${esc(l.text)}</text>`);
        }
      }
      // scale bar: a round number of bases
      if (s.scale.show) {
        let bp = s.scale.length;
        if (!bp) {
          const target = maxLen / 5, p = 10 ** Math.floor(Math.log10(target));
          bp = [1, 2, 5, 10].map((m) => m * p).reduce((a, b) => (Math.abs(b - target) < Math.abs(a - target) ? b : a));
        }
        const sy = H - margin - fs * 1.6, w = bp * perBp;
        out.push(`<line x1="${num(x0)}" y1="${num(sy)}" x2="${num(x0 + w)}" y2="${num(sy)}" stroke="#000" stroke-width="${num(lw * 1.5)}"/>`);
        const label = bp >= 1000 ? `${+(bp / 1000).toPrecision(3)} kb` : `${bp} bp`;
        out.push(`<text x="${num(x0 + w / 2)}" y="${num(sy + fs * 1.2)}" ${font(fs)} text-anchor="middle">${label}</text>`);
      }
      return { body: out.join("\n"), width: W, height: H };
    },
  };

  const KINDS = { tree: treeFigure, comparison: comparisonFigure,
    genome: window.makeGenomeFigure({ PT_PER_MM, FONTS, esc, num, clone, textWidth, markupRuns, speciesRuns, runsWidth }),
    dotplot: window.makeDotplotFigure({ PT_PER_MM, FONTS, esc, num, textWidth, markupRuns, runsWidth }) };

  // ── The editor ─────────────────────────────────────────────────────────
  class FigureEditor {
    constructor(container, options) {
      this.container = container;
      this.options = options;
      this.saveQueue = Promise.resolve();
      this.pendingSave = null;
      this.zoom = null;  // null: fit the whole page
      // Its controls: in the viewer's bar when the page gives one (options.bar), else above the page
      const icon = (name, text) => (typeof ICONS !== "undefined" && ICONS[name]) || text;
      const bar = options.bar || document.createElement("div");
      bar.innerHTML = `
          <span class="group viewer-zoom" aria-label="Zoom"><span class="group-label">Zoom</span>
            <button class="secondary icon-button" data-act="out" title="Zoom out" aria-label="Zoom out">${icon("minus", "−")}</button>
            <button class="secondary icon-button" data-act="in" title="Zoom in" aria-label="Zoom in">${icon("plus", "+")}</button>
            <button class="secondary icon-button" data-act="fit" title="Fit the whole page" aria-label="Fit whole">${icon("whole", "Fit whole")}</button>
            <button class="secondary icon-button" data-act="selection" disabled title="Fit the selected figure element" aria-label="Fit selection">${icon("selection", "Fit selection")}</button>
          </span>
          <span class="group"><span class="group-label">Edit</span>
            <button class="secondary icon-button" data-act="undo" title="Undo (Ctrl+Z)" aria-label="Undo">${icon("undo", "Undo")}</button>
            <button class="secondary icon-button" data-act="redo" title="Redo (Ctrl+Y)" aria-label="Redo">${icon("redo", "Redo")}</button>
          </span>
          <span class="group fig-export"><span class="group-label">Export</span>
            <button class="secondary" data-export="svg" title="Vector; editable in Inkscape or Illustrator">SVG</button>
            <button class="secondary" data-export="pdf" title="Vector">PDF</button>
            <button class="secondary" data-export="png" title="A bitmap at the resolution chosen">PNG</button>
            <button class="secondary" data-export="tiff" title="A bitmap at the resolution chosen, LZW-compressed">TIFF</button>
            <select class="fig-dpi" title="Resolution of PNG and TIFF"><option value="300">300 dpi</option><option value="600" selected>600 dpi</option><option value="1200">1200 dpi</option></select>
          </span>
          <span class="group fig-size muted"></span>`;
      this.bar = bar;
      container.innerHTML = `        <div class="fig-body">
          <div class="fig-canvas"><div class="fig-page"></div></div>
          <aside class="fig-panel"></aside>
        </div>`;
      if (!options.bar) container.prepend(bar);
      this.page = container.querySelector(".fig-page");
      this.panelEl = container.querySelector(".fig-panel");
      bar.onclick = (e) => {
        const b = e.target.closest("button");
        if (!b) return;
        if (b.dataset.export) return this.export(b.dataset.export);
        ({ undo: () => this.undo(), redo: () => this.redo(), in: () => this.setZoom(1.25), out: () => this.setZoom(0.8),
           selection: () => this.fitSelection(),
           fit: () => { this.zoom = null; this.render(); this.page.parentElement.scrollTo(0, 0); } })[b.dataset.act]?.();
      };
      this.page.onclick = (e) => this.pick(e);
      container.addEventListener("keydown", (e) => {
        if (!(e.ctrlKey || e.metaKey) || e.target.closest("input, select")) return;
        if (e.key === "z") { e.preventDefault(); this.undo(); }
        if (e.key === "y") { e.preventDefault(); this.redo(); }
      });
      new ResizeObserver(() => { if (this.zoom === null && this.kind) this.render(); }).observe(container.querySelector(".fig-canvas"));
    }

    // figure: { of, settings, title }; source: what it is drawn from ({ nodes } for a tree)
    open(figure, source) {
      this.itemId = figure.itemId;
      this.kind = KINDS[figure.of];
      this.source = this.kind.prepare(source);
      this.settings = merged(merged(this.kind.defaults, this.kind.initial?.(this.source)), figure.settings);
      this.sel = null;
      this.history = [];
      this.future = [];
      this.render();
      this.renderPanel();
    }

    // Nothing to draw (a figure whose source is gone)
    clear() {
      this.kind = null;
      this.page.innerHTML = "";
      this.panelEl.innerHTML = "";
    }

    // A change: one undo step, saved a little later
    change(fn) {
      this.history.push(JSON.stringify(this.settings));
      this.future = [];
      fn(this.settings);
      this.changed();
    }
    changed() {
      this.render();
      this.renderPanel();
      clearTimeout(this.saveTimer);
      this.pendingSave = { itemId: this.itemId, settings: JSON.parse(JSON.stringify(this.settings)) };
      this.saveTimer = setTimeout(() => { this.flush().catch(() => {}); }, SAVE_DELAY);
    }
    flush() {
      clearTimeout(this.saveTimer);
      if (this.pendingSave) {
        const pending = this.pendingSave;
        this.pendingSave = null;
        this.saveQueue = this.saveQueue.catch(() => {}).then(() => this.options.onSave(pending.settings, pending.itemId));
        this.saveQueue.catch((e) => this.options.onError?.(`Figure save failed: ${e.message}`));
      }
      return this.saveQueue;
    }
    undo() {
      if (!this.history.length) return;
      this.future.push(JSON.stringify(this.settings));
      this.settings = JSON.parse(this.history.pop());
      this.changed();
    }
    redo() {
      if (!this.future.length) return;
      this.history.push(JSON.stringify(this.settings));
      this.settings = JSON.parse(this.future.pop());
      this.changed();
    }

    svg(forExport) {
      const d = this.kind.draw(this.settings, this.source, forExport ? null : this.sel, this.options.lookup);
      this.size = d;
      const mm = (pt) => num(pt / PT_PER_MM);
      return `<svg xmlns="http://www.w3.org/2000/svg" width="${mm(d.width)}mm" height="${mm(d.height)}mm" ` +
        `viewBox="0 0 ${num(d.width)} ${num(d.height)}">\n<rect width="100%" height="100%" fill="#fff"/>\n${d.body}\n</svg>`;
    }

    render() {
      this.page.innerHTML = this.svg(false);
      const svg = this.page.firstElementChild, d = this.size;
      const scale = this.zoom ?? this.fitScale();
      svg.style.width = `${d.width * scale}px`;
      svg.style.height = `${d.height * scale}px`;
      const size = this.bar.querySelector(".fig-size");  // the page size; how to select things on hover
      size.textContent = `${Math.round(d.width / PT_PER_MM)} × ${Math.round(d.height / PT_PER_MM)} mm`;
      size.title = this.kind.hint;
      this.bar.querySelector('[data-act="selection"]').disabled = !this.selectionElements().length;
      this.bar.querySelector('[data-act="undo"]').disabled = !this.history.length;
      this.bar.querySelector('[data-act="redo"]').disabled = !this.future.length;
    }

    fitScale() {
      const canvas = this.container.querySelector(".fig-canvas");
      return Math.max(.001, Math.min(Math.max(1, canvas.clientWidth - 40) / this.size.width,
        Math.max(1, canvas.clientHeight - 40) / this.size.height));
    }

    setZoom(factor) {
      this.zoom = Math.min(20, Math.max(Math.min(.02, this.fitScale() / 4), (this.zoom ?? this.fitScale()) * factor));
      this.render();
    }

    selectionElements() {
      const s = this.sel;
      if (!s) return [];
      const tree = this.source.tree;
      const nodes = new Set(), tips = new Set(s.type === "tip" ? [s.name] : s.type === "tips" ? s.names : []);
      if (s.type === "clade" && tree?.byKey.has(s.key)) {
        const stack = [tree.byKey.get(s.key)];
        while (stack.length) { const i = stack.pop(); nodes.add(String(i)); stack.push(...tree.kids[i]); }
        for (const name of tree.tips[tree.byKey.get(s.key)]) tips.add(name);
      }
      return [...this.page.querySelectorAll("[data-node], [data-tip], [data-bar], [data-genome], [data-feature], [data-block], [data-centre], [data-class], [data-region]")].filter(el => {
        const d = el.dataset;
        return nodes.has(d.node) || tips.has(d.tip) ||
          (["bar", "genome", "block"].includes(s.type) && d[s.type] === String(s.index)) ||
          (["feature", "centre", "class", "region"].includes(s.type) && d[s.type] === s.key);
      });
    }

    fitSelection() {
      const elements = this.selectionElements();
      if (!elements.length) return;
      const svg = this.page.firstElementChild, rect = svg.getBoundingClientRect();
      const scale = rect.width / this.size.width;
      const boxes = elements.map(el => el.getBoundingClientRect());
      const x0 = (Math.min(...boxes.map(b => b.left)) - rect.left) / scale;
      const x1 = (Math.max(...boxes.map(b => b.right)) - rect.left) / scale;
      const y0 = (Math.min(...boxes.map(b => b.top)) - rect.top) / scale;
      const y1 = (Math.max(...boxes.map(b => b.bottom)) - rect.top) / scale;
      const canvas = this.page.parentElement;
      this.zoom = Math.max(.001, Math.min(20, (canvas.clientWidth - 60) / Math.max(1, x1 - x0),
        (canvas.clientHeight - 60) / Math.max(1, y1 - y0)));
      this.render();
      canvas.scrollTo(this.page.offsetLeft + (x0 + x1) / 2 * this.zoom - canvas.clientWidth / 2,
        this.page.offsetTop + (y0 + y1) / 2 * this.zoom - canvas.clientHeight / 2);
    }

    // A click on the figure: what it selects is the kind's to say (data-* attributes)
    pick(e) {
      const el = e.target.closest("[data-node], [data-tip], [data-bar], [data-genome], [data-feature], [data-block], [data-centre], [data-class], [data-region]");
      this.sel = el ? this.kind.select(el, this.source, this.sel, e, this.size) : null;
      this.render();
      this.renderPanel();
    }

    renderPanel() {
      const s = this.settings;
      const sections = this.kind.panel(s, this.sel, this.source);
      const panel = this.panelEl, scrolled = panel.scrollTop;  // (redrawn on every change: stay where you were)
      panel.innerHTML = "";
      for (const sec of sections) {
        const box = document.createElement("section");
        if (sec.selection) box.className = "fig-selection";
        box.innerHTML = `<h4>${esc(sec.title)}</h4>`;
        for (const f of sec.fields) {
          if (f.hidden) continue;
          box.append(this.field(f));
        }
        panel.append(box);
      }
      panel.scrollTop = scrolled;
    }

    // One setting's control; a change to it is an undo step
    field(f) {
      const s = this.settings;
      const set = (value) => this.change((st) => setPath(st, f.path, value));
      if (f.type === "button") {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "secondary";
        b.textContent = f.label;
        b.onclick = () => this.change((st) => {
          const next = this.kind.action(f.action, st, this.sel, this.source);
          if (next !== undefined) this.sel = next;  // an action may select what it made (or nothing)
        });
        return b;
      }
      if (f.type === "custom") return f.render((fn) => this.change(fn));  // a kind's own control (e.g. the genes list)
      if (f.type === "note") {
        const p = document.createElement("p");
        p.className = "muted";
        p.textContent = f.label;
        return p;
      }
      const label = document.createElement("label");
      const value = getPath(s, f.path);
      if (f.help) label.title = f.help;
      if (f.type === "check") {
        label.className = "check";
        label.innerHTML = `<input type="checkbox"${value ? " checked" : ""}> ${esc(f.label)}`;
        label.querySelector("input").onchange = (e) => set(e.target.checked);
        return label;
      }
      label.textContent = f.label;
      let input;
      if (f.type === "select") {
        input = document.createElement("select");
        for (const [v, t] of f.options) input.append(new Option(t, v));
        input.value = value ?? f.options[0][0];
        input.onchange = () => set(input.value || undefined);
      } else if (f.type === "color") {
        // a colour, or none: a tick to use one, and the colour
        const row = document.createElement("span");
        row.className = "fig-color";
        row.innerHTML = `<input type="checkbox"${value ? " checked" : ""}> <input type="color" value="${esc(value || "#c0392b")}">`;
        const [on, pick] = row.querySelectorAll("input");
        on.setAttribute("aria-label", `Use ${f.label.toLowerCase()}`);
        pick.setAttribute("aria-label", f.label);
        on.onchange = () => set(on.checked ? pick.value : undefined);
        pick.oninput = () => { on.checked = true; };
        pick.onchange = () => set(pick.value);
        label.append(row);
        return label;
      } else {
        input = document.createElement("input"); input.autocomplete = "off";
        input.type = f.type === "number" ? "number" : "text";
        if (f.type === "number") Object.assign(input, { min: f.min ?? "", max: f.max ?? "", step: f.step ?? "any" });
        input.value = value ?? "";
        input.onchange = () => {
          if (f.type !== "number") return set(input.value || undefined);
          const n = Number(input.value);
          if (input.value !== "" && Number.isFinite(n)) set(n);
        };
      }
      input.setAttribute("aria-label", f.label);
      label.append(input);
      return label;
    }

    // SVG and PDF from the SVG; PNG and TIFF from the SVG drawn on a canvas at the dpi
    async export(format) {
      const svg = this.svg(true);
      const dpi = Number(this.bar.querySelector(".fig-dpi").value);
      let png = "";
      if (format === "png" || format === "tiff") {
        const d = this.size;
        const w = Math.round((d.width / 72) * dpi), h = Math.round((d.height / 72) * dpi);
        if (w * h > 120e6) return this.options.onError("Too large at this resolution: choose a lower one");
        const img = new Image();
        img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
        await img.decode();
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(img, 0, 0, w, h);
        png = canvas.toDataURL("image/png").split(",")[1];
      }
      this.options.onExport(format, { svg, png, dpi });
    }
  }

  FigureEditor.kinds = KINDS;
  window.FigureEditor = FigureEditor;
})();
