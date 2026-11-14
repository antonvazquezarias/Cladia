// Full-size sample × variant view for the VCF attached to an alignment.
// Paging bounds DOM work while CSV export includes every filtered site/sample.
const variantMatrix = { data: null, parsed: null, sites: [], sitePage: 0, samplePage: 0, key: "" };
const VARIANT_SITES_PAGE = 80, VARIANT_SAMPLES_PAGE = 100;

function updateVariantMatrixView() {
  const data = seqShown?.variantData;
  if (!data) return;
  if (variantMatrix.data !== data) {
    variantMatrix.data = data;
    variantMatrix.parsed = seqShown.parsedVariants || parseVariantVcf(data.vcf);
    variantMatrix.sitePage = variantMatrix.samplePage = 0;
    variantMatrix.key = "";
  }
  const query = $("variant-matrix-filter").value;
  const sampleQuery = $("variant-matrix-sample").value.trim().toLowerCase();
  const type = $("variant-matrix-type").value;
  const key = JSON.stringify([query, sampleQuery, type, variantMatrix.sitePage, variantMatrix.samplePage, data.stale]);
  if (key === variantMatrix.key) return;
  variantMatrix.key = key;
  const sites = variantMatrix.sites = filterVariantSites(variantMatrix.parsed.sites, { query, type });
  const samples = variantMatrix.parsed.samples;
  const sampleIndexes = variantMatrix.sampleIndexes = samples.map((_, i) => i)
    .filter((i) => samples[i].toLowerCase().includes(sampleQuery));
  const sitePages = Math.max(1, Math.ceil(sites.length / VARIANT_SITES_PAGE));
  const samplePages = Math.max(1, Math.ceil(sampleIndexes.length / VARIANT_SAMPLES_PAGE));
  variantMatrix.sitePage = Math.min(variantMatrix.sitePage, sitePages - 1);
  variantMatrix.samplePage = Math.min(variantMatrix.samplePage, samplePages - 1);
  const siteStart = variantMatrix.sitePage * VARIANT_SITES_PAGE;
  const sampleStart = variantMatrix.samplePage * VARIANT_SAMPLES_PAGE;
  const shownSites = sites.slice(siteStart, siteStart + VARIANT_SITES_PAGE);
  const shownSamples = sampleIndexes.slice(sampleStart, sampleStart + VARIANT_SAMPLES_PAGE);
  $("variant-matrix-title").textContent = `Sample × variant matrix · ${sampleIndexes.length} of ${samples.length} samples · ${sites.length} sites${data.stale ? " · calls are stale" : ""}`;
  $("variant-matrix-sites-page").textContent = `${sites.length ? siteStart + 1 : 0}–${Math.min(siteStart + VARIANT_SITES_PAGE, sites.length)} of ${sites.length}`;
  $("variant-matrix-samples-page").textContent = `${sampleIndexes.length ? sampleStart + 1 : 0}–${Math.min(sampleStart + VARIANT_SAMPLES_PAGE, sampleIndexes.length)} of ${sampleIndexes.length}`;
  $("variant-matrix-prev-sites").disabled = !variantMatrix.sitePage;
  $("variant-matrix-next-sites").disabled = variantMatrix.sitePage >= sitePages - 1;
  $("variant-matrix-prev-samples").disabled = !variantMatrix.samplePage;
  $("variant-matrix-next-samples").disabled = variantMatrix.samplePage >= samplePages - 1;
  $("variant-matrix-copy").disabled = !sites.length || !sampleIndexes.length;
  $("variant-matrix-csv").disabled = !sites.length || !sampleIndexes.length;
  const html = ['<table><thead><tr><th class="name">Sample</th>'];
  shownSites.forEach((site, j) => html.push(`<th title="${esc(`${site.type}: ${site.position} ${site.ref} → ${site.alt}`)}" data-site="${siteStart + j}">${site.position}<small>${esc(site.ref)}→${esc(site.alt)}</small></th>`));
  html.push('</tr></thead><tbody>');
  shownSamples.forEach((sampleIndex) => {
    const sample = samples[sampleIndex];
    const alternate = sites.reduce((count, site) => count + Number(!!site.calls[sampleIndex]?.alternate), 0);
    const missing = sites.reduce((count, site) => count + Number(!!site.calls[sampleIndex]?.missing), 0);
    html.push(`<tr><th class="name" title="${alternate} alternate sites · ${missing} missing calls">${esc(sample)}<small>${alternate} alt · ${missing} missing</small></th>`);
    shownSites.forEach((site, j) => {
      const call = site.calls[sampleIndex];
      const kind = call?.missing ? "missing" : call?.alternate ? "alt" : "ref";
      html.push(`<td class="${kind}" data-site="${siteStart + j}" data-sample="${sampleIndex}" title="${esc(`${sample}: ${call?.bases || "·"} (${call?.gt || "."}) at ${site.position} ${site.ref} → ${site.alt}`)}">${esc(call?.gt || ".")}</td>`);
    });
    html.push('</tr>');
  });
  html.push('</tbody></table>');
  $("variant-matrix-body").innerHTML = html.join("");
}

$("variant-matrix-body").addEventListener("click", (event) => {
  const cell = event.target.closest("[data-site]");
  if (!cell || seqShown?.variantData?.stale) return;
  const site = variantMatrix.sites[Number(cell.dataset.site)];
  const column = seqView?.variantBaseColumns?.[site?.position - 1];
  if (column === undefined) return;
  const sample = variantMatrix.parsed.samples[Number(cell.dataset.sample)];
  const row = Math.max(0, seqView.rows.findIndex((entry) => entry.name === sample));
  if (sideState.max) { sideState.max = false; renderSidePanel(); }  // the alignment back in view beside the panel
  seqView.select(column, column, row, row);
  seqView.centerOn(column);
});
for (const id of ["variant-matrix-filter", "variant-matrix-sample", "variant-matrix-type"]) $(id).addEventListener(id.endsWith("type") ? "change" : "input", () => {
  variantMatrix.sitePage = variantMatrix.samplePage = 0;
  variantMatrix.key = "";
  updateVariantMatrixView();
});
for (const [id, field, delta] of [
  ["variant-matrix-prev-sites", "sitePage", -1], ["variant-matrix-next-sites", "sitePage", 1],
  ["variant-matrix-prev-samples", "samplePage", -1], ["variant-matrix-next-samples", "samplePage", 1],
]) $(id).onclick = () => { variantMatrix[field] += delta; updateVariantMatrixView(); };
$("variant-matrix-copy").onclick = async () => {
  const sites = variantMatrix.sites.slice(variantMatrix.sitePage * VARIANT_SITES_PAGE, (variantMatrix.sitePage + 1) * VARIANT_SITES_PAGE);
  const sampleIndexes = variantMatrix.sampleIndexes
    .slice(variantMatrix.samplePage * VARIANT_SAMPLES_PAGE, (variantMatrix.samplePage + 1) * VARIANT_SAMPLES_PAGE);
  try { await navigator.clipboard.writeText(variantMatrixText(variantMatrix.parsed, sites, sampleIndexes, "\t")); }
  catch (e) { return toast("Couldn't copy variant matrix", true); }
  toast("Visible matrix page copied");
};
$("variant-matrix-csv").onclick = () => {
  const name = (seqShown?.alignment?.name || "alignment").replace(/[^\w.-]+/g, "_");
  saveTextFile(`${name}_variant_matrix.csv`, variantMatrixText(variantMatrix.parsed, variantMatrix.sites, variantMatrix.sampleIndexes), "text/csv");
};
