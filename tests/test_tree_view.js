// Tree zoom uses rendered branch+label bounds, keeping label pixels unscaled.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const ctx = new Proxy({font: '12px sans-serif', measureText(text) {
  const size = Number(this.font.match(/([\d.]+)px/)?.[1] || 12);
  return {width: text.length * size * .5 * (this.font.startsWith('bold') ? 1.1 : 1)};
}}, {get: (object, key) => key in object ? object[key] : () => {}});
const canvas = {style: {}, addEventListener() {}, getContext: () => ctx};
const sandbox = {window: {devicePixelRatio: 1}, document: {createElement: () => canvas},
  ResizeObserver: class {observe() {}}};
vm.runInNewContext(fs.readFileSync('cladia/static/treeview.js', 'utf8'), sandbox);
const long = 'Selected_species_with_a_long_tip_label';
function make() {
  const view = new sandbox.window.TreeView({clientWidth: 800, clientHeight: 500, append() {}});
  view.setTree([
    {parent: -1, length: 0}, {parent: 0, length: 1},
    {parent: 1, length: 1, name: long}, {parent: 1, length: .5, name: 'Short'},
    {parent: 0, length: 10, name: 'Outgroup'},
  ]);
  return view;
}
function right(view, node) {
  const text = node.kids.length ? `${node.tips} tips` : node.name;
  const size = Math.max(9, Math.min(13, view.spacing * .75));
  ctx.font = `${view.matches.includes(node) ? 'bold ' : ''}${size}px sans-serif`;
  const end = node.kids.length ? 16 + node.deepest * view.xScale - view.scrollX : view.xOf(node);
  const start = view.show.align ? view.alignX() : end + 5;
  return start + ctx.measureText(text).width;
}
function centered(view, index) {
  const node = view.nodes[index];
  const rows = view.rows.filter(n => n === node || view.inSelection(n));
  const edge = Math.max(...rows.map(n => right(view, n)));
  assert(Math.abs((view.xOf(node) + edge) / 2 - view.areaWidth / 2) < 1e-7);
}
for (const align of [false, true]) {
  const view = make(); view.setShow({align}); view.select(1);
  view.zoomBranches(8);
  centered(view, 1);
  // Repeated zooming must not scale the fixed-size label offset or drift.
  view.zoomBranches(1.2); centered(view, 1);
  view.zoomBranches(1 / 1.2); centered(view, 1);
}
{
  const view = make(); view.toggleCollapse(1); view.select(1); view.zoomBranches(8);
  centered(view, 1); // triangle end + "2 tips", rather than hidden leaf names
}
{
  const view = make(); view.find(long, false); view.zoomBranches(8);
  centered(view, 2); // Find renders names in bold; use that width too.
}
{
  const view = make(); view.select(2); view.xScale *= 8;
  view.scrollX = 16 + view.nodes[2].depth * view.xScale + 20;
  view.clampScroll();
  assert(view.xOf(view.nodes[2]) < 0 && right(view, view.nodes[2]) > 0);
  assert(view.focusNode(), 'A visible selected label retains its zoom focus');
  view.zoomBranches(1.1); centered(view, 2);
}
{
  const view = make(); view.xScale *= 4; view.scrollX = 200;
  const x = 250, depth = (x + view.scrollX - 16) / view.xScale;
  view.zoomBranches(1.3, x);
  assert(Math.abs(16 + depth * view.xScale - view.scrollX - x) < 1e-7, 'Unselected zoom keeps its original anchor');
}
{
  const view = make();
  view.setTree([{parent: -1}, {parent: 0, name: 'Very_long_label_'.repeat(30)}, {parent: 0, name: 'Other'}]);
  view.scrollX = view.contentWidth(); view.clampScroll();
  assert(Math.max(...view.rows.map(n => right(view, n))) <= view.areaWidth - 15.9, 'Can scroll to the complete label');
  view.spacing = 9; const shown = view.contentWidth();
  view.spacing = 8; assert.equal(view.contentWidth(), shown, 'Hiding labels does not jump horizontal scroll limits');
}
{
  const view = make(), original = view.labelRight.bind(view); let calls = 0;
  view.labelRight = n => {calls++; return original(n);};
  view.measuredContent = null;
  view.contentWidth(); const first = calls;
  view.contentWidth(); view.draw();
  assert.equal(calls, first, 'Unchanged frames reuse measured content bounds');
  view.find(long, false);
  assert(calls > first, 'Find bolding invalidates the measured bounds');
}
console.log('Tree zoom: labels, aligned names, collapsed clades, Find, scroll limits passed');

// Explicit fitting frames a selected clade even when it is currently offscreen.
for (const align of [false, true]) {
  const v = make(); v.setShow({align}); v.select(1);
  v.scrollX = 1e6; v.scrollY = 1e6;
  v.fitSelection();
  for (const n of v.rows.filter(n => v.inSelection(n))) {
    assert(v.yOf(n) >= 14 && v.yOf(n) <= v.areaHeight - 14);
    assert(right(v, n) <= v.areaWidth - 15.9);
  }
  assert(v.xOf(v.nodes[1]) >= 15.9);
}
{
  const v = make();
  v.setTree([{parent: -1}, ...Array.from({length: 200}, (_, i) => ({parent: 0, length: i + 1, name: `Tip_${i}`}))]);
  v.fitWhole();
  assert(v.spacing < 9, 'Fit whole compresses rows enough to show a large tree');
  assert(v.contentHeight() <= v.areaHeight + 1e-6);
  assert(v.scrollX === 0 && v.scrollY === 0);
  v.select(-1); const before = v.xScale; v.fitSelection(); assert.equal(v.xScale, before);
}

