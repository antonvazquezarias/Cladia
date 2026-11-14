// Run with: node tests/test_variant_matrix.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const vcf = [
  "##fileformat=VCFv4.3",
  "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tref\tsample",
  "ref\t2\t.\tC\tG\t.\tPASS\t.\tGT\t0\t1",
  "ref\t4\t.\tT\tTA\t.\tPASS\tTYPE=INDEL\tGT\t0\t1",
].join("\n") + "\n";
const elements = new Map();
const listeners = new Map();
const $ = (id) => {
  if (!elements.has(id)) elements.set(id, { value: id.endsWith("type") ? "all" : "", hidden: false,
    addEventListener: (event, fn) => listeners.set(`${id}:${event}`, fn) });
  return elements.get(id);
};
const selected = [];
const savedFiles = [];
const seqView = { variantBaseColumns: [0, 1, 2, 3], rows: [{ name: "ref" }, { name: "sample" }],
  select: (...args) => selected.push(args), centerOn: (column) => selected.push(column) };
const seqShown = { variantData: { vcf, stale: false }, alignment: { name: "pair" } };
const context = vm.createContext({ $, seqShown, seqView, navigator: { clipboard: {} },
  esc: (value) => String(value).replaceAll("<", "&lt;"), sideState: { max: true }, renderSidePanel: () => selected.push("panel"),
  saveTextFile: (...args) => savedFiles.push(args), toast() {} });
vm.runInContext(fs.readFileSync("cladia/static/variant-table.js", "utf8"), context);
vm.runInContext(fs.readFileSync("cladia/static/variant-matrix.js", "utf8"), context);
context.updateVariantMatrixView();
assert.match($("variant-matrix-body").innerHTML, /data-site="1" data-sample="1"/);
assert.match($("variant-matrix-body").innerHTML, /class="alt"/);
assert.equal($("variant-matrix-sites-page").textContent, "1–2 of 2");
$("variant-matrix-type").value = "Indel";
listeners.get("variant-matrix-type:change")();
assert.equal($("variant-matrix-sites-page").textContent, "1–1 of 1");
$("variant-matrix-sample").value = "sample";
listeners.get("variant-matrix-sample:input")();
assert.equal($("variant-matrix-samples-page").textContent, "1–1 of 1");
$("variant-matrix-csv").onclick();
assert.match(savedFiles[0][1], /^Sample,4 T>TA\nsample,1\n$/);
listeners.get("variant-matrix-body:click")({ target: { closest: () => ({ dataset: { site: "0", sample: "1" } }) } });
assert.deepEqual(selected, ["panel", [3, 3, 1, 1], 3]);  // a maximised panel gives the alignment room again
console.log("Variant matrix rendering, filtering and navigation passed");
