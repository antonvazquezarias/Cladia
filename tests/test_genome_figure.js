// Pure genome geometry, SVG and shared GC tests (no browser dependency).
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const sandbox = { window: {}, document: { createElement: () => ({ getContext: () => ({ measureText: t => ({ width: t.length * 3 }) }) }) } };
for (const file of ["seqedit.js", "dotplot.js", "figure-genome.js", "figure-dotplot.js", "figure.js"])
  vm.runInNewContext(fs.readFileSync(`cladia/static/${file}`, "utf8"), sandbox);
const kind = sandbox.window.FigureEditor.kinds.genome;
{ // the dot plot figure: SVG paths of both colours, a page as wide as set
  const dk = sandbox.window.FigureEditor.kinds.dotplot;
  const seq = "ACGTTAGCCATGGATCCAGT".repeat(30), rc = [...seq].reverse().map(c => ({ A: "T", C: "G", G: "C", T: "A" })[c]).join("");
  const d = dk.draw(JSON.parse(JSON.stringify(dk.defaults)), dk.prepare({ a: { name: "a", seq }, b: { name: "b", seq: seq + rc } }));
  assert.match(d.body, /fill="#1f5fa8" d="M/); assert.match(d.body, /fill="#c0392b" d="M/);
  assert.ok(d.height > d.width);  // B is twice as long, drawn to the same scale
}
const gc = sandbox.window.SeqEdit.gcWindows("GGCCATNNNNacgt", 4);
assert.equal(gc[0].gc, 1); assert.equal(gc[1].gc, 0); assert.equal(gc[1].called, 2);
assert.equal(gc[2].gc, .5); assert.equal(gc[3].gc, .5);
assert.equal(sandbox.window.SeqEdit.gcWindows("NN--", 4)[0].gc, null);
assert.equal(sandbox.window.SeqEdit.gcWindows("G", 100)[0].end, 1);
assert.equal(sandbox.window.SeqEdit.gcWindows("", 4).length, 0);
const data = { sequence: "ACGT".repeat(2500), organism: "Gracilaria cf. gracilis", name: "test", circular: true,
  features: [
    { type: "CDS", name: "psaA", start: 10, end: 1200, strand: 1 },
    { type: "CDS", name: "cox1", start: 1500, end: 4000, strand: -1, locations: [{ start: 1500, end: 2200 }, { start: 2500, end: 4000 }] },
    { type: "tRNA", name: "trnM", start: 4400, end: 4470, strand: -1 },
    { type: "rRNA", name: "rnl", start: 4700, end: 7100, strand: 1 },
    { type: "CDS", name: "nad5", start: 9400, end: 200, strand: 1, locations: [{ start: 9400, end: 9999 }, { start: 0, end: 200 }] },
    { type: "CDS", name: "partial", start: 8000, end: 8300, strand: -1, partial: { left: true } },
  ] };
const settings = () => JSON.parse(JSON.stringify(kind.defaults));
const source = kind.prepare(data), s = settings();
assert.equal(kind.initial(source).shape, "circular");
assert.equal(kind.initial(kind.prepare({ ...data, circular: false })).shape, "linear");
const a = kind.helpers.featureArcs(data.features[4], 10000);
assert.equal(a.length, 2); assert.equal(a.filter(x => x.head).length, 1); assert(a[1].head);
const reverse = kind.helpers.featureArcs({ ...data.features[4], strand: -1 }, 10000);
assert(reverse[0].head); assert(!reverse[1].head);
assert(!kind.helpers.featureArcs(data.features[5], 10000).some(p => p.head));
assert.equal(kind.helpers.classOf(data.features[0], s.classes).id, "psa");
assert.equal(kind.helpers.classOf(data.features[2], s.classes).id, "trna");
assert.equal(kind.helpers.classOf({ type: "CDS", name: "ATP6" }, s.classes).id, "atp");
assert.equal(kind.helpers.classOf({ type: "CDS", name: "unrecognised" }, s.classes).id, "unknown");
for (const shape of ["circular", "linear"]) {
  s.shape = shape; s.gc.show = true;
  s.regions = [{ id: "ir", name: "IRa", start: 9000, end: 1000, color: "#123456" }];
  const drawing = kind.draw(s, source, null);
  assert(!/NaN|Infinity|undefined/.test(drawing.body));
  assert(drawing.body.includes("psaA") && drawing.body.includes("#176b4b"));
  assert.equal((drawing.body.match(/data-role="feature"/g) || []).length, 6);
  assert.equal((drawing.body.match(/data-arrow="true"/g) || []).length, 5);
  assert.equal(drawing.namedFeatures, drawing.placedLabels + drawing.omittedLabels);
  assert(drawing.body.includes('data-role="gc-window"') && drawing.body.includes('data-role="gc-mean"'));
  assert(!drawing.body.includes("textPath"));
  for (let i = 0; i < drawing.labelBoxes.length; i++) for (let j = i + 1; j < drawing.labelBoxes.length; j++)
    assert(!kind.helpers.overlaps(drawing.labelBoxes[i], drawing.labelBoxes[j]));
  assert(!drawing.body.includes("fig-sel"));
}
const key = source.features[0].key;
s.featureEdits[key] = { color: "#123abc", label: "renamed" };
assert(kind.draw(s, source, null).body.includes("#123abc"));
assert.equal(kind.helpers.featureKey({ ...data.features[0], start: 11 }) === key, false);
kind.action("hideFeature", s, { type: "feature", key }, source);
assert.equal((kind.draw(s, source, null).body.match(/data-role="feature"/g) || []).length, 5);
kind.action("resetFeature", s, { type: "feature", key }, source);
assert(!s.featureEdits[key]);
s.shape = "linear"; s.range = { from: 3000, to: 6000 };
assert(!/NaN|Infinity/.test(kind.draw(s, source, null).body));
const dense = kind.prepare({ ...data, sequence: "ACGT".repeat(37500), features: Array.from({ length: 300 }, (_, i) =>
  ({ type: "CDS", name: `long_gene_name_${i}`, start: i * 500, end: i * 500 + 350, strand: i % 2 ? 1 : -1 })) });
s.shape = "circular"; s.regions = []; s.range = { from: 1, to: 0 };
const crowded = kind.draw(s, dense, null);
assert.equal(crowded.namedFeatures, 300); assert.equal(crowded.placedLabels + crowded.omittedLabels, 300);
assert(crowded.placedLabels > 0);
assert(crowded.height < crowded.width * 1.5);
assert.equal((crowded.body.match(/data-orientation="horizontal"/g) || []).length, crowded.placedLabels);
for (const box of crowded.labelBoxes) for (const [x,y] of box) {
  assert(x >= 0 && x <= crowded.width && y >= 0 && y <= crowded.height);
}
for (let i=0; i<crowded.labelBoxes.length; i++) for (let j=i+1; j<crowded.labelBoxes.length; j++)
  assert(!kind.helpers.overlaps(crowded.labelBoxes[i], crowded.labelBoxes[j]));
s.labels.circular = "columns";
const columns = kind.draw(s, dense, null);
assert.equal(columns.omittedLabels, 0);
assert(columns.height > columns.width);
s.labels.circular = "radial";
assert(kind.draw(s, dense, null).omittedLabels > 0);
s.labels.circular = "horizontal";
assert(!/NaN|Infinity/.test(crowded.body));
// Leader origins follow each strand's actual lane, for either outside-strand choice.
for (const outside of ['+', '-']) for (const style of ['horizontal','radial','columns']) {
  const layout = settings(); layout.outside = outside; layout.labels.circular = style;
  const d = kind.draw(layout, source, null);
  const circle = d.body.match(/<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"/);
  const [cx, cy, radius] = circle.slice(1).map(Number);
  const leaders = [...d.body.matchAll(/<g data-feature="([^"]+)" data-role="label"[^>]*>\s*<path data-role="leader"[^>]*d="M([\d.-]+),([\d.-]+)/g)];
  assert(leaders.length > 0);
  for (const match of leaders) {
    const f = source.features.find(f => f.key === match[1]);
    const expected = radius + (((f.strand === -1) === (outside === '-')) ? layout.track.height + 1 : -1);
    assert(Math.abs(Math.hypot(Number(match[2])-cx, Number(match[3])-cy) - expected) < .02);
  }
}
// Dense names stay in their own angular neighbourhood, without accumulated drift.
for (const match of crowded.body.matchAll(/data-angle-offset="([\d.-]+)"/g))
  assert(Math.abs(Number(match[1])) <= .601);
console.log("Genome figure: geometry, strands, wraps, partial ends, labels, GC, overrides and dense layout passed");
if (process.argv[2] === "--svg") {
  const exportSettings = settings(); exportSettings.gc.show = true;
  const d = kind.draw(exportSettings, source, null);
  fs.writeFileSync(process.argv[3], `<svg xmlns="http://www.w3.org/2000/svg" width="174mm" height="${d.height / (72 / 25.4)}mm" viewBox="0 0 ${d.width} ${d.height}"><rect width="100%" height="100%" fill="#fff"/>${d.body}</svg>`);
}
