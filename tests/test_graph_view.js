// Run with node tests/test_graph_view.js.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const strokes = [];
const strokeWidths = [];
const fills = [];
const calls = [];
const ctx = new Proxy({ measureText: (s) => ({ width: s.length * 6 }), stroke() { strokes.push(this.strokeStyle); strokeWidths.push(this.lineWidth); }, fill() { fills.push(this.fillStyle); } }, {
  get(target, key) { return key in target ? target[key] : (...args) => calls.push([key, ...args]); }
});
const canvas = { style: {}, addEventListener() {}, getContext: () => ctx };
const sandbox = { window: { devicePixelRatio: 1 }, document: { createElement: () => canvas }, ResizeObserver: class { observe() {} } };
vm.runInNewContext(fs.readFileSync("cladia/static/graphview.js", "utf8"), sandbox);
const view = new sandbox.window.GraphView({ clientWidth: 800, clientHeight: 500, append() {} });
view.setData({ groups: [{ name: "Group 1", description: "", circular: false, span: 10000,
  nodes: { A: { o: "+", x0: 0, x1: 4990, lane: 0, length: 100, depth: 10, color: "red", hits: [] },
           B: { o: "+", x0: 5000, x1: 10000, lane: 0, length: 100, depth: 10, color: "blue", hits: [] } },
  links: [["A", "end", "B", "start", "forward"]] }] });
view.fit();
assert(view.scale < 0.3);
assert(view.width_ * view.scale <= 800);
strokes.length = 0;
fills.length = 0;
Object.assign(view.options, { links: false, colours: false, labels: "hidden" });
view.draw();
assert.deepEqual(strokes, []);
assert.deepEqual(fills, ["#687280", "#687280"]);
view.choose([view.nodes[0]]);
assert.equal(view.selectedNodes()[0].id, "A");
console.log("Graph view: fit, display options and selection passed.");

// Overlapping return links use distinct rails, and node tapers face opposite
// directions for the original forward and reverse strands.
const node = (x0, x1, o = "+") => ({ o, x0, x1, lane: 0, length: 100, depth: 10, color: "red", hits: [] });
view.setData({ groups: [{ name: "Returns", description: "", circular: false, span: 160,
  nodes: { A: node(0, 40), B: node(50, 90, "-"), C: node(100, 140) },
  links: [["B", "end", "A", "start", "back"], ["C", "end", "A", "start", "back"]] }] });
const links = view.groups[0].links;
assert.notEqual(links[0].routeY, links[1].routeY);
calls.length = 0;
view.drawNode(view.nodes[0]);
assert(calls.some(([op, x]) => op === "lineTo" && x === view.groups[0].left + 40));
calls.length = 0;
view.drawNode(view.nodes[1]);
assert(calls.some(([op, x]) => op === "moveTo" && x === view.groups[0].left + 50));
view.setData({ groups: [{ name: "Self", description: "", circular: true, span: 46,
  nodes: { A: node(0, 40) }, links: [["A", "end", "A", "start", "back"]] }] });
calls.length = 0;
view.drawLinks(view.groups[0]);
assert(calls.some(([op]) => op === "arc"));
assert(!calls.some(([op]) => op === "lineTo" || op === "quadraticCurveTo"));
console.log("Graph view: return rails, directional tips and circular connector passed.");
const localNodes = {
  "1147600": { ...node(56.6, 75.8, "-"), lane: 0 },
  "1114471": { ...node(81.8, 111.5, "-"), lane: 2 },
  "282079": { ...node(163.7, 179.9, "-"), lane: 0 }
};
view.setData({ groups: [{ name: "Local circular links", description: "", circular: true, span: 475.8,
  nodes: localNodes, links: [["1147600", "end", "1114471", "start", "forward"], ["1114471", "end", "282079", "start", "forward"]] }] });
for (const link of view.groups[0].links) {
  const group = view.groups[0];
  calls.length = 0;
  view.drawLinks({ ...group, links: [link] });
  const curve = calls.find(([op]) => op === "bezierCurveTo");
  assert(curve);
  assert(!calls.some(([op]) => op === "quadraticCurveTo"));
  const a = view.end(link.a, link.sa), b = view.end(link.b, link.sb);
  const c = [curve[1], curve[2]], d = [curve[3], curve[4]];
  for (const [n, side, p, control] of [[link.a, link.sa, a, c], [link.b, link.sb, b, d]]) {
    const angle = 2 * Math.PI * (side === "end" ? n.x1 : n.x0) / group.span - Math.PI / 2;
    const dir = side === "end" ? 1 : -1;
    const dx = control[0] - p[0], dy = control[1] - p[1];
    assert(Math.hypot(dx, dy) <= 60.00001);
    assert(Math.abs(dx * Math.cos(angle) + dy * Math.sin(angle)) < 1e-8);
    assert(dx * -Math.sin(angle) * dir + dy * Math.cos(angle) * dir > 0);
  }
  if (link.a.id === "1147600") {
    const radius = Math.min(Math.hypot(a[0] - group.cx, a[1] - group.cy), Math.hypot(b[0] - group.cx, b[1] - group.cy));
    for (let i = 0; i <= 20; i++) {
      const t = i / 20, u = 1 - t;
      const x = u**3*a[0] + 3*u*u*t*c[0] + 3*u*t*t*d[0] + t**3*b[0];
      const y = u**3*a[1] + 3*u*u*t*c[1] + 3*u*t*t*d[1] + t**3*b[1];
      assert(Math.hypot(x - group.cx, y - group.cy) >= radius - 1);
    }
  }
}
console.log("Graph view: actual 1114471 endpoint geometry and local routing passed.");

