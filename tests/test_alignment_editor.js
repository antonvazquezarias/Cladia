// Run with: node tests/test_alignment_editor.js
const assert = require('node:assert/strict');
global.window = global;
require('../cladia/static/seqedit.js');
const doc = new AlignmentDocument([
  { name: 'a', sequence: '--AC-GT' },
  { name: 'b', sequence: 'TTAC-G-' },
  { name: 'c', sequence: '--ACCGT' },
]);
assert.equal(doc.removeRows([0, 2]), true);
assert.deepEqual(doc.rows(), [{ name: 'b', sequence: 'TTAC-G-' }]);
assert.equal(doc.removeRows([0]), false);
assert.equal(doc.undo(), true);
assert.deepEqual(doc.names, ['a', 'b', 'c']);
assert.equal(doc.redo(), true);
assert.deepEqual(doc.names, ['b']);
assert.equal(doc.undo(), true);
assert.equal(doc.push(0, 0, 2, 3), true);
assert.deepEqual(doc.names, ['a', 'b', 'c']);
assert.equal(doc.undo(), true);
assert.equal(doc.seqs[0], '--AC-GT');
require('../cladia/static/seqview.js');
const view = Object.create(SeqView.prototype);
view.single = false;
view.mode = 'alignment';
view.rows = [{ name: 'a', seq: '--AC-GT' }, { name: 'b', seq: 'TTAC-G-' }, { name: 'c', seq: '--ACCGT' }];
view.selection = { c0: 0, c1: 6, r0: 0, r1: 2, rows: [0, 2], wholeRows: true };
assert.equal(view.selectionCopy(), '>a\nAC-GT\n>c\nACCGT\n');
view.selection = { c0: 0, c1: 6, r0: 0, r1: 1, wholeRows: true };
assert.equal(view.selectionCopy(), '>a\n--AC-GT\n>b\nTTAC-G-\n');
view.selection = { c0: 0, c1: 4, r0: 0, r1: 1 };
assert.equal(view.selectionCopy(), '>a\n--AC-\n>b\nTTAC-\n');
console.log('Alignment row removal, undo and copy passed');

// Attached VCF reference positions skip both '-' and '.' alignment gaps.
const track = Object.create(SeqView.prototype);
track.mode = 'alignment';
track.rows = [{ name: 'ref', seq: '--AC.GT' }];
track.variantAt = new Map();
track.variantColumns = [];
track.show = { variants: true };
track.layout = () => {};
track.draw = () => {};
track.setVariantSites([{ position: 1, ref: 'A', alt: 'G' }, { position: 3, ref: 'G', alt: 'A' }], 'ref');
assert.deepEqual(track.variantColumns, [2, 5]);
assert.equal(track.headerHeight, 68);
const marks = [];
track.nameWidth = 100;
track.width = 400;
track.charWidth = 4;
track.ctx = { fillRect() {}, set fillStyle(_) {} };
track.colRect = (x) => marks.push(x);
track.drawVariantTrack(0, 7, (column) => 100 + column * 4);
assert.deepEqual(marks, [108, 120]);
track.show.variants = false;
assert.equal(track.headerHeight, 59);
console.log('Alignment SNP track coordinates and visibility passed');
