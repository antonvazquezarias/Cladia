// VCF site summaries for the alignment's Variants panel. Genotypes are counted
// per sample, so a diploid 1/1 call contributes one alternate-carrying sample.
function parseVariantVcf(text) {
  const lines = text.split(/\r?\n/).filter(Boolean);
  const header = lines.find((line) => line.startsWith("#CHROM\t"));
  if (!header) return { headers: lines.filter((line) => line.startsWith("#")), samples: [], sites: [] };
  const samples = header.split("\t").slice(9);
  const sites = [];
  for (const line of lines) {
    if (line.startsWith("#")) continue;
    const fields = line.split("\t");
    if (fields.length < 8 || !/^\d+$/.test(fields[1])) continue;  // (8 columns: sites without samples)
    const alleles = [fields[3], ...fields[4].split(",")];
    const format = (fields[8] || "").split(":");
    const gtIndex = format.indexOf("GT");
    const calls = samples.map((name, index) => {
      const value = fields[index + 9] || ".";
      const gt = gtIndex < 0 ? "." : (value.split(":")[gtIndex] || ".");
      const numbers = gt.split(/[\/|]/);
      const missing = numbers.some((number) => number === "." || alleles[Number(number)] === undefined);
      const alternate = numbers.some((number) => /^\d+$/.test(number) && Number(number) > 0 && alleles[Number(number)] !== undefined);
      const bases = numbers.map((number) => alleles[Number(number)] ?? "·").join(gt.includes("|") ? "|" : "/");
      return { name, gt, bases, missing, alternate };
    });
    const info = fields[7].split(";");
    const repeat = info.find((value) => value.startsWith("RU="))?.slice(3) || null;
    const type = repeat ? "Repeat indel" : info.includes("TYPE=INDEL") ||
      alleles.slice(1).some((allele) => allele.length !== alleles[0].length) ? "Indel" :
      alleles.every((allele) => allele.length === 1) ? "SNP" : "MNV";
    sites.push({ line, position: Number(fields[1]), ref: fields[3], alt: fields[4], type, repeat, calls,
                 alternateCount: calls.filter((call) => call.alternate).length,
                 missingCount: calls.filter((call) => call.missing).length });
  }
  return { headers: lines.filter((line) => line.startsWith("#")), samples, sites };
}

function filterVariantSites(sites, { query = "", missing = "all", minAlternate = 0, type = "all" } = {}) {
  const term = query.trim().toLowerCase();
  return sites.filter((site) =>
    (type === "all" || site.type === type || type === "Indel" && site.type === "Repeat indel") &&
    (missing === "all" || (missing === "any" ? site.missingCount > 0 : site.missingCount === 0)) &&
    site.alternateCount >= minAlternate &&
    (!term || [site.position, site.ref, site.alt, ...site.calls.filter((call) => call.alternate).map((call) => call.name)]
      .some((value) => String(value).toLowerCase().includes(term))));
}

function filteredVariantVcf(parsed, sites) {
  return [...parsed.headers, ...sites.map((site) => site.line)].join("\n") + "\n";
}

function variantMatrixText(parsed, sites, sampleIndexes, separator = ",") {
  const cell = (value) => {
    const text = String(value ?? "");
    return separator === "," && /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const lines = [["Sample", ...sites.map((site) => `${site.position} ${site.ref}>${site.alt}`)].map(cell).join(separator)];
  for (const index of sampleIndexes) lines.push([parsed.samples[index], ...sites.map((site) => site.calls[index]?.gt || ".")]
    .map(cell).join(separator));
  return lines.join("\n") + "\n";
}
