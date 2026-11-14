// Run with: node tests/test_sequence_find.js
const assert = require('node:assert/strict');
global.window = global;
require('../cladia/static/seqview.js');

function make(rows, protein = false) {
  const view = Object.create(SeqView.prototype);
  view.mode = 'alignment';
  view.protein = protein;
  view.rows = rows.map(([name, seq, features = []]) => ({
    name, seq, features, bases: seq.replace(/-/g, '').length,
  }));
  view.length = Math.max(...view.rows.map(row => row.seq.length));
  view.draw = () => {};
  return view;
}

// Accessions must search row names literally, never become an "NC" motif.
const view = make([
  ['other', 'NCNCNC'],
  ['NC_013255.1_p011|cox1', '--ACGT'],
  ['NC_013255.2_p011|cox1', 'NCNCNC'],
  ['NC_013255.1_p012|ai7', 'ACGT--'],
]);
assert.equal(view.find(' nc_013255.1 '), 2);
assert.deepEqual(view.matches, [
  { row: 1, c0: 0, c1: 5, name: 'NC_013255.1_p011|cox1' },
  { row: 3, c0: 0, c1: 5, name: 'NC_013255.1_p012|ai7' },
]);
const selected = [], scrolled = [], centered = [];
view.select = (...args) => selected.push(args);
view.centerOn = mid => centered.push(mid);
view.scrollToRow = row => scrolled.push(row);
assert.equal(view.findNext(), 0);
assert.equal(view.findNext(), 1);
assert.equal(view.findNext(), 0);
assert.equal(view.findNext(-1), 1);
assert.deepEqual(scrolled, [1, 3, 1, 3]);
assert.deepEqual(selected[0], [0, 5, 1, 1]);
assert.deepEqual(centered, [3, 3, 3, 3]);
assert.equal(view.find('NC_999999.1'), 0);
assert.equal(view.findNext(), null);
assert.equal(view.find(''), 0);
assert.equal(view.matchIndex, -1);
assert.equal(view.find('  '), 0);

// Motifs retain alignment coordinates, reverse strands and overlapping hits.
const dna = make([['first', '--A-CG---CGT'], ['second', 'AAAA']]);
assert.equal(dna.find(' a-c g '), 2);
assert.deepEqual(dna.matches, [
  { row: 0, c0: 2, c1: 5 },
  { row: 0, c0: 9, c1: 11 },
]);
assert.equal(dna.find('AAA'), 2);
assert.deepEqual(dna.matches.map(m => [m.row, m.c0, m.c1]), [[1, 0, 2], [1, 1, 3]]);
assert.equal(make([['first', 'ATAT']]).find('AT'), 2, 'Palindromes count once per position');
assert.equal(make([['first', 'ACGCGT']], true).find('ACG'), 1, 'Proteins have no reverse strand');

// Feature names still match literally, including digits and punctuation.
const features = make([['first', 'NCNCNC', [{ name: 'cox1.2', c0: 1, c1: 4 }]]]);
assert.equal(features.find('COX1.2'), 1);
assert.deepEqual(features.matches, [{ row: 0, c0: 1, c1: 4, feature: 'cox1.2' }]);
console.log('Sequence Find: literal accessions, row navigation, motifs and features passed');