// A self-link on a short branch stays near that branch, while a lone circular
// segment keeps its circumference-following connector.
view.setData({ groups: [{ name: "Branch self-loop", description: "", circular: true, span: 300,
  nodes: { main: node(0, 250), branch: { ...node(100, 130), lane: 1 } },
  links: [["branch", "end", "branch", "start", "back"]] }] });
calls.length = 0;
view.drawLinks(view.groups[0]);
assert(!calls.some(([op]) => op === "arc"));
const curves = calls.filter(([op]) => op === "bezierCurveTo");
assert.equal(curves.length, 2);
const branch = view.groups[0].nodes.get("branch");
const middle = view.point(branch, 115);
for (const curve of curves) {
  for (let i = 1; i < curve.length; i += 2) {
    assert(Math.hypot(curve[i] - middle[0], curve[i + 1] - middle[1]) < 40);
  }
}
console.log("Graph view: branch self-link remains local.");

// A distant lower node must not push an unrelated return link below the group.
view.setData({ groups: [{ name: "Sparse", description: "", circular: false, span: 400,
  nodes: { A: node(0, 40), B: node(70, 100), distant: { ...node(300, 340), lane: 10 } },
  links: [["B", "end", "A", "start", "back"]] }] });
assert(Math.abs(view.groups[0].links[0].routeY) <= 10);
console.log("Graph view: return links ignore empty distant lanes.");

// Nearby nodes must not force the connector into a narrow channel.
view.setData({ groups: [{ name: "Broad bend", description: "", circular: false, span: 140,
  nodes: { source: node(71.1, 87), neighbour: node(93, 112.5), target: { ...node(114.7, 134.3), lane: 1 } },
  links: [["source", "end", "target", "start", "forward"]] }] });
calls.length = 0;
view.drawLinks(view.groups[0]);
const broad = calls.filter(([op]) => op === "bezierCurveTo");
assert.equal(broad.length, 1);
assert.equal(calls.filter(([op]) => op === "quadraticCurveTo").length, 0);
const start = view.end(view.nodes[0], "end"), end = view.end(view.nodes[2], "start");
assert(Math.abs(broad[0][1] - start[0] - (end[0]-start[0])*0.12) < 1e-8);
console.log("Graph view: broad curve retained despite a nearby node.");
// Ordinary circular joins should follow the ring rather than its chord.
view.setData({ groups: [{ name: "Arc join", description: "", circular: true, span: 400,
  nodes: { A: node(0, 30), B: node(100, 130) }, links: [["A", "end", "B", "start", "forward"]] }] });
const ag = view.groups[0], al = ag.links[0], ac = al.curves[0], ap = view.end(al.a, al.sa);
for (let i=0;i<=30;i++) {
  const t=i/30,s=1-t;
  const x=s**3*ap[0]+3*s*s*t*ac[0]+3*s*t*t*ac[2]+t**3*ac[4];
  const y=s**3*ap[1]+3*s*s*t*ac[1]+3*s*t*t*ac[3]+t**3*ac[5];
  assert(Math.abs(Math.hypot(x-ag.cx,y-ag.cy)-ag.R)<0.1);
}
console.log("Graph view: circular joins follow the ring circumference.");
// Label rectangles must avoid nodes and each other at crowded lane spacing.
Object.assign(view.options, { labels: "auto", links: true });
view.setData({ groups: [{ name: "Labels", description: "", circular: false, span: 220,
  nodes: { first_node: node(0, 100), second_node: { ...node(0, 100), lane: 1 }, third_node: { ...node(110, 210), lane: 0 } },
  links: [["first_node", "end", "third_node", "start", "forward"]] }] });