{
  const v = make();
  v.setTree([{parent:-1}, {parent:0,length:1,name:'Long_label_'.repeat(9)}, {parent:0,length:10,name:'Other'}]);
  v.fitWhole(); const scale = v.xScale;
  v.zoomBranches(.8); assert(v.xScale <= scale, 'Zoom out never increases scale after a label-aware fit');
}

// Multiple tips support independent toggles, visible-row ranges and the same
// selection through clade collapse/ladderization, zoom and context menus.
{
  const v=make();
  v.select(2);v.select(4,{toggle:true});
  assert.deepEqual([...v.selectedTipNames()],[long,'Outgroup']);
  v.select(4,{toggle:true});assert.deepEqual([...v.selectedTipNames()],[long]);
  v.select(4,{range:true});assert.deepEqual([...v.selectedTipNames()],['Outgroup']); // last toggled tip remains the range anchor
  v.select(2);v.select(4,{range:true});assert.deepEqual([...v.selectedTipNames()],[long,'Short','Outgroup']);
  v.select(3,{range:true});assert.deepEqual([...v.selectedTipNames()],[long,'Short'],'Range uses its original anchor');
  v.select(4,{toggle:true,range:true});assert.equal(v.selectedTipNames().length,3,'Ctrl+Shift adds a range');
  v.select(1);v.select(2,{toggle:true});assert.deepEqual([...v.selectedTipNames()],['Short']);
  v.select(3,{toggle:true});assert.equal(v.selected,-1,'Toggling off the last tip clears ancestor branches');
  v.select(2);v.toggleCollapse(1);assert(v.inSelection(v.nodes[1]),'A collapsed partial selection stays highlighted');
  assert.deepEqual([...v.selectedTipNames()],[long]);
  v.fitSelection();assert(v.labelRight(v.nodes[1])-v.scrollX<=v.areaWidth-15);
  v.toggleCollapse(1);v.setShow({ladderize:true});
  const first=v.rows[0].index,last=v.rows.at(-1).index;
  v.select(first);v.select(last,{range:true});assert.equal(v.selectedTipNames().length,3,'Range follows ladderized display order');
  v.select(2);v.select(4,{toggle:true});v.fitSelection();
  for(const n of v.rows.filter(n=>v.inSelection(n)))assert(v.labelRight(n)-v.scrollX<=v.areaWidth-15);
  const focus=v.focusNode();assert.equal(focus.rows.length,2);assert.equal(focus.row,(v.nodes[2].row+v.nodes[4].row)/2);
  v.zoomBranches(1.2);
  let menu;
  v.options.onNodeMenu=(e,node)=>{menu=node;};v.point=()=>({x:0,y:0});v.nodeAt=()=>2;
  v.onContextMenu({preventDefault(){}});assert(menu);assert.equal(v.selectedTipNames().length,2,'Right click inside preserves other tips');
  v.nodeAt=()=>3;v.onContextMenu({preventDefault(){}});assert.deepEqual([...v.selectedTipNames()],['Short']);
  v.select(-1);assert.equal(v.selectedTipNames().length,0);
}

{
  const v=make();v.select(2);v.toggleCollapse(1);
  assert(v.describe(1).includes('1 selected'));
  v.select(4,{range:true});assert.equal(v.selectedTipNames().length,3,'A collapsed ancestor retains the range anchor');
}

// Below the text threshold, every visible hit has a fixed-size marker and
// the active result gets a larger ring painted after the other matches.
{
  const v=make(), markers=[];
  v.spacing=3;v.scrollY=0;v.find('o',false);
  const arc=ctx.arc;ctx.arc=(x,y,r)=>markers.push({x,y,r});
  v.draw();
  assert.equal(markers.length,v.matches.length);
  assert.equal(markers.at(-1).r,4.5);
  assert.equal(markers.at(-1).y,v.yOf(v.matches[v.matchIndex]));
  assert(markers.slice(0,-1).every(m=>m.r===2.5));
  markers.length=0;v.findStep(1);
  assert.equal(markers.at(-1).y,v.yOf(v.matches[v.matchIndex]));
  markers.length=0;v.find('',false);assert.equal(markers.length,0);
  v.find(long,false);markers.length=0;v.spacing=9;v.draw();assert.equal(markers.length,0,'Readable labels retain their existing Find highlight');
  v.toggleCollapse(1);v.spacing=3;markers.length=0;v.draw();
  assert.equal(markers.length,1,'Collapsed clades containing a hit are marked');
  assert.equal(markers[0].x,16+v.nodes[1].deepest*v.xScale-v.scrollX+3);
  ctx.arc=arc;
}
