// Run with node tests/test_seqedit.js (from the project folder).
// The editing helpers of seqedit.js: GenBank location text both ways (with partial ends),
// features following insertions, deletions, a new origin and a reverse complement.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const sandbox = { window: {} };
vm.runInNewContext(fs.readFileSync("cladia/static/seqedit.js", "utf8"), sandbox);
const { SeqEdit, SeqDocument } = sandbox.window;
// (objects made inside the sandbox have another Object prototype: compare them as JSON)
const same = (a, b) => assert.deepEqual(a === undefined ? a : JSON.parse(JSON.stringify(a)), b);

// ── Locations ──
const plain = SeqEdit.parseLocation("complement(join(1..50,80..120))", 200);
same(plain.parts, [{ start: 0, end: 49 }, { start: 79, end: 119 }]);
assert.equal(plain.strand, -1);
assert.equal(plain.partial, undefined);
assert.equal(SeqEdit.parseLocation("100..300", 200), null);   // outside
assert.equal(SeqEdit.parseLocation("nonsense", 200), null);

const partial = SeqEdit.parseLocation("<1..>60", 90);
same(partial.partial, { left: true, right: true });
same(SeqEdit.parseLocation("complement(<10..80)", 90).partial, { left: true, right: false });
same(SeqEdit.parseLocation("join(<1..20,40..>60)", 90).partial, { left: true, right: true });
assert.equal(SeqEdit.parseLocation("join(1..20,40..60)", 90).partial, undefined);

// text -> feature -> text is the same text
for (const text of ["<1..>60", "complement(<10..80)", "join(<1..20,40..>60)", "100", "complement(join(1..50,80..120))", "5..>20"]) {
  const where = SeqEdit.parseLocation(text, 200);
  const feature = SeqEdit.withParts({ strand: where.strand, partial: where.partial }, where.parts);
  assert.equal(SeqEdit.locationText(feature), text, text);
}

// ── Editing keeps the markers where they belong ──
const data = { sequence: "ACGT".repeat(25), name: "x", circular: false,
  features: [{ id: "f0", name: "a", type: "CDS", strand: 1, start: 0, end: 59, partial: { left: true, right: true }, notes: {} },
             { id: "f1", name: "b", type: "gene", strand: 1, start: 70, end: 90, notes: {} }] };
const doc = new SeqDocument(JSON.parse(JSON.stringify(data)));
doc.reverseComplement(0, 99);   // the whole sequence turned round: strands flip, so do the ends
const turned = doc.features[0];
assert.equal(turned.strand, -1);
same(turned.partial, { left: true, right: true });
assert.equal(turned.start, 40);
const half = new SeqDocument(JSON.parse(JSON.stringify({ ...data, features: [{ ...data.features[0], partial: { left: true, right: false }, end: 40 }] })));
half.reverseComplement(0, 99);
same(half.features[0].partial, { left: false, right: true });   // "<" on the left becomes ">" on the right
half.undo();
same(half.features[0].partial, { left: true, right: false });

// an insertion before a feature moves it and leaves its markers
const moved = new SeqDocument(JSON.parse(JSON.stringify(data)));
moved.insert(0, "AAAAA");
assert.equal(moved.features[0].start, 5);
same(moved.features[0].partial, { left: true, right: true });

console.log("Sequence editing: locations with partial ends, reverse complement and insertion passed.");