view.choose([view.nodes[1]]);
view.drawLabels(view.groups);
assert(view.labelBoxes.some((b) => b.id === "second_node"));
const intersect = (a,b) => a[0]<b[0]+b[2] && a[0]+a[2]>b[0] && a[1]<b[1]+b[3] && a[1]+a[3]>b[1];
for(let i=0;i<view.labelBoxes.length;i++){
  const a=view.labelBoxes[i].rect;
  for(let j=0;j<i;j++) assert(!intersect(a,view.labelBoxes[j].rect));
  for(const n of view.nodes){
    const p=view.point(n,n.x0),q=view.point(n,n.x1);
    const box=[p[0]*view.scale+view.ox,p[1]*view.scale+view.oy-2.5*view.scale,(q[0]-p[0])*view.scale,5*view.scale];
    assert(!intersect(a,box));
  }
}
view.options.labels="hidden";
view.drawLabels(view.groups);
assert.equal(view.labelBoxes.length,0);
console.log("Graph view: labels avoid nodes and each other, prioritise selection and respect Hidden.");

// Highlight the actual filled node outline, without a thick centreline halo.
view.options.labels = "auto";
view.scale = 2;
view.choose([view.nodes[0]]);
strokeWidths.length = 0;
fills.length = 0;
view.drawNode(view.nodes[0]);
assert(strokeWidths.length === 1);
assert.equal(strokes[strokes.length - 1], "#d81b60");
assert(strokeWidths[0] * view.scale <= 1.5);
assert(fills.includes("rgba(216, 27, 96, 0.14)"));
console.log("Graph view: selection uses a subtle tint and thin outline.");

// Multi-node path selection persists on connectors despite hover changes.
view.setData({ groups: [{ name: "Path", description: "", circular: false, span: 180,
  nodes: { A: node(0,40), B: node(50,90), C: node(100,140) },
  links: [["A","end","B","start","forward"], ["B","end","C","start","forward"]] }] });
view.choose([view.nodes[0],view.nodes[1]]);
for(const hover of [null,view.nodes[2]]){
  view.hovered=hover;
  strokes.length=0;
  view.drawLinks(view.groups[0]);
  assert.equal(strokes[0],"#d81b60");
  if(!hover) assert.notEqual(strokes[1],"#d81b60");
}
view.choose([]);view.hovered=null;
strokes.length=0;view.drawLinks(view.groups[0]);
assert(!strokes.includes("#d81b60"));
console.log("Graph view: selected path connectors persist until deselected.");
const ids = (list) => Array.from(list, n=>n.id);
view.setData({groups:[{name:"Bubble",description:"",circular:false,span:240,
 nodes:{A:node(0,40),B:{...node(50,90),lane:1},C:{...node(50,90),lane:-1},D:node(100,140),unrelated:node(60,80)},
 links:[["A","end","B","start","forward"],["B","end","D","start","forward"],
        ["A","end","C","start","forward"],["C","end","D","start","forward"]]}]});
let byId=view.groups[0].nodes;
view.choose([byId.get("A")]);
assert.deepEqual(ids(view.stretch(byId.get("D"))),["A","B","D"]);
view.choose([byId.get("A"),byId.get("C")],byId.get("A"));
assert.deepEqual(ids(view.stretch(byId.get("D"))),["A","C","D"]);
assert.deepEqual(ids(view.stretch(byId.get("unrelated"))),[]);
view.choose([byId.get("D")]);
assert.deepEqual(ids(view.stretch(byId.get("A"))),["D","B","A"]);
// These edges touch the same end of the middle node: no continuous walk.
view.setData({groups:[{name:"Invalid walk",description:"",circular:false,span:180,
 nodes:{A:node(0,40),B:node(50,90),C:node(100,140)},
 links:[["A","end","B","start","forward"],["B","start","C","start","twisted"]]}]});
view.choose([view.nodes[0]]);
assert.deepEqual(ids(view.stretch(view.nodes[2])),[]);
// A route across the ring origin is a single link, independent of x ordering.
view.setData({groups:[{name:"Ring",description:"",circular:true,span:200,
 nodes:{A:node(0,40),B:node(50,90),C:node(100,140)},
 links:[["A","end","B","start","forward"],["B","end","C","start","forward"],["C","end","A","start","back"]]}]});
view.choose([view.nodes[0]]);
assert.deepEqual(ids(view.stretch(view.nodes[2])),["A","C"]);
canvas.getBoundingClientRect=()=>({left:0,top:0});
view.onWheel({preventDefault(){},ctrlKey:true,clientX:100,clientY:100,deltaY:-10000});
assert.equal(view.scale,Math.max(8,3*view.minScale()));  // the most zoomed in: 8x, or 3x the fit of a small graph
console.log("Graph view: connected Shift paths, branch ties, orientation, ring origin and the zoom limit passed.");

