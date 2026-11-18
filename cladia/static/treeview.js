// Cladia tree viewer: a rectangular phylogenetic tree drawn on a canvas.
// Zooming spreads the tree out while names keep their size, as in FigTree or
// Geneious; only what is on screen is drawn, so trees of thousands of tips stay
// fast.
//
//   const view = new TreeView(container, { onNodeMenu(e, node), onChange() });
//   view.setTree(nodes);          // from /api/tree: [{ name, length, support, parent }], parents first
//   view.setShow({ align, support, lengths, ladderize });
//   view.find("text"); view.findStep(+1);
//
// Mouse: Ctrl+wheel spreads the tips apart (or closer), Ctrl+Shift+wheel stretches
// the branches; the wheel scrolls up and down, Shift+wheel sideways; dragging
// moves the tree. Click a branch or name to select that clade (or tip), double-
// click to collapse or expand it, right-click for its menu (onNodeMenu).
// Ctrl/Cmd+click toggles tips/clades; Shift+click selects a range in displayed
// row order; Ctrl/Cmd+C copies selected tip names.

(function () {
  // ── Look ───────────────────────────────────────────────────────────────
  const FONT = "'Segoe UI', system-ui, sans-serif";
  const BRANCH = "#4a4f55", SELECTED = "#2f6f5e", MATCH = "#e67e22", MATCH_BG = "rgba(230, 126, 34, 0.22)"  // as the sequence viewer's Find;
  const MUTED = "#687280", GUIDE = "#c4c8ce", COLLAPSED = "#dfe3e8";
  const MARGIN = 16, TOP = 14, SCALE = 30, STATUS = 20, SCROLLBAR = 10;
  const LABELS_FROM = 9;    // px per tip from which tip names are drawn
  const START = 16;         // px per tip a big tree opens with (names readable; it scrolls)
  const MOST = 100;         // px per tip, most zoomed in (by hand)
  const FIT_MOST = 40;      // px per tip at most when fitting (opening a small tree, Fit buttons)
  const ASPECT = 1.5;       // fitting rows: branches at most this many times as wide as the rows are tall
  const HINT = "Ctrl+wheel: spread the tips · Ctrl+Shift+wheel: stretch the branches · wheel: up/down · " +
               "Shift+wheel: sideways · drag to move · Ctrl+click: toggle · Shift+click: range · double-click: collapse · right-click: menu";

  // A length as text: 3 significant digits, no trailing zeros
  const short = (x) => String(Number(x.toPrecision(3)));

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

  class TreeView {
    constructor(container, options = {}) {
      this.container = container;
      this.options = options;
      this.canvas = document.createElement("canvas");
      this.canvas.style.display = "block";
      this.canvas.tabIndex = 0;
      container.append(this.canvas);
      this.ctx = this.canvas.getContext("2d");
      this.nodes = null;
      this.show = { align: false, support: true, lengths: false, ladderize: false };
      this.spacing = START;  // px between tips
      this.xScale = 1;       // px per unit of branch length
      this.scrollX = this.scrollY = 0;
      this.selected = -1;    // last selected node; chosen contains the full selection
      this.selectionAnchor = -1;
      this.chosen = new Set();
      this.selectedCounts = null;
      this.hovered = -1;
      this.matches = [];     // nodes drawn as tips whose names match the find text
      this.matchSet = new Set();
      this.matchIndex = -1;
      this.status = "";
      this.canvas.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
      this.canvas.addEventListener("mousedown", (e) => this.onMouseDown(e));
      this.canvas.addEventListener("dblclick", (e) => this.onDoubleClick(e));
      this.canvas.addEventListener("mousemove", (e) => this.onHover(e));
      this.canvas.addEventListener("mouseleave", () => { this.hovered = -1; this.status = ""; this.draw(); });
      this.canvas.addEventListener("contextmenu", (e) => this.onContextMenu(e));
      this.canvas.addEventListener("keydown", (e) => {
        if (e.key === "Escape") this.select(-1);
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c" && this.selected >= 0) {
          e.preventDefault(); this.options.onCopy?.(this.selectedTipNames());
        }
      });
      this.resize();
      new ResizeObserver(() => this.resize()).observe(container);
    }

    // ── Data ───────────────────────────────────────────────────────────────
    setTree(list) {
      // Without branch lengths (a cladogram), every branch counts 1
      const lengths = list.some((n, i) => i > 0 && n.length);
      this.nodes = list.map((n, i) => ({ ...n, index: i, kids: [], length: i === 0 ? 0 : lengths ? n.length || 0 : 1 }));
      for (const n of this.nodes) if (n.parent >= 0) this.nodes[n.parent].kids.push(n.index);
      this.cladogram = !lengths;
      // depth (distance from the root) and tip counts; parents come before children
      for (const n of this.nodes) n.depth = n.parent < 0 ? 0 : this.nodes[n.parent].depth + n.length;
      for (let i = this.nodes.length - 1; i >= 0; i--) {
        const n = this.nodes[i];
        n.tips = n.kids.length ? n.kids.reduce((s, k) => s + this.nodes[k].tips, 0) : 1;
        n.deepest = n.kids.length ? Math.max(...n.kids.map((k) => this.nodes[k].deepest)) : n.depth;
      }
      this.tipCount = this.nodes[0].tips;
      this.collapsed = new Set();
      this.selected = this.hovered = -1;
      this.status = "";
      this.chosen = new Set();
      this.selectionAnchor = -1;
      this.selectedCounts = new Uint32Array(this.nodes.length);
      this.matches = [];
      this.matchSet = new Set();
      this.matchIndex = -1;
      this.measureNames();
      this.layout();
      this.fit();
      this.options.onChange?.();
    }

    setShow(show) {
      Object.assign(this.show, show);
      this.layout();
      this.clampScroll();
      this.draw();
    }

    measureNames() {
      this.ctx.font = `12px ${FONT}`;
      for (const n of this.nodes) {
        n.nameWidth = n.name ? this.ctx.measureText(n.name).width : 0;
        n.summaryWidth = n.kids.length ? this.ctx.measureText(`${n.tips} tips`).width : 0;
      }
      this.ctx.font = `bold 12px ${FONT}`;
      for (const n of this.nodes) {
        n.boldNameWidth = n.name ? this.ctx.measureText(n.name).width : 0;
        n.boldSummaryWidth = n.kids.length ? this.ctx.measureText(`${n.tips} tips`).width : 0;
      }
      this.ctx.font = `10px ${FONT}`;
      this.supportWidth = Math.max(0, ...this.nodes.map((n) => (n.support ? this.ctx.measureText(n.support).width : 0)));
    }

    // Rows (y) of the tips in view, in order (ladderized: smaller clades first,
    // the largest at the bottom); an inner node halfway between its first and last
    // child; a collapsed clade is one row
    layout() {
      if (!this.nodes) return;
      const kids = (n) => (this.show.ladderize ? [...n.kids].sort((a, b) => this.nodes[a].tips - this.nodes[b].tips) : n.kids);
      this.drawn = [];  // nodes in view, parents first
      this.rows = [];   // the node on each row
      const stack = [0];
      while (stack.length) {
        const n = this.nodes[stack.pop()];
        this.drawn.push(n);
        n.open = n.kids.length > 0 && !this.collapsed.has(n.index);
        n.order = n.open ? kids(n) : [];
        if (n.open) for (let i = n.order.length - 1; i >= 0; i--) stack.push(n.order[i]);
        else { n.row = this.rows.length; this.rows.push(n); }
      }
      for (let i = this.drawn.length - 1; i >= 0; i--) {
        const n = this.drawn[i];
        if (n.open) n.row = (this.nodes[n.order[0]].row + this.nodes[n.order[n.order.length - 1]].row) / 2;
      }
      this.maxDepth = Math.max(1e-9, ...this.rows.map((n) => n.deepest));
      this.labelWidth = Math.max(0, ...this.rows.map((n) => this.label(n).width));
      if (this.matches.length) this.find(this.query, false);
    }

    // What a row says after its branch: a tip's name, a collapsed clade's size
    label(n) {
      if (!n.kids.length) return { text: n.name || "", width: n.nameWidth };
      const text = `${n.tips} tips`;
      return { text, width: n.summaryWidth };
    }

    // ── Geometry ───────────────────────────────────────────────────────────
    get areaWidth() { return this.width - SCROLLBAR; }
    get areaHeight() { return this.height - SCALE - STATUS - SCROLLBAR; }
    labelRoom() { return Math.min(this.labelWidth + 10, this.areaWidth * 0.45); }
    fitXScale() { return Math.max(1e-9, (this.areaWidth - 2 * MARGIN - this.labelRoom()) / this.maxDepth); }
    fitSpacing() { return (this.areaHeight - 2 * TOP) / Math.max(1, this.rows.length); }
    minSpacing() { return Math.min(this.fitSpacing(), START); }
    labelSize() { return Math.max(9, Math.min(13, this.spacing * 0.75)); }
    labelX(n) {
      const depth = this.show.align ? this.maxDepth : n.kids.length ? n.deepest : n.depth;
      return MARGIN + depth * this.xScale + (this.show.align ? 6 : 5);
    }
    labelPixels(n) {
      const width = this.matchSet.has(n) ? (n.kids.length ? n.boldSummaryWidth : n.boldNameWidth) : this.label(n).width;
      return width * this.labelSize() / 12;
    }
    labelRight(n) { return this.labelX(n) + this.labelPixels(n); }
    contentWidth() {
      const cached = this.measuredContent;
      if (cached && cached.rows === this.rows && cached.scale === this.xScale && cached.spacing === this.spacing &&
          cached.align === this.show.align && cached.matches === this.matchSet) return cached.width;
      let right = MARGIN + this.maxDepth * this.xScale;
      // Reserve the labels below their visibility threshold too, so crossing it
      // cannot suddenly change the horizontal scrolling bounds.
      for (const n of this.rows) right = Math.max(right, this.labelRight(n));
      this.measuredContent = { rows: this.rows, scale: this.xScale, spacing: this.spacing,
                               align: this.show.align, matches: this.matchSet, width: right + MARGIN };
      return this.measuredContent.width;
    }
    contentHeight() { return 2 * TOP + this.rows.length * this.spacing; }
    xOf(n) { return MARGIN + n.depth * this.xScale - this.scrollX; }
    yOf(n) { return TOP + (n.row + 0.5) * this.spacing - this.scrollY; }
    alignX() { return MARGIN + this.maxDepth * this.xScale - this.scrollX + 6; }

    // The whole tree in the panel, or as many tips as stay readable
    fit() {
      if (!this.nodes) return;
      this.spacing = Math.min(28, Math.max(this.fitSpacing(), START));
      this.xScale = this.fitXScale();
      this.scrollX = this.scrollY = 0;
      this.draw();
    }

    // Fit the displayed rows, retaining collapsed clades and display choices.
    fitWhole() { this.fitRows(this.rows, 0); }

    viewportState() {
      return { spacing: this.spacing, scale: this.xScale / this.fitXScale(),
        x: this.scrollX / this.xScale, y: this.scrollY / this.spacing,
        collapsed: [...this.collapsed] };
    }

    restoreViewport(state) {
      this.collapsed = new Set(state.collapsed);
      this.layout();
      this.spacing = state.spacing;
      this.xScale = state.scale * this.fitXScale();
      this.scrollX = state.x * this.xScale;
      this.scrollY = state.y * this.spacing;
      this.clampScroll();
      this.draw();
    }

    fitSelection() {
      const selection = this.selectionBounds();
      if (selection) this.fitRows(selection.rows, selection.leftDepth);
    }

    fitRows(rows, leftDepth) {
      if (!rows?.length) return;
      const first = rows[0].row, last = rows[rows.length - 1].row;
      this.spacing = Math.min(FIT_MOST, Math.max(.001, (this.areaHeight - 2 * TOP) / (last - first + 1)));
      const room = Math.max(1, this.areaWidth - 2 * MARGIN);
      let scale = this.fitXScale() * 200;
      for (const n of rows) {
        const depth = n.kids.length ? n.deepest : n.depth;
        if (depth > leftDepth) scale = Math.min(scale, room / (depth - leftDepth));
        if (this.spacing >= LABELS_FROM) {
          const labelDepth = this.show.align ? this.maxDepth : depth;
          const remaining = room - (this.show.align ? 6 : 5) - this.labelPixels(n);
          if (labelDepth > leftDepth) scale = Math.min(scale, remaining / (labelDepth - leftDepth));
        }
      }
      // Short branches stretched over the whole width look smeared: keep the fitted
      // branches in proportion to the rows' height (never narrower than the whole tree's fit).
      let span = 0;
      for (const n of rows) span = Math.max(span, (n.kids.length ? n.deepest : n.depth) - leftDepth);
      if (span > 0) {
        const tall = Math.min(this.areaHeight - 2 * TOP, (last - first + 1) * this.spacing);
        scale = Math.min(scale, Math.max(this.fitXScale(), tall * ASPECT / span));
      }
      this.xScale = Math.max(1e-9, scale);
      let right = MARGIN + leftDepth * this.xScale;
      for (const n of rows) {
        right = Math.max(right, MARGIN + (n.kids.length ? n.deepest : n.depth) * this.xScale);
        if (this.spacing >= LABELS_FROM) right = Math.max(right, this.labelRight(n));
      }
      this.scrollX = (MARGIN + leftDepth * this.xScale + right) / 2 - this.areaWidth / 2;
      this.scrollY = TOP + (first + last + 1) / 2 * this.spacing - this.areaHeight / 2;
      this.clampScroll();
      this.draw();
    }

    resize() {
      // A hidden viewer has no usable dimensions. Keep its viewport intact.
      if (!this.container.clientWidth || !this.container.clientHeight) return;
      const dpr = window.devicePixelRatio || 1;
      const was = this.width && this.nodes ? this.fitXScale() : null;
      this.width = this.container.clientWidth;
      this.height = this.container.clientHeight;
      this.canvas.width = Math.round(this.width * dpr);
      this.canvas.height = Math.round(this.height * dpr);
      this.canvas.style.width = `${this.width}px`;
      this.canvas.style.height = `${this.height}px`;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (this.nodes && was) {  // the branches keep their stretch relative to the panel
        this.xScale *= this.fitXScale() / was;
        this.spacing = Math.max(this.spacing, this.minSpacing());
      }
      this.clampScroll();
      this.draw();
    }

    clampScroll() {
      if (!this.nodes) return;
      this.scrollX = Math.max(0, Math.min(this.scrollX, this.contentWidth() - this.areaWidth));
      this.scrollY = Math.max(0, Math.min(this.scrollY, this.contentHeight() - this.areaHeight));
    }

    // What zooming centres on: the selected tips/clade, else the current Find match, if
    // it is drawn and in view: row midpoint and branch-depth bounds, plus the
    // displayed rows whose fixed-size labels complete its visual bounds; null: zoom
    // where the mouse (or the leftmost branch point) is
    focusNode() {
      // A tip still counts when its branch endpoint is offscreen but its name shows.
      const inView = (n) => n && this.xOf(n) <= this.areaWidth &&
        (n.open || this.spacing < LABELS_FROM ? this.xOf(n) : this.labelRight(n) - this.scrollX) >= 0 &&
        this.yOf(n) >= 0 && this.yOf(n) <= this.areaHeight;
      const selection = this.selectionBounds();
      if (selection && this.drawn.some(n => this.inSelection(n) && inView(n))) return selection;
      const n = this.matches[this.matchIndex];
      return inView(n) ? {row: n.row, leftDepth: n.depth, rightDepth: n.deepest || n.depth, rows: [n]} : null;
    }

    selectionBounds() {
      if (!this.chosen?.size) return null;
      const rows = this.rows.filter(n => this.inSelection(n));
      if (!rows.length) return null;
      let leftDepth = Infinity, rightDepth = 0;
      for (const n of this.drawn) if (this.inSelection(n)) leftDepth = Math.min(leftDepth, n.depth);
      for (const n of rows) rightDepth = Math.max(rightDepth, n.depth, n.deepest || 0);
      return {row: (rows[0].row + rows[rows.length - 1].row) / 2, leftDepth, rightDepth, rows};
    }

    // Zoom keeping the content under (x, y) in place, or the focus node in the middle
    zoomTips(factor, y = this.areaHeight / 2) {
      const focus = this.focusNode();
      if (focus) y = this.areaHeight / 2;
      const at = focus ? focus.row + 0.5 : (y + this.scrollY - TOP) / this.spacing;
      this.spacing = Math.max(this.minSpacing(), Math.min(MOST, this.spacing * factor));
      this.scrollY = at * this.spacing + TOP - y;
      this.clampScroll();
      this.draw();
    }

    zoomBranches(factor, x = this.areaWidth / 2) {
      const fit = this.fitXScale();
      const focus = this.focusNode();
      if (focus) x = this.areaWidth / 2;
      const at = (x + this.scrollX - MARGIN) / this.xScale;
      this.xScale = Math.max(Math.min(fit, this.xScale), Math.min(fit * 200, this.xScale * factor));
      if (focus) {
        const left = MARGIN + focus.leftDepth * this.xScale;
        let right = MARGIN + focus.rightDepth * this.xScale;
        if (this.spacing >= LABELS_FROM) for (const n of focus.rows) right = Math.max(right, this.labelRight(n));
        this.scrollX = (left + right) / 2 - x;
      } else this.scrollX = at * this.xScale + MARGIN - x;
      this.clampScroll();
      this.draw();
    }

    // Where stretching the branches is anchored: the leftmost branch point in view
    // (the root when it shows), so what is crowded to its right opens up and the
    // empty stretch before it doesn't grow; the view's left edge if none shows
    firstNodeX() {
      let first = Infinity;
      for (const n of this.drawn) {
        const x = this.xOf(n), y = this.yOf(n);
        if (n.kids.length && x >= 0 && x <= this.areaWidth && y >= 0 && y <= this.areaHeight) first = Math.min(first, x);
      }
      return Number.isFinite(first) ? first : 0;
    }

    // A node's row in the middle of the view, unless it is already in view
    reveal(n) {
      const y = this.yOf(n);
      if (y < this.spacing || y > this.areaHeight - this.spacing) {
        this.scrollY = TOP + (n.row + 0.5) * this.spacing - this.areaHeight / 2;
      }
      const x = MARGIN + n.depth * this.xScale;
      const right = this.spacing >= LABELS_FROM ? this.labelRight(n) : x;
      if (x - this.scrollX < MARGIN || right - this.scrollX > this.areaWidth - MARGIN) {
        this.scrollX = (x + right) / 2 - this.areaWidth / 2;
      }
      this.clampScroll();
      this.draw();
    }

    // ── Clades ─────────────────────────────────────────────────────────────
    select(index, {toggle = false, range = false} = {}) {
      if (!this.nodes) return;
      if (index < 0 && (toggle || range)) return;
      const rowIndexes = new Map(this.rows.map((n,i) => [n.index,i]));
      let from = -1;
      for (let n = this.nodes[this.selectionAnchor]; n; n = this.nodes[n.parent]) {
        if (rowIndexes.has(n.index)) { from = rowIndexes.get(n.index); break; }
      }
      const to = rowIndexes.get(index) ?? -1;
      const extend = range && from >= 0 && to >= 0;
      if (!toggle) this.chosen = new Set();
      const targets = extend ? this.rows.slice(Math.min(from, to), Math.max(from, to) + 1).map(n => n.index)
        : index >= 0 ? [index] : [];
      const subtree = [], stack = [...targets];
      while (stack.length) {
        const i = stack.pop(); subtree.push(i); stack.push(...this.nodes[i].kids);
      }
      const remove = toggle && !extend && subtree.length && subtree.every(i => this.chosen.has(i));
      for (const i of subtree) remove ? this.chosen.delete(i) : this.chosen.add(i);
      // Keep collapsed partial selections visible, and discard empty selected
      // ancestor branches when the last selected tip beneath them is toggled off.
      this.selectedCounts = new Uint32Array(this.nodes.length);
      for (let i = this.nodes.length - 1; i >= 0; i--) {
        const n = this.nodes[i];
        this.selectedCounts[i] = n.kids.length ? n.kids.reduce((sum, k) => sum + this.selectedCounts[k], 0)
          : Number(this.chosen.has(i));
        if (!this.selectedCounts[i]) this.chosen.delete(i);
      }
      this.selected = this.chosen.has(index) ? index : this.chosen.values().next().value ?? -1;
      if (!extend) this.selectionAnchor = index;
      this.status = this.hovered >= 0 ? this.describe(this.hovered) : "";
      this.draw();
      this.options.onChange?.();
    }

    inSelection(n) {
      return !!this.chosen?.has(n.index) || (!n.open && n.kids.length > 0 && this.selectedCounts?.[n.index] > 0);
    }

    selectedTipNames() {
      const names = [], stack = [0];
      while (stack.length) {
        const n = this.nodes[stack.pop()];
        const kids = this.show.ladderize ? [...n.kids].sort((a,b) => this.nodes[a].tips - this.nodes[b].tips) : n.kids;
        for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
        if (!kids.length && this.chosen.has(n.index)) names.push(n.name);
      }
      return names;
    }

    // Tip names under a node (collapsed ones too)
    tipNames(index) {
      const names = [], stack = [index];
      while (stack.length) {
        const n = this.nodes[stack.pop()];
        if (n.kids.length) for (let i = n.kids.length - 1; i >= 0; i--) stack.push(n.kids[i]);
        else names.push(n.name);
      }
      return names;
    }

    toggleCollapse(index) {
      const n = this.nodes[index];
      if (!n.kids.length || index === 0) return;
      if (this.collapsed.has(index)) this.collapsed.delete(index); else this.collapsed.add(index);
      this.layout();
      this.spacing = Math.max(this.spacing, this.minSpacing());
      this.clampScroll();
      this.draw();
    }

    // ── Find ───────────────────────────────────────────────────────────────
    // Tips whose names contain the text; a collapsed clade counts when a tip in it does
    // The current match stays current while it still matches (no jump back to the first)
    find(query, jump = true) {
      const current = this.matches[this.matchIndex]?.index;
      this.query = query;
      const q = (query || "").trim().toLowerCase();
      this.matches = !q ? [] : this.rows.filter((n) => (n.kids.length
        ? this.tipNames(n.index).some((name) => name && name.toLowerCase().includes(q))
        : n.name && n.name.toLowerCase().includes(q)));
      this.matchSet = new Set(this.matches);
      const kept = this.matches.findIndex((n) => n.index === current);
      this.matchIndex = kept >= 0 ? kept : this.matches.length ? 0 : -1;
      if (jump && this.matches.length) this.reveal(this.matches[this.matchIndex]); else this.draw();
      return this.matches.length;
    }

    findStep(step) {
      if (!this.matches.length) return;
      this.matchIndex = (this.matchIndex + step + this.matches.length) % this.matches.length;
      this.reveal(this.matches[this.matchIndex]);
    }

    // ── Drawing ────────────────────────────────────────────────────────────
    draw() {
      const ctx = this.ctx, w = this.width, h = this.height;
      if (!w || !h) return;
      ctx.fillStyle = "white";
      ctx.fillRect(0, 0, w, h);
      if (!this.nodes) return;
      const area = this.areaHeight, sp = this.spacing;
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, this.areaWidth, area);
      ctx.clip();
      const visible = (y0, y1) => y1 >= -sp && y0 <= area + sp;  // a vertical stretch on screen
      const matched = this.matchSet;
      const current = this.matches[this.matchIndex];
      const thin = Math.max(0.4, Math.min(1.5, sp * 0.5));  // lines thinner as the tips come closer

      // Branches: across to each node, then down its children's span
      for (const n of this.drawn) {
        const x = this.xOf(n), y = this.yOf(n), chosen = this.inSelection(n);
        ctx.strokeStyle = chosen ? SELECTED : BRANCH;
        ctx.lineWidth = chosen || n.index === this.hovered ? thin + 1 : thin;
        if (n.parent >= 0 && visible(y, y)) {
          ctx.beginPath();
          ctx.moveTo(this.xOf(this.nodes[n.parent]), y);
          ctx.lineTo(x, y);
          ctx.stroke();
        }
        if (n.open) {
          const y0 = this.yOf(this.nodes[n.order[0]]), y1 = this.yOf(this.nodes[n.order[n.order.length - 1]]);
          if (visible(y0, y1)) {
            ctx.lineWidth = chosen ? thin + 1 : thin;
            ctx.beginPath();
            ctx.moveTo(x, Math.max(y0, -sp));
            ctx.lineTo(x, Math.min(y1, area + sp));
            ctx.stroke();
          }
        } else if (n.kids.length && visible(y, y)) {  // a collapsed clade: a triangle to its deepest tip
          const half = Math.max(2, sp * 0.4), end = MARGIN + n.deepest * this.xScale - this.scrollX;
          ctx.fillStyle = COLLAPSED;
          ctx.beginPath();
          ctx.moveTo(x, y);
          ctx.lineTo(end, y - half);
          ctx.lineTo(end, y + half);
          ctx.closePath();
          ctx.fill();
          ctx.stroke();
        }
      }

      if (sp < LABELS_FROM) this.drawMatchMarkers();

      // Tip names (or a collapsed clade's size), when there is room for them
      const nameSize = this.labelSize();
      if (sp >= LABELS_FROM) {
        ctx.font = `${nameSize}px ${FONT}`;
        ctx.textBaseline = "middle";
        const first = Math.max(0, Math.floor((this.scrollY - TOP) / sp) - 1);
        const last = Math.min(this.rows.length - 1, Math.ceil((this.scrollY + area - TOP) / sp) + 1);
        for (let r = first; r <= last; r++) {
          const n = this.rows[r], y = this.yOf(n);
          const end = n.kids.length ? MARGIN + n.deepest * this.xScale - this.scrollX : this.xOf(n);
          const x = this.labelX(n) - this.scrollX;
          if (this.show.align && x - end > 8) {  // a dotted guide to the aligned name
            ctx.strokeStyle = GUIDE;
            ctx.lineWidth = 1;
            ctx.setLineDash([2, 3]);
            ctx.beginPath();
            ctx.moveTo(end + 3, y);
            ctx.lineTo(x - 3, y);
            ctx.stroke();
            ctx.setLineDash([]);
          }
          const { text } = this.label(n);
          ctx.font = `${matched.has(n) ? "bold " : ""}${nameSize}px ${FONT}`;  // (before measuring: bold is wider)
          if (matched.has(n)) {
            const tw = ctx.measureText(text).width;
            ctx.fillStyle = MATCH_BG;
            ctx.fillRect(x - 2, y - sp / 2 + 1, tw + 4, sp - 2);
            if (n === current) {
              ctx.strokeStyle = MATCH;
              ctx.lineWidth = 1.5;
              ctx.strokeRect(x - 2, y - sp / 2 + 1, tw + 4, sp - 2);
            }
          }
          ctx.fillStyle = this.inSelection(n) ? SELECTED : n.kids.length ? MUTED : "#1f2328";
          ctx.fillText(text, x, y);
        }
      }

      // Supports at inner nodes (just above the branch, ending at the node) and
      // branch lengths (under the middle of each branch)
      if ((this.show.support || this.show.lengths) && sp >= 10) {
        ctx.font = `10px ${FONT}`;
        ctx.fillStyle = MUTED;
        for (const n of this.drawn) {
          const y = this.yOf(n);
          if (n.parent < 0 || !visible(y, y)) continue;
          const x = this.xOf(n), px = this.xOf(this.nodes[n.parent]);
          if (this.show.support && n.kids.length && n.support) {
            ctx.textAlign = "right";
            ctx.textBaseline = "bottom";
            ctx.fillText(n.support, x - 3, y - 1);
          }
          if (this.show.lengths && !this.cladogram && sp >= 14 && x - px > 20) {
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            ctx.fillText(short(n.length), (x + px) / 2, y + 2);
          }
        }
        ctx.textAlign = "left";
      }
      ctx.restore();
      this.drawScrollbars();
      this.drawScale();
      this.drawStatus();
    }

    // A nice round length for the scale bar, 60-150 px long
    scaleLength() {
      const raw = 100 / this.xScale, p = Math.pow(10, Math.floor(Math.log10(raw)));
      return [1, 2, 5, 10].map((k) => k * p).find((v) => v * this.xScale >= 60) || 10 * p;
    }

    drawScale() {
      const ctx = this.ctx, y = this.areaHeight + SCROLLBAR;
      ctx.fillStyle = "white";
      ctx.fillRect(0, y, this.width, SCALE);
      ctx.strokeStyle = "#d9dce1";
      ctx.beginPath();
      ctx.moveTo(0, y + 0.5);
      ctx.lineTo(this.width, y + 0.5);
      ctx.stroke();
      ctx.font = `11px ${FONT}`;
      ctx.fillStyle = MUTED;
      ctx.textBaseline = "middle";
      if (this.cladogram) {
        ctx.fillText("No branch lengths in this tree (all drawn the same)", MARGIN, y + SCALE / 2);
        return;
      }
      const len = this.scaleLength(), px = len * this.xScale, mid = y + SCALE / 2;
      ctx.strokeStyle = BRANCH;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(MARGIN, mid - 4); ctx.lineTo(MARGIN, mid + 4);
      ctx.moveTo(MARGIN, mid); ctx.lineTo(MARGIN + px, mid);
      ctx.moveTo(MARGIN + px, mid - 4); ctx.lineTo(MARGIN + px, mid + 4);
      ctx.stroke();
      ctx.fillText(`${short(len)} substitutions per site`, MARGIN + px + 8, mid);
    }

    // Fixed-size marks keep Find useful when tip spacing hides the names.
    // Draw the current match last so it remains distinct in a dense group.
    drawMatchMarkers() {
      if (!this.matches.length) return;
      const ctx = this.ctx, current = this.matches[this.matchIndex];
      ctx.save();
      const mark = (n, active) => {
        const y = this.yOf(n);
        const x = n.kids.length ? MARGIN + n.deepest * this.xScale - this.scrollX : this.xOf(n);
        if (y < -5 || y > this.areaHeight + 5 || x < -5 || x > this.areaWidth + 5) return;
        const start = n.kids.length ? this.xOf(n) : n.parent >= 0 ? this.xOf(this.nodes[n.parent]) : x;
        ctx.strokeStyle = MATCH; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(Math.max(start, x - 12), y); ctx.lineTo(x, y); ctx.stroke();
        ctx.beginPath(); ctx.arc(x + 3, y, active ? 4.5 : 2.5, 0, 2 * Math.PI);
        ctx.fillStyle = active ? "white" : MATCH; ctx.fill();
        if (active) ctx.stroke();
      };
      for (const n of this.matches) if (n !== current) mark(n, false);
      if (current) mark(current, true);
      ctx.restore();
    }

    drawStatus() {
      const ctx = this.ctx, y = this.height - STATUS;
      ctx.fillStyle = "#f4f5f7";
      ctx.fillRect(0, y, this.width, STATUS);
      ctx.font = `11px ${FONT}`;
      ctx.fillStyle = MUTED;
      ctx.textBaseline = "middle";
      const zoomedOut = this.spacing < LABELS_FROM ? "Zoom in (Ctrl+wheel) to see the names · " : "";
      const count = this.selectedCounts?.[0] || 0;
      ctx.fillText(this.status || (count ? `${count} tip${count === 1 ? "" : "s"} selected · ` : "") + zoomedOut + HINT, 8, y + STATUS / 2);
    }

    // ── Scrollbars (drawn on the canvas, as in the sequence viewer) ──────────
    bars() {
      const aw = this.areaWidth, ah = this.areaHeight, cw = this.contentWidth(), ch = this.contentHeight();
      const v = ch > ah ? { top: (this.scrollY / ch) * ah, size: Math.max(24, (ah / ch) * ah) } : null;
      const hz = cw > aw ? { left: (this.scrollX / cw) * aw, size: Math.max(24, (aw / cw) * aw) } : null;
      return { v, hz };
    }

    drawScrollbars() {
      const ctx = this.ctx, { v, hz } = this.bars();
      ctx.fillStyle = "#f4f5f7";
      ctx.fillRect(this.areaWidth, 0, SCROLLBAR, this.areaHeight + SCROLLBAR);
      ctx.fillRect(0, this.areaHeight, this.areaWidth, SCROLLBAR);
      ctx.fillStyle = "#b9bec6";
      if (v) ctx.fillRect(this.areaWidth + 2, v.top, SCROLLBAR - 4, v.size);
      if (hz) ctx.fillRect(hz.left, this.areaHeight + 2, hz.size, SCROLLBAR - 4);
    }

    // ── Mouse ──────────────────────────────────────────────────────────────
    point(e) {
      const r = this.canvas.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    }

    // The node at a point: its branch, its name, or its children's vertical line
    nodeAt(x, y) {
      if (!this.nodes || y > this.areaHeight || x > this.areaWidth) return -1;
      const sp = this.spacing, near = Math.max(3, Math.min(7, sp / 2));
      let best = -1, bestD = Infinity;
      for (const n of this.drawn) {
        const nx = this.xOf(n), ny = this.yOf(n), dy = Math.abs(y - ny);
        if (dy > Math.max(near, sp / 2) && !n.open) continue;
        const px = n.parent >= 0 ? this.xOf(this.nodes[n.parent]) : nx - 6;
        let d = Infinity;
        if (dy <= near && x >= px - 2 && x <= nx + 4) d = dy;  // on its branch
        if (!n.open && dy <= sp / 2 && sp >= LABELS_FROM) {    // on its name
          if (x >= nx && x <= this.labelRight(n) - this.scrollX + 4) d = Math.min(d, dy);
        }
        if (n.open) {  // on the line down its children
          const y0 = this.yOf(this.nodes[n.order[0]]), y1 = this.yOf(this.nodes[n.order[n.order.length - 1]]);
          if (Math.abs(x - nx) <= 4 && y >= y0 && y <= y1) d = Math.min(d, Math.abs(x - nx));
        }
        if (d < bestD) { bestD = d; best = n.index; }
      }
      return best;
    }

    describe(index) {
      const n = this.nodes[index];
      const parts = [];
      if (!n.kids.length) parts.push(n.name);
      else parts.push(`Clade of ${n.tips} tips${this.collapsed.has(index) ? " (collapsed)" : ""}`);
      if (this.selectedCounts?.[index]) parts.push(n.kids.length ? `${this.selectedCounts[index]} selected` : "selected");
      if (n.kids.length && n.support) parts.push(`support ${n.support}`);
      if (index > 0 && !this.cladogram) parts.push(`branch ${short(n.length)}`, `from the root ${short(n.depth)}`);
      return parts.join(" · ");
    }

    onHover(e) {
      if (this.panning) return;
      const { x, y } = this.point(e);
      const index = this.nodeAt(x, y);
      if (index !== this.hovered) {
        this.hovered = index;
        this.status = index >= 0 ? this.describe(index) : "";
        this.canvas.style.cursor = index >= 0 ? "pointer" : "grab";
        this.draw();
      }
    }

    onWheel(e) {
      e.preventDefault();
      const { y } = this.point(e);
      const factor = Math.pow(1.2, -(e.deltaY || e.deltaX) / 100);
      const keys = wheelKeys(e);
      if (keys.ctrl && keys.shift) return this.zoomBranches(factor, this.firstNodeX());
      if (keys.ctrl) return this.zoomTips(factor, y);
      if (keys.shift) this.scrollX += e.deltaY || e.deltaX;
      else { this.scrollY += e.deltaY; this.scrollX += e.deltaX; }
      this.clampScroll();
      this.draw();
    }

    onMouseDown(e) {
      if (e.button !== 0 || !this.nodes) return;
      this.canvas.focus();
      const { x, y } = this.point(e), { v, hz } = this.bars();
      if (v && x >= this.areaWidth && y < this.areaHeight) return this.dragBar(e, "y", y < v.top || y > v.top + v.size);
      if (hz && y >= this.areaHeight && y < this.areaHeight + SCROLLBAR) return this.dragBar(e, "x", x < hz.left || x > hz.left + hz.size);
      if (y > this.areaHeight) return;
      // a drag moves the tree; a click (no movement) selects
      const start = { x: e.clientX, y: e.clientY, sx: this.scrollX, sy: this.scrollY };
      let moved = false;
      const move = (ev) => {
        if (!moved && Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 4) return;
        moved = this.panning = true;
        this.canvas.style.cursor = "grabbing";
        this.scrollX = start.sx - (ev.clientX - start.x);
        this.scrollY = start.sy - (ev.clientY - start.y);
        this.clampScroll();
        this.draw();
      };
      const up = () => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
        this.panning = false;
        this.canvas.style.cursor = "grab";
        if (!moved) this.select(this.nodeAt(x, y), {toggle: e.ctrlKey || e.metaKey, range: e.shiftKey});
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    }

    // Dragging a scrollbar's thumb; a click beside it jumps there
    dragBar(e, axis, jump) {
      const content = axis === "y" ? this.contentHeight() : this.contentWidth();
      const track = axis === "y" ? this.areaHeight : this.areaWidth;
      const at = (ev) => (axis === "y" ? this.point(ev).y : this.point(ev).x);
      const view = axis === "y" ? this.areaHeight : this.areaWidth;
      const set = (pos) => {
        if (axis === "y") this.scrollY = pos; else this.scrollX = pos;
        this.clampScroll();
        this.draw();
      };
      if (jump) set((at(e) / track) * content - view / 2);
      const start = at(e), from = axis === "y" ? this.scrollY : this.scrollX;
      const move = (ev) => set(from + ((at(ev) - start) / track) * content);
      const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    }

    onDoubleClick(e) {
      const { x, y } = this.point(e);
      const index = this.nodeAt(x, y);
      if (index > 0 && this.nodes[index].kids.length) this.toggleCollapse(index);
    }

    onContextMenu(e) {
      e.preventDefault();
      const { x, y } = this.point(e);
      const index = this.nodeAt(x, y);
      if (index < 0) return;
      if (!this.inSelection(this.nodes[index])) this.select(index);
      const n = this.nodes[index];
      this.options.onNodeMenu?.(e, { index, root: index === 0, tip: !n.kids.length, collapsed: this.collapsed.has(index),
                                     name: n.name, tips: n.tips });
    }


  }

  window.TreeView = TreeView;
})();
