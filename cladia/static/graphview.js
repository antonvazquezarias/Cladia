// Cladia assembly graph view: each group of linked nodes as parallel lines, laid
// out on the server (graph.layout): the group's longest path is the main line in
// the middle, the other nodes stack above and below it where they run beside it,
// links are short hooks from a node's end to the next one's start; a group whose
// main line closes on itself is drawn as rings. Nodes are coloured by depth, a
// reference's matches are a strip on the nodes. Bases are the sequence viewer's:
// double-click a node to open its track there.
//
//   const view = new GraphView(container, { onNodeMenu(e, node), onOpen(node), onChange(chosen) });
//   view.setData(data);   // /api/graph/{id}/layout
//   node: { id, group, o, x0, x1, lane, length, depth, color, hits }

(function () {
  const FONT = "'Segoe UI', system-ui, sans-serif";
  const HOOK = "#8a9099", MUTED = "#687280", SELECTED = "#d81b60", STATUS = 20;
  const LINE = 5;        // a node's thickness
  const LANE = 11;       // between parallel lines
  const MARGIN = 16;
  const HEAD = 20;       // a group's name and description, above it
  const BETWEEN = 16;    // between groups (twice that beside each other)
  const LABEL = `10.5px ${"'Segoe UI', system-ui, sans-serif"}`;
  const MOST = 8;        // the furthest zoom in (at least 3 x the fit, for a graph shown magnified)
  const FILL = 3;        // the most a small graph is magnified to fill the view
  const BAR = 6;
  const HINT = "Click a node to select it (Ctrl+click: add, Shift+click: shortest path) · double-click: its track in the sequence view · " +
               "right-click: menu · drag: move around · wheel: scroll · Ctrl+wheel: zoom";
  const kb = (bp) => (bp >= 10000 ? `${(bp / 1000).toFixed(1)} kb` : `${bp.toLocaleString()} bp`);

  // A twisted link whose two nodes are on different lines and close in x: drawn as a bracket
  // between the two ends (else as an arc over the group). From the layout's data (a, b: the
  // two nodes' places) or, in drawing, the link's two nodes
  function nearTwist(nodes, l, a = nodes?.get?.(l[0]) ?? nodes?.[l[0]], b = nodes?.get?.(l[2]) ?? nodes?.[l[2]]) {
    if (!a || !b) return false;
    const xa = l.sa ?? l[1], xb = l.sb ?? l[3];
    const ex = (n, side) => (side === "end" ? n.x1 : n.x0);
    return a.lane !== b.lane && Math.abs(ex(a, xa) - ex(b, xb)) < 60;
  }

  class GraphView {
    constructor(container, options = {}) {
      this.container = container;
      this.options = options;
      this.canvas = document.createElement("canvas");
      this.canvas.style.display = "block";
      this.canvas.tabIndex = 0;
      container.append(this.canvas);
      this.ctx = this.canvas.getContext("2d");
      this.groups = [];
      this.nodes = [];
      this.scale = 1;
      this.ox = this.oy = 0;
      this.width_ = this.height_ = 0;
      this.selected = this.hovered = null;
      this.pathMode = false;     // picking a path: steps {node, dir: "right" | "left", gap} through the graph
      this.path = [];
      this.allowGaps = false;
      this.hoverEnd = null;      // the node end the pointer would enter at: {node, end}
      this.chosen = new Set();   // the selected nodes; `selected` is the last one clicked, `anchor` where a Shift+click starts
      this.anchor = null;
      this.canvas.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
      this.canvas.addEventListener("mousedown", (e) => this.onMouseDown(e));
      this.canvas.addEventListener("dblclick", (e) => { const n = this.nodeAt(e); if (n) this.options.onOpen?.(n); });
      this.canvas.addEventListener("mousemove", (e) => this.onHover(e));
      this.canvas.addEventListener("mouseleave", () => { this.hovered = this.hoverEnd = null; this.draw(); });
      this.canvas.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c" && (this.chosen.size || this.hovered)) {
          e.preventDefault();  // the selected nodes' names (else the one under the mouse), a line each
          this.options.onCopy?.([...(this.chosen.size ? this.chosen : [this.hovered])].map((n) => n.id));
        }
        if (this.pathMode) {
          if (e.key === "Escape") this.pathClear();
          else if (e.key === "Backspace") { e.preventDefault(); this.pathUndo(); }
        } else if (e.key === "Escape") this.choose([]);
      });
      this.canvas.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const n = this.nodeAt(e);
        if (!n) return;
        if (!this.chosen.has(n)) this.choose([n]);  // (a node outside the selection: selected first)
        this.options.onNodeMenu?.(e, n);
      });
      this.resize();
      new ResizeObserver(() => this.resize()).observe(container);
    }

    // ── Layout: groups packed in rows (each as wide as its drawing) ────────
    setData(data) {
      this.data = data;
      this.groups = [];
      this.nodes = [];
      for (const g of data.groups) {
        const places = Object.entries(g.nodes).map(([id, p]) => ({ id, ...p }));
        const low = Math.min(...places.map((p) => p.lane)), high = Math.max(...places.map((p) => p.lane));
        const group = { ...g, low, high, nodes: new Map(), head: places.length > 1 ? HEAD : 6 };  // (a lone node: its label names it)
        if (g.circular) {  // rings: the main line's ring, inner ones above it, outer ones below
          group.R = Math.max(30, g.span / (2 * Math.PI)) - low * LANE;
          group.outer = group.R + high * LANE + LINE;
          group.width = 2 * group.outer;
          group.height = group.head + 2 * group.outer;
        } else {  // room above for twisted links' arcs, below for links looping back
          // twisted links between far-apart nodes (an inverted repeat's copy) arc over the group
          group.above = g.links.some((l) => l[4] === "twisted" && !nearTwist(g.nodes, l)) ? 14 : 0;
          group.width = g.span;
          // room beside the first / last end for a contig folding back on itself (a loop)
          const loops = (side) => g.links.some((l) => l[4] === "twisted" && l[0] === l[2] && l[1] === side && l[3] === side);
          group.padL = loops("start") ? 30 : 0;
          group.padR = loops("end") ? 30 : 0;
          group.height = group.head + group.above + (high - low) * LANE + LINE + (g.links.some((l) => l[4] === "back") ? 14 : 2);
        }
        for (const p of places) {
          const node = { ...p, group };
          group.nodes.set(p.id, node);
          this.nodes.push(node);
        }
        group.links = g.links.map(([a, sa, b, sb, kind]) => ({ a: group.nodes.get(a), sa, b: group.nodes.get(b), sb, kind }));
        if (!g.circular) {
          // Route against actual node rectangles, not the group's bounding box.
          // Reuse clear space; separate only horizontal runs that overlap.
          const placedRoutes = [];
          const yOf = (n) => (n.lane - low) * LANE;
          const endX = (n, side) => side === "end" ? n.x1 : n.x0;
          const returns = group.links.filter((l) => l.kind === "back").sort((a, b) =>
            Math.abs(endX(a.a, a.sa) - endX(a.b, a.sb)) - Math.abs(endX(b.a, b.sa) - endX(b.b, b.sb)));
          for (const l of returns) {
            const ax = endX(l.a, l.sa), bx = endX(l.b, l.sb), ay = yOf(l.a), by = yOf(l.b);
            const lo = Math.min(ax, bx), hi = Math.max(ax, bx);
            const obstacles = [...group.nodes.values()].filter((n) => n !== l.a && n !== l.b);
            const candidates = new Set([ay - 9, ay + 9, by - 9, by + 9]);
            for (const n of group.nodes.values()) { candidates.add(yOf(n) - 6); candidates.add(yOf(n) + 6); candidates.add(yOf(n) + LANE / 2); candidates.add(yOf(n) - LANE / 2); }
            for (const route of placedRoutes) { candidates.add(route.y - 8); candidates.add(route.y + 8); }
            const inside = (x, y) => obstacles.some((n) => x > n.x0 - 3 && x < n.x1 + 3 && Math.abs(y - yOf(n)) < LINE / 2 + 1.5);
            const clear = (y) => {
              if (obstacles.some((n) => n.x0 - 3 < hi && n.x1 + 3 > lo && Math.abs(y - yOf(n)) < LINE / 2 + 1.5)) return false;
              if (placedRoutes.some((r) => lo < r.hi + 8 && hi + 8 > r.lo && Math.abs(y - r.y) < 7)) return false;
              // Sample the short bends at both ends, including their sideways reach.
              for (const [x, yy, dir] of [[ax, ay, 1], [bx, by, -1]]) {
                const steps = Math.max(12, Math.ceil(Math.abs(y - yy) / 2));
                for (let i = 1; i < steps; i++) {
                  const t = i / steps, u = 1 - t;
                  if (inside(x + 30 * dir * u * t, yy * (u*u*u + 3*u*u*t) + y * (3*u*t*t + t*t*t))) return false;
                }
              }
              return true;
            };
            const score = (y) => Math.abs(y - ay) + Math.abs(y - by);
            const options = [...candidates].sort((a, b) => score(a) - score(b) || b - a);
            const clearRun = (y) => !obstacles.some((n) => n.x0 - 3 < hi && n.x1 + 3 > lo && Math.abs(y - yOf(n)) < LINE / 2 + 1.5)
              && !placedRoutes.some((r) => lo < r.hi + 8 && hi + 8 > r.lo && Math.abs(y - r.y) < 7);
            let fallback = options.find(clearRun);
            if (fallback === undefined) {
              const top = Math.min(...options), bottom = Math.max(...options);
              fallback = score(top - 8) < score(bottom + 8) ? top - 8 : bottom + 8;
            }
            l.routeY = options.find(clear) ?? fallback;
            placedRoutes.push({ lo, hi, y: l.routeY });
          }
          const top = Math.min(0, ...placedRoutes.map((r) => r.y - 6));
          group.above = Math.max(group.above, -top);
          const bottom = Math.max((high - low) * LANE + LINE, ...placedRoutes.map((r) => r.y + 6));
          group.height = group.head + group.above + bottom + 2;
        }
        this.groups.push(group);
      }
      this.arrange();
      this.selected = this.hovered = this.anchor = null;
      this.chosen = new Set();
      this.path = [];
      this.hoverEnd = null;
      this._links = null;
      this.byId = new Map(this.nodes.map((n) => [n.id, n]));
      this.scale = this.minScale();
      this.ox = this.oy = 0;
      this.draw();
    }

    // Where each group goes (again when the Compact option changes)
    arrange() {
      // rows about 16:10 for what is there (at most as wide as the view, at least the widest group), in the server's order; the
      // nodes linked to nothing last, in one block packed tight (each as its own
      // group they made rows of dashes)
      // (a small graph gets about the proportions of the view, 16:10, not a strip as wide as the window)
      const area = this.groups.reduce((n, g) => n + (g.width + 2 * BETWEEN) * (g.height + BETWEEN), 0);
      const rowWidth = Math.max(...this.groups.map((g) => g.width + (g.padL || 0) + (g.padR || 0)),
        Math.min((this.width || 1200) - 2 * MARGIN, Math.sqrt(area * 1.6)));
      const lone = (g) => g.nodes.size === 1 && !g.circular;
      let x = MARGIN, y = MARGIN, rowHeight = 0, widest = 0;
      const place = (g, gap) => {
        const padL = g.padL || 0, padR = g.padR || 0;
        if (x > MARGIN && x + padL + g.width + padR > MARGIN + rowWidth) { x = MARGIN; y += rowHeight + gap; rowHeight = 0; }
        g.left = x + padL;
        g.top = y;
        if (g.circular) { g.cx = x + g.outer; g.cy = y + g.head + g.outer; }
        x += padL + g.width + padR + 2 * gap;
        rowHeight = Math.max(rowHeight, g.height);
        widest = Math.max(widest, g.left + g.width + padR);
      };
      const compact = this.options.compact && this.groups.some((g) => g.circular);
      if (compact) {  // the rings first, side by side with their centres level (rows of them when they don't fit), the rest under them
        const rows = [];
        let row = [], used = 0;
        for (const g of this.groups.filter((g) => g.circular)) {
          const w = g.width + 2 * BETWEEN;
          if (row.length && used + w > rowWidth) { rows.push(row); row = []; used = 0; }
          row.push(g);
          used += w;
        }
        rows.push(row);
        for (const r of rows) {
          const above = Math.max(...r.map((g) => g.head + g.outer)), below = Math.max(...r.map((g) => g.outer));
          let rx = MARGIN;
          for (const g of r) {
            g.left = rx;
            g.cx = rx + g.outer;
            g.cy = y + above;
            g.top = g.cy - g.head - g.outer;
            rx += g.width + 2 * BETWEEN;
            widest = Math.max(widest, g.left + g.width);
          }
          y += above + below + BETWEEN;
        }
        for (const g of this.groups.filter((g) => !g.circular && !lone(g))) place(g, BETWEEN);
      } else {
        for (const g of this.groups.filter((g) => !lone(g))) place(g, BETWEEN);
      }
      const loners = this.groups.filter(lone);
      this.loneBlock = null;
      if (loners.length) {
        x = MARGIN;
        y += rowHeight + BETWEEN;
        rowHeight = 0;
        this.loneBlock = { top: y, count: loners.length };
        y += HEAD;
        for (const g of loners) place(g, 5);
      }
      this.width_ = widest + MARGIN;
      this.height_ = y + rowHeight + MARGIN;
      for (const g of this.groups.filter((g) => g.circular)) {
        for (const l of g.links) if (l.a !== l.b) l.curves = this.circularRoute(g, l);
      }
    }

    // A point on a node: at x along the drawing (its own px), on its line
    point(n, x) {
      const g = n.group;
      if (!g.circular) return [g.left + x, g.top + g.head + g.above + (n.lane - g.low) * LANE];
      const a = (2 * Math.PI * x) / g.span - Math.PI / 2, r = g.R + n.lane * LANE;
      return [g.cx + r * Math.cos(a), g.cy + r * Math.sin(a)];
    }
    end(n, side) { return this.point(n, side === "end" ? n.x1 : n.x0); }

    // ── View ───────────────────────────────────────────────────────────────
    resize() {
      const dpr = window.devicePixelRatio || 1;
      this.width = this.container.clientWidth;
      this.height = this.container.clientHeight;
      this.canvas.width = Math.round(this.width * dpr);
      this.canvas.height = Math.round(this.height * dpr);
      this.canvas.style.width = `${this.width}px`;
      this.canvas.style.height = `${this.height}px`;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (this.width_) { this.scale = Math.max(this.scale, this.minScale()); this.clamp(); }
      this.draw();
    }

    fit() {
      this.scale = this.minScale();
      this.ox = this.oy = 0;
      this.draw();
    }

    zoom(factor, x = this.width / 2, y = (this.height - STATUS) / 2) {
      const scale = Math.min(Math.max(MOST, 3 * this.minScale()), Math.max(this.minScale(), this.scale * factor));
      this.ox = x - (x - this.ox) * scale / this.scale;
      this.oy = y - (y - this.oy) * scale / this.scale;
      this.scale = scale;
      this.clamp();
      this.draw();
    }

    fitSelection() {
      const chosen = this.pathMode ? new Set(this.path.map(step => step.node)) : this.chosen;
      if (!chosen.size) return;
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const n of chosen) {
        // Include quadrant extrema on circular tracks, not just the endpoints.
        const positions = [n.x0, n.x1];
        if (n.group.circular) {
          const step = n.group.span / 4;
          for (let x = Math.ceil(n.x0 / step) * step; x < n.x1; x += step) positions.push(x);
        }
        for (const x of positions) {
          const [px, py] = this.point(n, x);
          x0 = Math.min(x0, px); x1 = Math.max(x1, px);
          y0 = Math.min(y0, py); y1 = Math.max(y1, py);
        }
      }
      this.scale = Math.max(this.minScale(), Math.min(Math.max(MOST, 3 * this.minScale()),
        (this.width - 60) / Math.max(1, x1 - x0 + LINE), (this.height - STATUS - 60) / Math.max(1, y1 - y0 + LINE)));
      this.ox = this.width / 2 - (x0 + x1) / 2 * this.scale;
      this.oy = (this.height - STATUS) / 2 - (y0 + y1) / 2 * this.scale;
      this.clamp();
      this.draw();
    }

    // Zoom out far enough to fit the whole graph.
    minScale() {
      if (!this.width_) return 1;
      return Math.max(0.001, Math.min(FILL, this.width / this.width_, (this.height - STATUS) / this.height_));  // (a small graph is magnified to fill the view)
    }

    // the drawing fills the view: no moving it off into empty space (one that fits stays at the top left)
    clamp() {
      const w = this.width_ * this.scale, h = this.height_ * this.scale, vh = this.height - STATUS;
      this.ox = w <= this.width ? 0 : Math.min(0, Math.max(this.width - w, this.ox));
      this.oy = h <= vh ? 0 : Math.min(0, Math.max(vh - h, this.oy));
    }

    // A node in the middle of the view, selected
    reveal(n) {
      const [x, y] = this.point(n, (n.x0 + n.x1) / 2);
      this.ox = this.width / 2 - x * this.scale;
      this.oy = (this.height - STATUS) / 2 - y * this.scale;
      this.clamp();
      this.choose([n]);
    }

    // The selection: these nodes (last: the one shown in the status line)
    choose(list, last = null, anchor = true) {
      this.chosen = new Set(list);
      this.selected = last || list[list.length - 1] || null;
      if (anchor) this.anchor = this.selected;
      this.draw();
      this.options.onChange?.(this.chosen);
    }

    // The selected nodes, in the order drawn (group by group, left to right)
    selectedNodes() {
      return this.nodes.filter((n) => this.chosen.has(n)).sort((a, b) =>
        this.groups.indexOf(a.group) - this.groups.indexOf(b.group) || a.x0 - b.x0 || a.lane - b.lane);
    }

    // Find a shortest walk through oriented node ends, independent of layout.
    // Entering one end means leaving the other; both initial directions are legal.
    stretch(to) {
      const from = this.anchor;
      if (!from || from.group !== to.group) return [to];
      if (from === to) return [from];
      const adjacent = new Map([...to.group.nodes.values()].map((n) => [n, []]));
      for (const l of to.group.links) {
        if (!l.a || !l.b) continue;
        adjacent.get(l.a).push({ side: l.sa, node: l.b, entry: l.sb });
        adjacent.get(l.b).push({ side: l.sb, node: l.a, entry: l.sa });
      }
      for (const list of adjacent.values()) list.sort((a,b) =>
        Number(this.chosen.has(b.node))-Number(this.chosen.has(a.node)) ||
        Math.abs(a.node.lane) - Math.abs(b.node.lane) ||  // equally short: along the main line, not the alphabet's first
        a.node.id.localeCompare(b.node.id) || a.entry.localeCompare(b.entry));
      const queue = [{node:from,exit:"end",parent:null},{node:from,exit:"start",parent:null}];
      const visited = new Map([[from,new Set(["start","end"])]]);
      for(let i=0;i<queue.length;i++) {
        const state=queue[i];
        for(const edge of adjacent.get(state.node)) {
          if(edge.side!==state.exit) continue;
          const exit=edge.entry==="start"?"end":"start";
          const next={node:edge.node,exit,parent:state};
          if(edge.node===to){
            const path=[];
            for(let p=next;p;p=p.parent) path.push(p.node);
            return path.reverse();
          }
          if(!visited.has(edge.node)) visited.set(edge.node,new Set());
          if(visited.get(edge.node).has(exit)) continue;
          visited.get(edge.node).add(exit);
          queue.push(next);
        }
      }
      return []; // keep the existing selection when no valid walk exists
    }

    // ── Drawing ────────────────────────────────────────────────────────────
    draw() {
      const ctx = this.ctx;
      if (!ctx || !this.width) return;
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, this.width, this.height);
      ctx.save();
      ctx.translate(this.ox, this.oy);
      ctx.scale(this.scale, this.scale);
      const y0 = -this.oy / this.scale, y1 = y0 + this.height / this.scale;
      const x0 = -this.ox / this.scale, x1 = x0 + this.width / this.scale;
      const shown = this.groups.filter((g) => g.top + g.height >= y0 && g.top <= y1 && g.left + g.width + (g.padR || 0) >= x0 && g.left - (g.padL || 0) <= x1);
      this.preparePath();
      for (const g of shown) {  // only what is in view
        if (this.options.links !== false) this.drawLinks(g);
        for (const n of g.nodes.values()) {
          const dim = this.pathDim(n);
          if (dim) ctx.globalAlpha = 0.35;
          this.drawNode(n);
          if (dim) ctx.globalAlpha = 1;
        }
      }
      if (this.pathMode) this.drawPathHalves();
      ctx.restore();
      this.drawLabels(shown);
      if (this.pathMode) this.drawPathBadges();
      this.drawLegend();
      this.drawBars();
      this.drawStatus();
    }

    circularRoute(g, l) {
      const a = this.end(l.a, l.sa), b = this.end(l.b, l.sb);
      const tangent = (n, side) => {
        const angle = 2 * Math.PI * (side === "end" ? n.x1 : n.x0) / g.span - Math.PI / 2;
        const sign = side === "end" ? 1 : -1;
        return [-Math.sin(angle) * sign, Math.cos(angle) * sign];
      };
      const u = tangent(l.a, l.sa), v = tangent(l.b, l.sb);
      const aa = Math.atan2(a[1] - g.cy, a[0] - g.cx), ba = Math.atan2(b[1] - g.cy, b[0] - g.cx);
      const delta = Math.atan2(Math.sin(ba - aa), Math.cos(ba - aa));
      const direction = Math.sign(delta);
      if (Math.abs(delta) < Math.PI * 0.75 && direction === (l.sa === "end" ? 1 : -1) &&
          direction === (l.sb === "start" ? 1 : -1)) {
        // Arc-shaped joins follow the ring rather than cutting across it.
        // Radius-specific handles also blend between neighbouring lanes.
        const factor = 4 / 3 * Math.tan(Math.abs(delta) / 4);
        const ha = Math.hypot(a[0] - g.cx, a[1] - g.cy) * factor;
        const hb = Math.hypot(b[0] - g.cx, b[1] - g.cy) * factor;
        return [[a[0]+u[0]*ha, a[1]+u[1]*ha, b[0]+v[0]*hb, b[1]+v[1]*hb, ...b]];
      }
      // Keep the middle direct, with short tangential bends at the endpoints.
      // Small overlaps are acceptable; avoid collision-avoidance detours.
      const reach = Math.min(10, Math.hypot(b[0] - a[0], b[1] - a[1]) * 0.12);
      return [[a[0]+u[0]*reach, a[1]+u[1]*reach,
               b[0]+v[0]*reach, b[1]+v[1]*reach, ...b]];
    }

    // Links: a short hook from one node's side to the other's; one looping back
    // (not on a ring) a dashed arc under the group; a twisted one (end to end or
    // start to start: one of the two read the other way, as an inverted repeat's
    // second copy) a dashed hairpin over it
    // How far a link may swing sideways out of a node's end: 10 px, less where another node on
    // the same line sits close beside that end
    room(g, node, side) {
      return Math.max(1.5, Math.min(10, this.gapBeside(g, node, side) / 2));
    }

    // The free distance (px) from a node's end to the next node on the same line, if any
    gapBeside(g, node, side) {
      const dir = side === "end" ? 1 : -1, at = dir > 0 ? node.x1 : node.x0;
      let gap = Infinity;
      for (const m of g.nodes.values()) {
        if (m === node || m.lane !== node.lane) continue;
        const d = dir > 0 ? m.x0 - at : at - m.x1;
        if (d >= -0.01 && d < gap) gap = d;
      }
      return gap * this.scale;
    }

    drawLinks(g) {
      const ctx = this.ctx;
      ctx.strokeStyle = HOOK;
      ctx.lineWidth = 1.3;
      for (const l of g.links) {
        if (!l.a || !l.b) continue;
        const [ax, ay] = this.end(l.a, l.sa), [bx, by] = this.end(l.b, l.sb);
        const focus = this.hovered || (this.chosen.size === 1 ? this.selected : null);
        const incident = focus && (l.a === focus || l.b === focus);
        const selectedPath = this.chosen.has(l.a) && this.chosen.has(l.b);
        const onPath = this._pathLinks?.has([`${l.a.id}|${l.sa}`, `${l.b.id}|${l.sb}`].sort().join("~"));
        const highlighted = selectedPath || incident || onPath;
        ctx.strokeStyle = highlighted ? SELECTED : focus?.group === g ? "#d6d9de" : HOOK;
        ctx.lineWidth = onPath ? 2.6 : highlighted ? 1.7 : 1.3;
        ctx.setLineDash(l.kind === "forward" ? [] : [3, 2]);
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        if (g.circular && l.a === l.b && l.sa !== l.sb &&
            (g.nodes.size === 1 || (g.main?.length === 1 && g.main[0] === l.a.id))) {
          // Follow the missing circumference, rather than drawing a chord.
          const r = g.R + l.a.lane * LANE;
          ctx.beginPath();
          ctx.arc(g.cx, g.cy, r, (2 * Math.PI * l.a.x1) / g.span - Math.PI / 2,
                  (2 * Math.PI * l.a.x0) / g.span + 3 * Math.PI / 2);
        } else if (g.circular && l.a === l.b && l.sa !== l.sb) {
          // A branch self-link returns locally beside the segment, not through
          // the unused circumference of the entire component.
          const n = l.a, r = g.R + n.lane * LANE;
          const angle = (at) => 2 * Math.PI * at / g.span - Math.PI / 2;
          const aa = angle(l.sa === "end" ? n.x1 : n.x0), ba = angle(l.sb === "end" ? n.x1 : n.x0);
          const mid = angle((n.x0 + n.x1) / 2), side = n.lane < 0 ? -1 : 1;
          const rr = Math.max(5, r + side * 14);
          const mx = g.cx + rr * Math.cos(mid), my = g.cy + rr * Math.sin(mid);
          const reach = Math.min(12, Math.max(4, Math.hypot(bx - ax, by - ay) / 4));
          const ad = l.sa === "end" ? 1 : -1, bd = l.sb === "end" ? 1 : -1;
          const direction = ba > aa ? 1 : -1;
          const tx = -Math.sin(mid) * direction * reach, ty = Math.cos(mid) * direction * reach;
          ctx.bezierCurveTo(ax - Math.sin(aa) * ad * reach, ay + Math.cos(aa) * ad * reach,
                            mx - tx, my - ty, mx, my);
          ctx.bezierCurveTo(mx + tx, my + ty,
                            bx - Math.sin(ba) * bd * reach, by + Math.cos(ba) * bd * reach, bx, by);
        } else if (g.circular) {
          for (const curve of l.curves || this.circularRoute(g, l)) ctx.bezierCurveTo(...curve);
        } else if (l.kind === "twisted" && nearTwist(null, l, l.a, l.b)) {
          // two ends close together on neighbouring lines: a bracket beside them, wider
          // the more lines it spans (so nested ones don't overlap)
          const dir = l.sa === "end" ? 1 : -1, edge = dir > 0 ? Math.max(ax, bx) : Math.min(ax, bx);
          const out = edge + dir * (9 + 3 * Math.abs(by - ay) / LANE);
          ctx.bezierCurveTo(out, ay, out, by, bx, by);
        } else if (l.kind === "twisted" && l.a === l.b) {
          // a contig whose end folds back on itself (a palindrome): a narrow teardrop loop beside that end
          const out = l.sa === "end" ? 1 : -1, tip = ax + out * 26;  // (upright at the far tip, so it is round there)
          ctx.bezierCurveTo(ax + out * 14, ay - 13, tip, ay - 9, tip, ay);
          ctx.bezierCurveTo(tip, ay + 9, ax + out * 14, ay + 13, ax, ay);
        } else if (l.kind === "twisted") {
          const top = g.top + HEAD + 2, out = l.sa === "end" ? 1 : -1, back = l.sb === "end" ? 1 : -1;
          // out of each end sideways, as far as the room beside it allows (a node may sit right against it)
          const ra = this.room(g, l.a, l.sa), rb = this.room(g, l.b, l.sb);
          // an end whose partner lies on the side the link swings to rises in a wide S, flattening
          // onto the top line a little along; otherwise (and for a contig folding back on itself)
          // the line goes round: a loop
          const towardB = l.a !== l.b && out === (bx >= ax ? 1 : -1), towardA = l.a !== l.b && back === (ax >= bx ? 1 : -1);
          const wide = Math.min(Math.abs(bx - ax) / 2, Math.max(45, 1.7 * Math.abs(ay - top)));  // as wide as the climb is tall,
          const near = (node, side) => this.gapBeside(g, node, side) < 60;  // a node right beside the end: climb steeply
          const riseA = near(l.a, l.sa) ? Math.min(wide, 14) : wide, riseB = near(l.b, l.sb) ? Math.min(wide, 14) : wide;
          if (towardB) ctx.bezierCurveTo(ax + out * riseA * 0.6, ay, ax + out * riseA * 0.4, top, ax + out * riseA, top);
          else { ctx.bezierCurveTo(ax + out * ra, ay, ax + out * ra, top, ax, top); }
          if (towardA) {
            ctx.lineTo(bx + back * riseB, top);
            ctx.bezierCurveTo(bx + back * riseB * 0.4, top, bx + back * riseB * 0.6, by, bx, by);
          } else {
            ctx.lineTo(bx, top);
            ctx.bezierCurveTo(bx + back * rb, top, bx + back * rb, by, bx, by);
          }
        } else if (l.kind === "back") {
          const bottom = g.top + g.head + g.above + l.routeY;
          const reach = 10;
          ctx.bezierCurveTo(ax + reach, ay, ax + reach, bottom, ax, bottom);
          ctx.lineTo(bx, bottom);
          ctx.bezierCurveTo(bx - reach, bottom, bx - reach, by, bx, by);
        } else {
          const out = l.sa === "end" ? 1 : -1, into = l.sb === "start" ? -1 : 1, reach = Math.max(2, Math.abs(bx - ax) * 0.12);
          ctx.bezierCurveTo(ax + out * reach, ay, bx + into * reach, by, bx, by);
        }
        ctx.stroke();
      }
      ctx.setLineDash([]);
    }

    drawNode(n) {
      const ctx = this.ctx, g = n.group;
      // Like sequence annotations: one filled band with a short integrated tip.
      const r = g.circular ? g.R + n.lane * LANE : 0;
      const length = g.circular ? (n.x1 - n.x0) * 2 * Math.PI * r / g.span : n.x1 - n.x0;
      const head = Math.min(LINE * 0.6, length * 0.6, 8);
      const trace = (shift = 0) => {
        ctx.beginPath();
        if (g.circular) {
          ctx.arc(g.cx, g.cy, r + shift, 2 * Math.PI * n.x0 / g.span - Math.PI / 2,
                  2 * Math.PI * n.x1 / g.span - Math.PI / 2);
        } else {
          const [ax, y] = this.point(n, n.x0), [bx] = this.point(n, n.x1);
          ctx.moveTo(ax, y + shift);
          ctx.lineTo(bx, y + shift);
        }
      };
      ctx.beginPath();
      if (g.circular) {
        const a0 = 2 * Math.PI * n.x0 / g.span - Math.PI / 2;
        const a1 = 2 * Math.PI * n.x1 / g.span - Math.PI / 2;
        const angle = head / r;
        const b0 = n.o === "-" ? a0 + angle : a0, b1 = n.o === "-" ? a1 : a1 - angle;
        const tip = (a) => ctx.lineTo(g.cx + r * Math.cos(a), g.cy + r * Math.sin(a));
        ctx.arc(g.cx, g.cy, r + LINE / 2, b0, b1);
        if (n.o !== "-") tip(a1);
        ctx.arc(g.cx, g.cy, r - LINE / 2, b1, b0, true);
        if (n.o === "-") tip(a0);
      } else {
        const [x0, y] = this.point(n, n.x0), [x1] = this.point(n, n.x1);
        const top = y - LINE / 2, bottom = y + LINE / 2;
        if (n.o === "-") { ctx.moveTo(x0, y); ctx.lineTo(x0 + head, top); }
        else ctx.moveTo(x0, top);
        if (n.o !== "-") { ctx.lineTo(x1 - head, top); ctx.lineTo(x1, y); ctx.lineTo(x1 - head, bottom); }
        else { ctx.lineTo(x1, top); ctx.lineTo(x1, bottom); }
        ctx.lineTo(n.o === "-" ? x0 + head : x0, bottom);
      }
      ctx.closePath();
      ctx.fillStyle = this.options.colours === false ? "#687280" : n.color;
      ctx.fill();
      if (this.chosen.has(n) || this.hovered === n) {
        if (this.chosen.has(n)) {
          ctx.fillStyle = "rgba(216, 27, 96, 0.14)";
          ctx.fill();
        }
        ctx.strokeStyle = this.chosen.has(n) ? SELECTED : "#4a4f55";
        ctx.lineWidth = Math.min(2, (this.chosen.has(n) ? 1.4 : 0.8) / this.scale);
        ctx.stroke();
      }
      if (n.hits.length && this.data.reference) this.drawHits(n);
    }

    // The reference's matches on a node: a strip beside it over the part that matches, its colour
    // running along the reference (red start .. purple end), backwards where the match is reversed
    drawHits(n) {
      const ctx = this.ctx, g = n.group, L = n.length, STEPS = 16, shift = -(LINE / 2 + 2);
      const along = (p) => (n.o === "-" ? n.x1 - (p / L) * (n.x1 - n.x0) : n.x0 + (p / L) * (n.x1 - n.x0));
      const angle = (x) => (2 * Math.PI * x) / g.span - Math.PI / 2;
      ctx.lineWidth = 2.5;
      ctx.lineCap = "butt";
      for (const [q0, q1, , reversed, p0, p1] of [...n.hits].sort((a, b) => (b[5] - b[4]) - (a[5] - a[4])).slice(0, 8)) {
        for (let k = 0; k < STEPS; k++) {
          const t0 = k / STEPS, t1 = (k + 1) / STEPS, mid = (t0 + t1) / 2;
          const q = reversed ? q1 - mid * (q1 - q0) : q0 + mid * (q1 - q0);
          const xa = along(p0 + (p1 - p0) * t0), xb = along(p0 + (p1 - p0) * t1);
          ctx.strokeStyle = `hsl(${Math.round((280 * q) / this.data.reference.length)}, 85%, 50%)`;
          ctx.beginPath();
          if (g.circular) {
            const r = g.R + n.lane * LANE + shift;
            ctx.arc(g.cx, g.cy, r, angle(xa), angle(xb), xb < xa);
          } else {
            const [ax, y] = this.point(n, xa), [bx] = this.point(n, xb);
            ctx.moveTo(ax, y + shift);
            ctx.lineTo(bx, y + shift);
          }
          ctx.stroke();
        }
      }
    }

    // Labels at a fixed size on screen, whatever the zoom: each group's name, then
    // node names where the node is long enough on screen for its name (main lines
    // first, the longest first), none over another
    drawLabels(groups) {
      this.labelBoxes = [];
      if (this.options.labels === "hidden") return;
      const ctx = this.ctx, taken = [];
      this.labelBoxes = [];
      // Screen-space geometry index: labels must clear the actual strokes,
      // including neighbouring groups, not just other text rectangles.
      const cells = new Map(), cellSize = 32;
      const add = (x, y, w, h) => {
        if (x+w < 0 || y+h < 0 || x > this.width || y > this.height) return;
        const r = [x,y,w,h];
        for (let cx=Math.floor(x/cellSize);cx<=Math.floor((x+w)/cellSize);cx++)
          for (let cy=Math.floor(y/cellSize);cy<=Math.floor((y+h)/cellSize);cy++) {
            const key = `${cx},${cy}`;
            if (!cells.has(key)) cells.set(key, []);
            cells.get(key).push(r);
          }
      };
      const segment = (a,b,pad=2) => {
        const ax=a[0]*this.scale+this.ox, ay=a[1]*this.scale+this.oy;
        const bx=b[0]*this.scale+this.ox, by=b[1]*this.scale+this.oy;
        add(Math.min(ax,bx)-pad,Math.min(ay,by)-pad,Math.abs(bx-ax)+2*pad,Math.abs(by-ay)+2*pad);
      };
      let current=null;
      const line = (x,y,pad=2) => { if(current) segment(current,[x,y],pad); current=[x,y]; };
      const recorder = {
        beginPath(){ current=null; }, moveTo(x,y){ current=[x,y]; }, lineTo: line,
        bezierCurveTo: (a,b,c,d,x,y) => {
          const p=current; if(!p) return;
          const steps=Math.max(4,Math.ceil((Math.hypot(a-p[0],b-p[1])+Math.hypot(c-a,d-b)+Math.hypot(x-c,y-d))*this.scale/3));
          for(let i=1;i<=steps;i++){ const t=i/steps,u=1-t;line(u*u*u*p[0]+3*u*u*t*a+3*u*t*t*c+t*t*t*x,u*u*u*p[1]+3*u*u*t*b+3*u*t*t*d+t*t*t*y); }
        },
        quadraticCurveTo: (a,b,x,y) => {
          const p=current; if(!p) return;
          const steps=Math.max(4,Math.ceil((Math.hypot(a-p[0],b-p[1])+Math.hypot(x-a,y-b))*this.scale/3));
          for(let i=1;i<=steps;i++){ const t=i/steps,u=1-t;line(u*u*p[0]+2*u*t*a+t*t*x,u*u*p[1]+2*u*t*b+t*t*y); }
        },
        arc: (x,y,r,a,b) => {
          const steps=Math.max(4,Math.ceil(Math.abs(b-a)*r*this.scale/3));
          current=[x+r*Math.cos(a),y+r*Math.sin(a)];
          for(let i=1;i<=steps;i++){const t=a+(b-a)*i/steps;line(x+r*Math.cos(t),y+r*Math.sin(t));}
        }, stroke(){}, setLineDash(){}
      };
      for(const g of groups){
        for(const n of g.nodes.values()){
          const steps=g.circular?Math.max(2,Math.ceil((n.x1-n.x0)/g.span*2*Math.PI*(g.R+n.lane*LANE)*this.scale/3)):1;
          let previous=this.point(n,n.x0);
          for(let i=1;i<=steps;i++){const p=this.point(n,n.x0+(n.x1-n.x0)*i/steps);segment(previous,p,(LINE/2+ (n.hits.length?3:0))*this.scale+2);previous=p;}
        }
        if(this.options.links!==false){
          this.ctx=recorder;
          try{this.drawLinks(g);}finally{this.ctx=ctx;}
        }
      }
      if ((this.options.colours!==false && this.data?.depth_range) || this.data?.reference)
        add(this.width-136,6,128,(this.options.colours!==false && this.data?.depth_range?30:0)+(this.data?.reference?30:0)+8);
      const overlaps = (x,y,w,h,r) => x<r[0]+r[2] && x+w>r[0] && y<r[1]+r[3] && y+h>r[1];
      const free = (x, y, w, h) => {
        if (x < 0 || y < 0 || x + w > this.width || y + h > this.height - STATUS) return false;
        if (taken.some((r) => overlaps(x,y,w,h,r))) return false;
        for(let cx=Math.floor(x/cellSize);cx<=Math.floor((x+w)/cellSize);cx++)
          for(let cy=Math.floor(y/cellSize);cy<=Math.floor((y+h)/cellSize);cy++)
            if(cells.get(`${cx},${cy}`)?.some((r)=>overlaps(x,y,w,h,r))) return false;
        taken.push([x, y, w, h]);
        return true;
      };
      const sx = (x) => x * this.scale + this.ox, sy = (y) => y * this.scale + this.oy;
      ctx.textBaseline = "alphabetic";
      ctx.textAlign = "left";
      for (const g of groups) {  // group names (several nodes), with their description when there is room
        if (g.nodes.size < 2 || g.head * this.scale < 13) continue;  // no room above it at this zoom: the status line names it
        const x = sx(g.left), y = sy(g.top) + 13;
        ctx.font = `600 12px ${FONT}`;
        const w = ctx.measureText(g.name).width;
        ctx.font = `11px ${FONT}`;
        const d = ctx.measureText(g.description).width;
        const room = Math.max(g.width * this.scale, 60);
        if (w > room || !free(x, y - 12, Math.min(room, w + 8 + d), 15)) continue;
        ctx.font = `600 12px ${FONT}`;
        ctx.fillStyle = "#333";
        ctx.fillText(g.name, x, y);
        if (w + 8 + d <= room) {
          ctx.font = `11px ${FONT}`;
          ctx.fillStyle = MUTED;
          ctx.fillText(g.description, x + w + 8, y);
        }
      }
      if (this.loneBlock && HEAD * this.scale >= 13) {
        const y = sy(this.loneBlock.top) + 13;
        const text = `${this.loneBlock.count.toLocaleString()} nodes linked to nothing`;
        ctx.font = `11px ${FONT}`;
        if (free(sx(MARGIN), y - 12, 76 + ctx.measureText(text).width, 15)) {
          ctx.font = `600 12px ${FONT}`;
          ctx.fillStyle = "#333";
          ctx.fillText("Not linked", sx(MARGIN), y);
          ctx.font = `11px ${FONT}`;
          ctx.fillStyle = MUTED;
          ctx.fillText(`${this.loneBlock.count.toLocaleString()} nodes linked to nothing`, sx(MARGIN) + 76, y);

        }
      }
      ctx.font = LABEL;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      const nodes = groups.flatMap((g) => [...g.nodes.values()]).sort((a, b) =>
        (this.chosen.has(b) || b === this.hovered) - (this.chosen.has(a) || a === this.hovered) ||
        (a.lane !== 0) - (b.lane !== 0) || b.length - a.length);
      for (const n of nodes) {
        ctx.font = this.chosen.has(n) ? `600 10.5px ${FONT}` : LABEL;
        if (this.options.labels === "selected" && !this.chosen.has(n) && n !== this.hovered) continue;
        const g = n.group, wId = ctx.measureText(n.id).width;
        const [px,py]=this.point(n,(n.x0+n.x1)/2), ax=sx(px), ay=sy(py);
        const wanted = this.chosen.has(n) || n === this.hovered;
        const length = (n.x1-n.x0)*this.scale*(g.circular?2*Math.PI*(g.R+n.lane*LANE)/g.span:1);
        if(!wanted && length<wId+6) continue;
        // zoomed in enough that the node's length and depth fit along it: its name, then those
        const detail = `${n.id} · ${n.length.toLocaleString()} bp` + (n.depth > 0 ? ` · ${n.depth.toFixed(1)}×` : "");
        const wDetail = ctx.measureText(detail).width;
        const place = (text, w) => {
          const candidates=[];
          if(g.circular){
            const angle=2*Math.PI*(n.x0+n.x1)/2/g.span-Math.PI/2;
            const nx=Math.cos(angle),ny=Math.sin(angle), preferred=n.lane<0?-1:1;
            const distance=Math.abs(nx)*(w/2+3)+Math.abs(ny)*8+LINE/2*this.scale+4;
            for(const extra of (wanted?[0,8,16]:[0,8])) for(const sign of [preferred,-preferred])
              candidates.push([ax+nx*sign*(distance+extra),ay+ny*sign*(distance+extra)+6]);
          }else{
            const distance=LINE/2*this.scale+5;
            for(const extra of (wanted?[0,8,16]:[0,8])) {
              candidates.push([ax,ay-distance-extra],[ax,ay+distance+12+extra]);
            }
          }
          const position=candidates.find(([x,y])=>free(x-w/2-3,y-12,w+6,14));
          if(!position) return false;
          const [x,y]=position;
          this.labelBoxes.push({id:n.id,rect:[x-w/2-3,y-12,w+6,14]});
          ctx.fillStyle = this.chosen.has(n) ? SELECTED : wanted ? "#111" : "#444";
          ctx.fillText(text, x, y);
          return true;
        };
        if (!(length >= wDetail + 10 && place(detail, wDetail))) place(n.id, wId);
      }
    }

    // The depth colours (and a reference's), in the top right corner, always shown
    drawLegend() {
      const range = this.options.colours === false ? null : this.data?.depth_range, ref = this.data?.reference;
      if (!range && !ref) return;
      const ctx = this.ctx, w = 110, x = this.width - w - 18;
      let y = 10;
      ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
      ctx.fillRect(x - 8, y - 4, w + 16, (range ? 30 : 0) + (ref ? 30 : 0) + 4);
      const bar = (label, lo, hi, colour) => {
        for (let i = 0; i < w; i++) {
          ctx.fillStyle = colour(i / (w - 1));
          ctx.fillRect(x + i, y + 14, 1, 7);
        }
        ctx.font = `10px ${FONT}`;
        ctx.fillStyle = MUTED;
        ctx.textBaseline = "alphabetic";
        ctx.textAlign = "left";
        ctx.fillText(lo, x, y + 10);
        ctx.textAlign = "right";
        ctx.fillText(hi, x + w, y + 10);
        ctx.textAlign = "center";
        ctx.fillText(label, x + w / 2, y + 10);
        y += 30;
      };
      if (range) bar("depth", `${range[0].toFixed(1)}×`, `${range[1].toFixed(1)}×`,
                     (t) => {
                       const palette = this.data.depth_palette;
                       if (!palette?.length) return "#9aa1a9";
                       const position = t * (palette.length - 1), i = Math.min(Math.floor(position), palette.length - 2), f = position - i;
                       const a = palette[i], b = palette[i+1];
                       return "#" + [1,3,5].map((k) => Math.round(parseInt(a.slice(k,k+2),16)*(1-f)+parseInt(b.slice(k,k+2),16)*f).toString(16).padStart(2,"0")).join("");
                     });
      if (ref) bar(ref.name.length > 14 ? `${ref.name.slice(0, 13)}…` : ref.name, "start", "end",
                   (t) => `hsl(${Math.round(280 * t)}, 85%, 50%)`);
    }

    drawBars() {
      const ctx = this.ctx, vw = this.width, vh = this.height - STATUS;
      const w = this.width_ * this.scale, h = this.height_ * this.scale;
      ctx.fillStyle = "rgba(0, 0, 0, 0.18)";
      if (h > vh + 1) {
        const len = Math.max(24, (vh * vh) / h);
        ctx.fillRect(vw - BAR - 2, (-this.oy / (h - vh)) * (vh - len) || 0, BAR, len);
      }
      if (w > vw + 1) {
        const len = Math.max(24, (vw * vw) / w);
        ctx.fillRect((-this.ox / (w - vw)) * (vw - len) || 0, vh - BAR - 2, len, BAR);
      }
    }

    drawStatus() {
      const ctx = this.ctx, y = this.height - STATUS;
      ctx.fillStyle = "#f4f5f7";
      ctx.fillRect(0, y, this.width, STATUS);
      ctx.font = `11px ${FONT}`;
      ctx.fillStyle = MUTED;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      const n = this.hovered || (this.chosen.size === 1 ? this.selected : null);
      const total = this.chosen.size > 1 ? [...this.chosen].reduce((s, m) => s + m.length, 0) : 0;
      ctx.fillText(this.pathMode ? this.pathStatus() : n ? this.nodeText(n) : this.chosen.size > 1 ? `${this.chosen.size} nodes selected · ${kb(total)} in all` : HINT, 8, y + STATUS / 2);
    }

    nodeText(n) {
      const links = n.group.links.filter((l) => l.a === n || l.b === n).length;
      const ref = this.data.reference, h = n.hits[0];
      return [`Node ${n.id}${n.o}`, kb(n.length), n.depth ? `depth ${n.depth.toFixed(1)}×` : "", n.group.name,
              n.lane === 0 ? "main line" : "", `${links} link${links === 1 ? "" : "s"}`,
              h && ref ? `matches ${ref.name} ${h[0].toLocaleString()}–${h[1].toLocaleString()} on bases ${(h[4] + 1).toLocaleString()}–${h[5].toLocaleString()}${h[3] ? " (reversed)" : ""}, ${h[2]} %` +
                         (n.hits.length > 1 ? ` (+${n.hits.length - 1})` : "") : ""].filter(Boolean).join(" · ");
    }

    // ── Mouse ──────────────────────────────────────────────────────────────
    nodeAt(e) {
      const r = this.canvas.getBoundingClientRect();
      const x = (e.clientX - r.left - this.ox) / this.scale, y = (e.clientY - r.top - this.oy) / this.scale;
      const reach = LINE / 2 + 1.5 / this.scale;
      for (const g of this.groups) {
        if (y < g.top || y > g.top + g.height || x < g.left - 5 || x > g.left + g.width + 5) continue;
        for (const n of g.nodes.values()) {
          if (g.circular) {
            const d = Math.hypot(x - g.cx, y - g.cy), rr = g.R + n.lane * LANE;
            let a = Math.atan2(y - g.cy, x - g.cx) + Math.PI / 2;
            if (a < 0) a += 2 * Math.PI;
            const at = (a / (2 * Math.PI)) * g.span;
            if (Math.abs(d - rr) <= reach && at >= n.x0 && at <= n.x1) return n;
          } else {
            const [ax, ny] = this.point(n, n.x0), [bx] = this.point(n, n.x1);
            if (x >= ax && x <= bx && Math.abs(y - ny) <= reach) return n;
          }
        }
      }
      return null;
    }

    onWheel(e) {
      e.preventDefault();
      if (e.ctrlKey) {
        const r = this.canvas.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
        return this.zoom(Math.exp(-e.deltaY * 0.0015), mx, my);
      } else if (e.shiftKey) {
        this.ox -= e.deltaY || e.deltaX;
      } else {
        this.ox -= e.deltaX;
        this.oy -= e.deltaY;
      }
      this.clamp();
      this.draw();
    }

    onMouseDown(e) {
      if (e.button !== 0) return;
      this.canvas.focus();
      const node = this.nodeAt(e);
      const x0 = e.clientX, y0 = e.clientY;
      let moved = false;
      const move = (ev) => {
        if (!moved && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 4) return;
        moved = true;
        this.ox += ev.movementX;
        this.oy += ev.movementY;
        this.clamp();
        this.draw();
      };
      const up = () => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
        if (moved) return;
        if (this.pathMode) { if (node) this.pathClick(node, e); return; }
        const add = e.ctrlKey || e.metaKey;
        if (!node) {
          if (!add && !e.shiftKey) this.choose([]);
        } else if (e.shiftKey) {
          const path = this.stretch(node);
          if (path.length) this.choose(add ? [...this.chosen, ...path] : path, node, !!(!this.anchor || this.anchor.group !== node.group));
        } else if (add) {
          const rest = [...this.chosen].filter((m) => m !== node);
          this.choose(this.chosen.has(node) ? rest : [...rest, node], node);
        } else {
          this.choose([node]);
        }
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    }

    onHover(e) {
      const node = this.nodeAt(e);
      if (this.pathMode) {
        const end = node ? this.entryAt(e, node) : null;
        if (node === this.hovered && end === (this.hoverEnd?.end ?? null)) return;
        this.hovered = node;
        this.hoverEnd = end ? { node, end } : null;
        this.canvas.style.cursor = end ? "pointer" : "default";
        this.draw();
        return;
      }
      if (node === this.hovered) return;
      this.hovered = node;
      this.canvas.style.cursor = node ? "pointer" : "default";
      this.draw();
    }
  }

  // ── Path picking ───────────────────────────────────────────────────────
  // Steps through the graph, a node as often as you like: each click enters a node at one of
  // its two ends (the half you point at) and leaves by the other. Only ends joined to where
  // the last step left are offered, unless gaps are allowed. dir "right" = entered at the
  // node's left end (as drawn), "left" = at its right end.
  Object.assign(GraphView.prototype, {
    setPathMode(on) {
      this.pathMode = on;
      this.hoverEnd = null;
      if (on) this.choose([]);
      this.draw();
      this.options.onPath?.(this);
    },

    // The ends of nodes that are joined: "id|end" -> [{node, end}]
    linkIndex() {
      if (this._links) return this._links;
      const map = new Map();
      const add = (a, sa, b, sb) => {
        const key = `${a.id}|${sa}`;
        if (!map.has(key)) map.set(key, []);
        map.get(key).push({ node: b, end: sb });
      };
      for (const g of this.groups) for (const l of g.links) {
        add(l.a, l.sa, l.b, l.sb);
        if (l.a !== l.b || l.sa !== l.sb) add(l.b, l.sb, l.a, l.sa);
      }
      return (this._links = map);
    },

    exitEnd: (step) => (step.dir === "right" ? "end" : "start"),
    entryEnd: (step) => (step.dir === "right" ? "start" : "end"),

    // Where the next step may enter: Set of "id|end" joined to the last step's exit, or null (nothing picked yet)
    candidates() {
      if (!this.path.length) return null;
      const last = this.path[this.path.length - 1];
      return new Set((this.linkIndex().get(`${last.node.id}|${this.exitEnd(last)}`) || []).map((c) => `${c.node.id}|${c.end}`));
    },

    preparePath() {
      this._pathLinks = new Set();
      this._cand = this.pathMode ? this.candidates() : null;
      this._candNodes = new Set([...(this._cand || [])].map((k) => k.split("|")[0]));
      this._inPath = new Set(this.path.map((s) => s.node.id));
      this.path.forEach((s, i) => {
        const t = this.path[i + 1];
        if (t) this._pathLinks.add([`${s.node.id}|${this.exitEnd(s)}`, `${t.node.id}|${this.entryEnd(t)}`].sort().join("~"));
      });
    },

    pathDim(n) {
      return this.pathMode && this.path.length && !this.allowGaps && !this._candNodes.has(n.id) && !this._inPath.has(n.id);
    },

    // The end of node n a click at e would enter at (null: that node can't be entered there)
    entryAt(e, n) {
      const cand = this.candidates();
      const ends = ["start", "end"].filter((side) => !cand || this.allowGaps || cand.has(`${n.id}|${side}`));
      if (!ends.length) return null;
      if (ends.length === 1) return ends[0];
      const r = this.canvas.getBoundingClientRect(), g = n.group;
      const x = (e.clientX - r.left - this.ox) / this.scale, y = (e.clientY - r.top - this.oy) / this.scale;
      let t;
      if (g.circular) {
        let a = Math.atan2(y - g.cy, x - g.cx) + Math.PI / 2;
        if (a < 0) a += 2 * Math.PI;
        t = ((a / (2 * Math.PI)) * g.span - n.x0) / (n.x1 - n.x0);
      } else {
        const [ax] = this.point(n, n.x0), [bx] = this.point(n, n.x1);
        t = (x - ax) / (bx - ax);
      }
      return t < 0.5 ? "start" : "end";
    },

    pathClick(n, e) {
      const end = this.entryAt(e, n);
      if (!end) return;
      const cand = this.candidates();
      this.path.push({ node: n, dir: end === "start" ? "right" : "left", gap: !!cand && !cand.has(`${n.id}|${end}`) });
      this.hoverEnd = null;
      this.draw();
      this.options.onPath?.(this);
    },

    pathUndo() { this.path.pop(); this.draw(); this.options.onPath?.(this); },
    pathClear() { this.path = []; this.draw(); this.options.onPath?.(this); },
    pathReverseFirst() {
      if (this.path.length !== 1) return;
      this.path[0].dir = this.path[0].dir === "right" ? "left" : "right";
      this.draw();
      this.options.onPath?.(this);
    },
    pathRemove(i) { this.path.splice(i); this.draw(); this.options.onPath?.(this); },

    // The steps as the server reads them: "id+" / "id-" in the file's orientation
    pathOrients() {
      return this.path.map((s) => `${s.node.id}${(s.dir === "right") === (s.node.o === "+") ? "+" : "-"}`);
    },

    // Does the last step lead back into the first (a circle)?
    pathCloses() {
      if (!this.path.length) return false;
      const first = this.path[0], last = this.path[this.path.length - 1];
      return (this.linkIndex().get(`${last.node.id}|${this.exitEnd(last)}`) || [])
        .some((c) => c.node === first.node && c.end === this.entryEnd(first));
    },

    pathStatus() {
      const h = this.hoverEnd;
      if (h) {
        const side = h.node.group.circular ? (h.end === "start" ? "start" : "end") : (h.end === "start" ? "left" : "right");
        return `${this.path.length ? "Continue into" : "Start at"} ${h.node.id} by its ${side} end · ${kb(h.node.length)}`;
      }
      return this.path.length ? "Click the highlighted ends to go on · Backspace: undo the last step · Esc: start again"
                              : "Path: click a node's end to start there, then the ends that follow it";
    },

    // The two halves of a node as bands: the ends that can be entered, the one under the pointer stronger
    drawPathHalves() {
      const ctx = this.ctx;
      const half = (n, side, colour) => {
        const g = n.group, mid = (n.x0 + n.x1) / 2, a = side === "start" ? n.x0 : mid, b = side === "start" ? mid : n.x1;
        ctx.beginPath();
        if (g.circular) {
          const r = g.R + n.lane * LANE, ang = (x) => (2 * Math.PI * x) / g.span - Math.PI / 2;
          ctx.arc(g.cx, g.cy, r, ang(a), ang(b));
        } else {
          const [ax, y] = this.point(n, a), [bx] = this.point(n, b);
          ctx.moveTo(ax, y);
          ctx.lineTo(bx, y);
        }
        ctx.strokeStyle = colour;
        ctx.lineWidth = LINE + 5;
        ctx.lineCap = "butt";
        ctx.stroke();
      };
      for (const key of this._cand || []) {
        const [id, side] = key.split("|"), n = this.byId.get(id);
        if (n) half(n, side, "rgba(216, 27, 96, 0.30)");
      }
      if (this.hoverEnd) half(this.hoverEnd.node, this.hoverEnd.end, "rgba(216, 27, 96, 0.65)");
    },

    // Numbered badges with an arrow in the direction each step went, over the nodes (screen size)
    drawPathBadges() {
      const ctx = this.ctx, S = this.scale;
      const place = (n, t, up) => {  // screen position at fraction t along the node, beside it; and its direction along the node
        const g = n.group, x = n.x0 + (n.x1 - n.x0) * t;
        if (g.circular) {
          const a = (2 * Math.PI * x) / g.span - Math.PI / 2, r = g.R + n.lane * LANE + up / S;
          return { x: (g.cx + r * Math.cos(a)) * S + this.ox, y: (g.cy + r * Math.sin(a)) * S + this.oy, ang: a + Math.PI / 2 };
        }
        const [px, py] = this.point(n, x);
        return { x: px * S + this.ox, y: py * S + this.oy - up, ang: 0 };
      };
      const arrow = (x, y, ang, size, colour) => {
        ctx.fillStyle = colour;
        ctx.beginPath();
        ctx.moveTo(x + Math.cos(ang) * size, y + Math.sin(ang) * size);
        ctx.lineTo(x - Math.cos(ang) * size * 0.6 + Math.sin(ang) * size * 0.75, y - Math.sin(ang) * size * 0.6 - Math.cos(ang) * size * 0.75);
        ctx.lineTo(x - Math.cos(ang) * size * 0.6 - Math.sin(ang) * size * 0.75, y - Math.sin(ang) * size * 0.6 + Math.cos(ang) * size * 0.75);
        ctx.closePath();
        ctx.fill();
      };
      const visits = new Map();
      this.path.forEach((s, i) => visits.set(s.node, [...(visits.get(s.node) || []), i]));
      ctx.save();
      ctx.font = "bold 10px 'Segoe UI', system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const [n, list] of visits) list.forEach((i, k) => {
        const step = this.path[i], p = place(n, (k + 1) / (list.length + 1), 0);
        const ang = p.ang + (step.dir === "right" ? 0 : Math.PI);
        const colour = step.gap ? "#c62828" : SELECTED;
        ctx.fillStyle = colour;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 8, 0, 2 * Math.PI);
        ctx.fill();
        ctx.fillStyle = "#fff";
        ctx.fillText(String(i + 1), p.x, p.y + 0.5);
        arrow(p.x + Math.cos(ang) * 15, p.y + Math.sin(ang) * 15, ang, 5, colour);
      });
      const h = this.hoverEnd;  // where a click would go in, and which way
      if (h) {
        const p = place(h.node, h.end === "start" ? 0.2 : 0.8, 0), ang = p.ang + (h.end === "start" ? 0 : Math.PI);
        arrow(p.x, p.y, ang, 7, SELECTED);
      }
      ctx.restore();
    },
  });

  // Find: nodes whose names contain the text (an exact name first); step 0 goes to
  // the first, +1/-1 to the next/previous. Returns {count, at}
  GraphView.prototype.find = function (text, step) {
    if (!text) { this.matches = []; return { count: 0, at: -1 }; }
    if (step === 0 || !this.matches || this.matchText !== text) {
      const all = this.nodes.filter((n) => n.id.includes(text));
      this.matches = [...all.filter((n) => n.id === text), ...all.filter((n) => n.id !== text)];
      this.matchText = text;
      this.matchAt = 0;
    } else if (this.matches.length) {
      this.matchAt = (this.matchAt + step + this.matches.length) % this.matches.length;
    }
    if (this.matches.length) this.reveal(this.matches[this.matchAt]);
    return { count: this.matches.length, at: this.matchAt };
  };

  window.GraphView = GraphView;
})();
