// Real browser/export QA. Uses a private library/server, never the user's app.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises'), os = require('node:os'), path = require('node:path'), net = require('node:net');
const assert = require('node:assert/strict');
(async () => {
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'cladia-genome-'));
  const artifacts = path.resolve(process.env.CLADIA_FIGURE_ARTIFACTS || 'build/genome-figure-qa');
  await fs.mkdir(artifacts, { recursive: true });
  await fs.mkdir(path.join(scratch, 'data', 'library'), { recursive: true });
  await fs.mkdir(path.join(scratch, 'data', '.cladia'), { recursive: true });
  const listener = net.createServer(); await new Promise(r => listener.listen(0, '127.0.0.1', r));
  const port = listener.address().port; await new Promise(r => listener.close(r));
  const base = `http://127.0.0.1:${port}`;
  const server = spawn(process.env.CLADIA_PYTHON || 'python', ['-m', 'uvicorn', 'cladia.server:app', '--host', '127.0.0.1', '--port', String(port)],
    { cwd: path.resolve(__dirname, '..'), env: { ...process.env, CLADIA_DATA: path.join(scratch, 'data') }, windowsHide: true, stdio: 'pipe' });
  let logs = '', browser;
  server.stdout.on('data', d => logs += d); server.stderr.on('data', d => logs += d);
  const api = async (route, method = 'GET', data) => {
    const response = await fetch(base + route, { method, headers: { 'Content-Type': 'application/json' }, body: data ? JSON.stringify(data) : undefined });
    assert(response.ok, `${route}: ${response.status} ${await response.clone().text()}`); return response.json();
  };
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      try { if ((await fetch(base + '/api/library')).ok) { ready = true; break; } } catch {}
      await new Promise(r => setTimeout(r, 100));
    }
    assert(ready, logs);
    const mito = (await api('/api/library/upload', 'POST', { filename: 'synthetic_mt.gb', text: await fs.readFile('tests/data/synthetic_mt.gb', 'utf8') })).ids[0];
    const plastid = (await api('/api/library/upload', 'POST', { filename: 'plastid.fa', text: '>Synthetic_plastid\n' + 'ACGT'.repeat(37500) })).ids[0];
    const data = await api(`/api/sequences/${plastid}`);
    const names = ['psaA', 'psbA', 'petB', 'atpA', 'ndhA', 'rps12', 'rpl2', 'rpoA', 'rbcL', 'trnM', 'rrn23', 'ycf1'];
    Object.assign(data, { circular: true, metadata: { organism: 'Porphyra umbilicalis' }, features: Array.from({ length: 300 }, (_, i) => ({
      type: i % 12 === 9 ? 'tRNA' : i % 12 === 10 ? 'rRNA' : 'CDS', name: names[i % 12], start: i * 500, end: i * 500 + 350, strand: i % 3 ? 1 : -1,
      ...(i === 6 ? { locations: [{ start: i * 500, end: i * 500 + 100 }, { start: i * 500 + 200, end: i * 500 + 350 }] } : {}),
    })) });
    data.features.push({ type: 'CDS', name: 'nad5_wrap', start: 149700, end: 180, strand: -1, locations: [{ start: 149700, end: 149999 }, { start: 0, end: 180 }] });
    await api(`/api/sequences/${plastid}`, 'PUT', data);
    const realPlastid = process.env.CLADIA_PLASTID_FIXTURE
      ? (await api('/api/library/upload', 'POST', { filename: 'plastid.gb', text: await fs.readFile(process.env.CLADIA_PLASTID_FIXTURE, 'utf8') })).ids[0] : null;
    browser = await chromium.launch({ executablePath: process.env.CLADIA_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
    const page = await browser.newPage({ viewport: { width: 1450, height: 1100 }, acceptDownloads: true });
    const errors = []; page.on('pageerror', e => { errors.push(e.message); console.error('Page error:', e.message); });
    await page.goto(base); await page.waitForFunction(() => items.length >= 2);
    async function open(id) {
      await page.evaluate(id => { selected = [id]; showSelection(); }, id);
      await page.locator('#view-figure:not([hidden])').waitFor();  // Figure ▾: its first figure, or a new one
      const fid = await page.evaluate(async id => {
        const made = figuresOf(itemById(id));
        if (made.length) { openItem(made[0].id); return made[0].id; }
        await newFigure(itemById(id)); return selected[0];
      }, id);
      await page.waitForFunction(fid => figureEditor?.itemId === fid && !!figureEditor?.source?.sequence, fid);
      await page.waitForFunction(() => !!document.querySelector('#figureview .fig-panel select'));
    }
    await open(mito);
    assert.equal(await page.evaluate(() => figureEditor.settings.shape), (await api(`/api/sequences/${mito}`)).circular ? 'circular' : 'linear');
    await page.getByLabel('Layout', { exact: true }).selectOption('circular');
    // One feature's overrides, Undo/Redo and autosave use the ordinary editor.
    const before = await page.locator('#figureview [data-role="feature"]').count();
    await page.locator('#figureview [data-role="feature"] path[data-part]').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    await page.getByRole('button', { name: 'Hide this gene', exact: true }).click();
    assert.equal(await page.locator('#figureview [data-role="feature"]').count(), before - 1);
    await page.locator('#figure-controls [data-act="undo"]').click();
    assert.equal(await page.locator('#figureview [data-role="feature"]').count(), before);
    await page.locator('#figureview [data-role="feature"] path[data-part]').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    await page.locator('#figure-controls [data-act="selection"]').click();
    const selectedVisible = await page.evaluate(() => {
      const c = figureEditor.page.parentElement.getBoundingClientRect();
      return figureEditor.selectionElements().every(el => {
        const r = el.getBoundingClientRect();
        return r.left >= c.left && r.right <= c.right && r.top >= c.top && r.bottom <= c.bottom;
      });
    });
    assert(selectedVisible, 'Fit selection includes genome feature geometry and its label');
    await page.locator('#figure-controls [data-act="fit"]').click();
    await page.getByLabel('GC content track', { exact: true }).check();
    await page.evaluate(() => figureEditor.flush());
    async function exports(prefix, all = false) {
      for (const format of all ? ['svg', 'pdf', 'png', 'tiff'] : ['svg', 'pdf']) {
        const download = page.waitForEvent('download');
        await page.evaluate(format => { figureEditor.bar.querySelector('.fig-dpi').value = '300'; return figureEditor.export(format); }, format);
        await (await download).saveAs(path.join(artifacts, `${prefix}.${format === 'tiff' ? 'tif' : format}`));
      }
    }
    await exports('mitochondrion-circular', true);
    await page.screenshot({ path: path.join(artifacts, 'editor-circular.png') });
    await page.getByLabel('Layout', { exact: true }).selectOption('linear');
    await page.evaluate(() => figureEditor.flush());
    await exports('mitochondrion-linear', true);
    await open(mito);  // the figure again, from the genome's Figure ▾
    await page.waitForFunction(() => figureEditor.settings.shape === 'linear');
    await page.getByLabel('GC content track', { exact: true }).uncheck();
    await page.evaluate(() => figureEditor.flush());
    await page.reload(); await page.waitForFunction(() => items.length >= 2); await open(mito);
    assert.equal(await page.evaluate(() => figureEditor.settings.gc.show), false); // false must survive defaults merging
    await open(plastid);
    await page.evaluate(() => figureEditor.change(s => { s.gc.show = true; s.regions = [
      { id: 'lsc', name: 'LSC', start: 1, end: 85000, color: '#9c6680' }, { id: 'ira', name: 'IRa', start: 85001, end: 110000, color: '#597e9c' },
      { id: 'ssc', name: 'SSC', start: 110001, end: 125000, color: '#9c6680' }, { id: 'irb', name: 'IRb', start: 125001, end: 150000, color: '#597e9c' } ]; }));
    await exports('plastid-circular', true);
    // Fit includes height; repeated zoom-out can always go below whole-page fit.
    await page.setViewportSize({width:1100,height:700});
    await page.locator('#figure-controls [data-act="fit"]').click();
    const fitted = await page.evaluate(() => {
      const canvas = document.querySelector('#figureview .fig-canvas'), svg = figureEditor.page.firstElementChild;
      return {width:svg.getBoundingClientRect().width,height:svg.getBoundingClientRect().height,
        roomW:canvas.clientWidth-40,roomH:canvas.clientHeight-40};
    });
    assert(fitted.width <= fitted.roomW + 1 && fitted.height <= fitted.roomH + 1);
    for (let i=0;i<12;i++) await page.locator('#figure-controls [data-act="out"]').click();
    assert(await page.evaluate(()=>figureEditor.zoom < figureEditor.fitScale()));
    await page.locator('#figure-controls [data-act="fit"]').click();
    await page.screenshot({path:path.join(artifacts,'dense-fit-small-window.png')});
    await page.setViewportSize({width:1450,height:1000});
    const dense = await page.evaluate(() => ({ shown: figureEditor.size.namedFeatures, placed: figureEditor.size.placedLabels, omitted: figureEditor.size.omittedLabels }));
    assert.equal(dense.shown, 301); assert.equal(dense.placed + dense.omitted, 301);
    await page.getByLabel('Layout', { exact: true }).selectOption('linear');
    await page.getByLabel('From (bp, 1-based)', { exact: true }).fill('20000');
    await page.getByLabel('From (bp, 1-based)', { exact: true }).press('Tab');
    await page.getByLabel('To (bp, 0 = end)', { exact: true }).fill('50000');
    await page.getByLabel('To (bp, 0 = end)', { exact: true }).press('Tab');
    await exports('plastid-region-linear', true);
    let realStats = null;
    if (realPlastid) {
      await open(realPlastid);
      await page.getByLabel('GC content track', { exact: true }).check();
      await exports('annotated-plastid-circular', true);
      realStats = await page.evaluate(() => ({ shown: figureEditor.size.namedFeatures, placed: figureEditor.size.placedLabels, omitted: figureEditor.size.omittedLabels }));
      await page.getByRole('button', { name: '84 mm', exact: true }).click();
      await exports('annotated-plastid-84mm');
      await page.getByRole('button', { name: '174 mm', exact: true }).click();
      await page.getByLabel('Layout', { exact: true }).selectOption('linear');
      await exports('annotated-plastid-linear', true);
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, dense, realStats, artifacts, formats: ['svg', 'pdf', 'png', 'tiff'], pageErrors: errors }));
  } finally {
    if (browser) await browser.close(); server.kill();
    if (server.exitCode === null) await new Promise(r => server.once('exit', r));
    await fs.rm(scratch, { recursive: true, force: true });
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