// ── Labels with length and depth when they fit; Ctrl+C copies names ──
const handlers = {};
canvas.addEventListener = (type, fn) => { handlers[type] = fn; };
const copied = [];
const v2 = new sandbox.window.GraphView({ clientWidth: 800, clientHeight: 500, append() {} }, { onCopy: (names) => copied.push(names) });
canvas.addEventListener = () => {};
const longNode = { o: "+", x0: 0, x1: 400, lane: 0, length: 12345, depth: 56.24, color: "red", hits: [] };
v2.setData({ groups: [{ name: "G", description: "", circular: false, span: 400, nodes: { k141_7: longNode }, links: [] }] });
v2.fit();
const labels = () => { calls.length = 0; v2.draw(); return calls.filter((c) => c[0] === "fillText").map((c) => c[1]); };
assert(labels().includes(`k141_7 · ${(12345).toLocaleString()} bp · 56.2×`),
       "a node long enough on screen shows its length and depth");
v2.setData({ groups: [{ name: "G", description: "", circular: false, span: 400, nodes: { k141_7: { ...longNode, x1: 60, depth: 0 } }, links: [] }] });
v2.scale = 0.3;  // zoomed out: the node is short on screen
v2.draw();
const short = labels();
assert(!short.some((t) => t.includes(" bp")), "a short node is not given more than its name: " + short.join("|"));
assert.deepEqual(copied, []);
v2.choose([v2.nodes[0]]);
handlers.keydown({ ctrlKey: true, key: "c", preventDefault() {} });
assert.deepEqual(JSON.parse(JSON.stringify(copied)), [["k141_7"]]);  // (made in the sandbox: another Array prototype)
v2.choose([]);
handlers.keydown({ ctrlKey: true, key: "c", preventDefault() {} });
assert.equal(copied.length, 1, "nothing selected or hovered: nothing copied");
console.log("Graph view: node labels with length and depth, and copying names passed.");

// ── Compact: the rings together, centres level, the other groups under them ──
{
  const n = (x0, x1, lane = 0) => ({ o: "+", x0, x1, lane, length: 100, depth: 10, color: "red", hits: [] });
  const ring = (name, span) => ({ name, description: "", circular: true, span,
    nodes: { a: n(0, span / 2 - 10), b: n(span / 2, span - 10) },
    links: [["a", "end", "b", "start", "forward"], ["b", "end", "a", "start", "back"]] });
  const chain = (name) => ({ name, description: "", circular: false, span: 300, nodes: { a: n(0, 140), b: n(160, 300) },
    links: [["a", "end", "b", "start", "forward"]] });
  const data = { groups: [chain("Chain 1"), ring("Ring big", 1200), chain("Chain 2"), ring("Ring small", 500)] };
  const v = new sandbox.window.GraphView({ clientWidth: 1200, clientHeight: 700, append() {} }, {});
  v.setData(data);
  const mixed = v.height_;
  v.options.compact = true;
  v.arrange();
  const rings = v.groups.filter((g) => g.circular), chains = v.groups.filter((g) => !g.circular);
  assert.equal(rings[0].cy, rings[1].cy, "the rings' centres are level");
  assert(rings[0].left + rings[0].width <= rings[1].left || rings[1].left + rings[1].width <= rings[0].left, "side by side");
  const ringBottom = Math.max(...rings.map((g) => g.cy + g.outer));
  assert(chains.every((g) => g.top >= ringBottom), "the other groups are under the rings");
  assert(v.height_ <= mixed, `compact takes no more room (${v.height_} against ${mixed})`);
  v.options.compact = false;
  v.arrange();
  assert.equal(v.height_, mixed, "turning it off packs as before");
  console.log("Graph view: compact arrangement of rings passed.");
}

// Fit selection includes circular quadrant extrema and uses path nodes in Path mode.
{
 const v = new sandbox.window.GraphView({clientWidth:800,clientHeight:500,append(){}});
 v.setData({groups:[{name:'ring',description:'',circular:true,span:1000,nodes:{
  A:{o:'+',x0:0,x1:900,lane:0,length:900,depth:10,color:'red',hits:[]},
  B:{o:'+',x0:900,x1:1000,lane:0,length:100,depth:10,color:'blue',hits:[]}},links:[]}]});
 v.choose([v.nodes[0]]);v.fitSelection();
 for(const x of [0,250,500,750,900]){
  const [px,py]=v.point(v.nodes[0],x);
  assert(px*v.scale+v.ox>=0 && px*v.scale+v.ox<=800);
  assert(py*v.scale+v.oy>=0 && py*v.scale+v.oy<=480);
 }
 v.chosen.clear();v.pathMode=true;v.path=[{node:v.nodes[1],dir:'right'}];
 v.fitSelection();assert(v.scale>v.minScale());
 v.fit();assert.equal(v.scale,v.minScale());
}
