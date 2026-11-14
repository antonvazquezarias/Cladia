// Run with: node tests/test_variant_table.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const context = vm.createContext({});
vm.runInContext(fs.readFileSync("cladia/static/variant-table.js", "utf8"), context);
const { parseVariantVcf, filterVariantSites, filteredVariantVcf, variantMatrixText } = context;
const vcf = [
  "##fileformat=VCFv4.3",
  "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tref\ta\tb",
  "ref\t2\t.\tC\tG\t.\tPASS\t.\tDP:GT\t4:0\t5:1\t5:.",
  "ref\t4\t.\tT\tA,G\t.\tPASS\t.\tGT\t0\t1/2\t0/0",
].join("\n") + "\n";
const parsed = parseVariantVcf(vcf);
assert.equal(parsed.sites.length, 2);
assert.equal(parsed.sites[0].alternateCount, 1);
assert.equal(parsed.sites[0].type, "SNP");
assert.equal(parsed.sites[0].missingCount, 1);
assert.equal(parsed.sites[0].calls[1].bases, "G");
assert.equal(parsed.sites[1].calls[1].bases, "A/G");
assert.deepEqual(Array.from(filterVariantSites(parsed.sites, { missing: "none" }).map((site) => site.position)), [4]);
assert.deepEqual(Array.from(filterVariantSites(parsed.sites, { missing: "any" }).map((site) => site.position)), [2]);
assert.equal(filterVariantSites(parsed.sites, { minAlternate: 2 }).length, 0);
assert.deepEqual(Array.from(filterVariantSites(parsed.sites, { query: "b" }).map((site) => site.position)), []);
assert.deepEqual(Array.from(filterVariantSites(parsed.sites, { query: "a" }).map((site) => site.position)), [2, 4]);
const indel = parseVariantVcf(vcf.replace("ref\t4\t.\tT\tA,G", "ref\t4\t.\tT\tTA,G"));
assert.equal(indel.sites[1].type, "Indel");
assert.deepEqual(Array.from(filterVariantSites(indel.sites, { type: "Indel" }).map((site) => site.position)), [4]);
const repeat = parseVariantVcf(vcf.replace("ref\t4\t.\tT\tA,G\t.\tPASS\t.", "ref\t4\t.\tT\tTA,G\t.\tPASS\tTYPE=INDEL;RU=A"));
assert.equal(repeat.sites[1].type, "Repeat indel");
assert.equal(filterVariantSites(repeat.sites, { type: "Indel" }).length, 1);
assert.equal(filteredVariantVcf(parsed, [parsed.sites[1]]),
  vcf.split("\n").slice(0, 2).join("\n") + "\n" + parsed.sites[1].line + "\n");
assert.equal(variantMatrixText(parsed, [parsed.sites[0]], [1, 2]),
  "Sample,2 C>G\na,1\nb,.\n");
console.log("Variant table tests passed");
