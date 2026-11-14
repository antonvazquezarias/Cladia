// Tree zoom anchoring against actual rendered label widths in a private library.
// Optional real-browser smoke check. Requires Playwright, Chrome, and CLADIA_PYTHON.
// Runs its own server and scratch library; never connects to an existing Cladia.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');

(async () => {
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'cladia-ui-'));
  await fs.mkdir(path.join(scratch, 'data', 'library'), { recursive: true });
  await fs.mkdir(path.join(scratch, 'data', '.cladia'), { recursive: true });
  const listener = net.createServer();
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  const server = spawn(process.env.CLADIA_PYTHON || 'python', ['-m', 'uvicorn', 'cladia.server:app', '--host', '127.0.0.1', '--port', String(port)],
    { cwd: path.resolve(__dirname, '..'), env: { ...process.env, CLADIA_DATA: path.join(scratch, 'data') }, windowsHide: true, stdio: 'pipe' });
  let log = '', browser;
  server.stdout.on('data', d => { log += d; });
  server.stderr.on('data', d => { log += d; });
  server.on('error', e => { log += e.message; });
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      try { if ((await fetch(base + '/api/library')).ok) { ready = true; break; } } catch {}
      await new Promise(r => setTimeout(r, 100));
    }
    assert(ready, log);
    const api = async (route, method = 'GET', data) => {
      const response = await fetch(base + route, {method, headers: {'Content-Type': 'application/json'}, body: data ? JSON.stringify(data) : undefined});
      assert(response.ok, await response.clone().text()); return response.json();
    };

    const upload = async (filename, text) => (await api('/api/library/upload', 'POST', {filename, text})).ids[0];
    const tree = await upload('zoom.nwk', '((Long_species_label_for_testing:1,Short:0.5):1,Outgroup:10);');
    const sequence = await upload('zoom.fa', '>sequence\n' + 'ACGT'.repeat(2500));
    const graph = await upload('zoom.gfa', 'H\tVN:Z:1.0\nS\tA\t' + 'ACGT'.repeat(300) + '\nS\tB\t' + 'ACGT'.repeat(300) + '\nL\tA\t+\tB\t+\t0M\n');
    browser = await chromium.launch({executablePath: process.env.CLADIA_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true});
    const page = await browser.newPage({viewport: {width: 1450, height: 1000}}), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base); await page.waitForFunction(() => items.length >= 3);
    const open = async id => page.evaluate(async id => {selected = [id]; await showSelection();}, id);
    const click = (id, action) => page.locator(`#${id}-controls [data-zoom="${action}"]`).click();
    await open(tree);
    await page.waitForFunction(() => !!treeView?.nodes);
    assert(await page.locator('#tree-controls [data-zoom="selection"]').isDisabled());
    const initial = await page.evaluate(() => ({s: treeView.spacing, x: treeView.xScale}));
    await click('tree', 'branches-in');
    assert(await page.evaluate(x => treeView.xScale > x, initial.x));
    await click('tree', 'branches-out');
    await click('tree', 'rows-in');
    assert(await page.evaluate(s => treeView.spacing > s, initial.s));
    await page.evaluate(() => treeView.select(1));
    await click('tree', 'selection');
    assert(await page.evaluate(() => treeView.rows.filter(n => treeView.inSelection(n)).every(n => treeView.labelRight(n) - treeView.scrollX <= treeView.areaWidth - 15)));
    await click('tree', 'whole');
    assert(await page.evaluate(() => treeView.contentHeight() <= treeView.areaHeight + 1 && treeView.contentWidth() <= treeView.areaWidth + 1));
    await page.evaluate(() => treeView.select(-1));
    assert(await page.locator('#tree-controls [data-zoom="selection"]').isDisabled());
    // Figure controls fit the selected clade including its tip labels.
    await page.evaluate(() => newFigure(treeShown.item));  // Figure ▾ > New figure
    await page.waitForFunction(() => figureEditor?.kind && !figureEditor.sel);
    assert(await page.locator('#figure-controls [data-act="selection"]').isDisabled());
    await page.locator('#figureview [data-tip]').first().dispatchEvent('click');
    await page.locator('#figure-controls [data-act="selection"]').click();
    assert(await page.evaluate(() => figureEditor.zoom > figureEditor.fitScale()));
    const figBounds = await page.evaluate(() => {
      const c = figureEditor.page.parentElement.getBoundingClientRect();
      return figureEditor.selectionElements().map(e => {const r = e.getBoundingClientRect(); return r.left >= c.left && r.right <= c.right && r.top >= c.top && r.bottom <= c.bottom;});
    });
    assert(figBounds.every(Boolean), 'Selected figure labels should be in view');
    await page.locator('#figure-controls [data-act="fit"]').click();
    assert(await page.evaluate(() => figureEditor.zoom === null));
    // Linear and circular sequence zoom retain the actual selection.
    await open(sequence);
    await page.waitForFunction(() => seqView?.length === 10000);
    await page.locator('[data-seqmap="linear"]').click();
    assert(await page.locator('#seq-controls [data-zoom="selection"]').isDisabled());
    await click('seq', 'scale-in');
    await page.waitForFunction(() => !seqView.navigation);
    assert(await page.evaluate(() => seqView.charWidth > seqView.minCharWidth()));
    await page.evaluate(() => seqView.select(3000, 3999, 0, 0));
    await click('seq', 'selection');
    await page.waitForFunction(() => !seqView.navigation);
    assert(await page.evaluate(() => seqView.selection.c0 === 3000 && 3000 * seqView.charWidth - seqView.scrollX >= 0 && 4000 * seqView.charWidth - seqView.scrollX <= seqView.seqWidth));
    await click('seq', 'whole');
    await page.waitForFunction(() => !seqView.navigation);
    assert(await page.evaluate(() => seqView.charWidth === seqView.minCharWidth()));
    await page.locator('[data-seqmap="circular"]').click();
    await click('seq', 'selection');
    await page.waitForFunction(() => !seqView.navigation);
    assert(await page.evaluate(() => seqView.circZoom > 1 && seqView.rotation === 3500));
    await click('seq', 'whole');
    await page.waitForFunction(() => !seqView.navigation);
    assert(await page.evaluate(() => seqView.circZoom === 1));
    await open(graph);
    await page.waitForFunction(() => graphView?.nodes.length === 2);
    assert(await page.locator('#graph-controls [data-zoom="selection"]').isDisabled());
    await click('graph', 'scale-in');
    assert(await page.evaluate(() => graphView.scale > graphView.minScale()));
    await page.evaluate(() => graphView.choose([graphView.nodes[0]]));
    await click('graph', 'selection');
    assert(await page.evaluate(() => graphView.scale > graphView.minScale()));
    await click('graph', 'whole');
    assert(await page.evaluate(() => graphView.scale === graphView.minScale()));
    await page.evaluate(async () => openGraphTrack(graphView.nodes[0]));
    assert(await page.locator('#graph-controls .viewer-zoom').isHidden(), 'A graph track uses only the sequence zoom controls');
    assert(await page.locator('#seq-controls .viewer-zoom').isVisible());
    await click('seq', 'selection');
    await page.waitForFunction(() => !seqView.navigation);
    // The zoom compartment remains reachable without horizontal overflow.
    await page.setViewportSize({width: 1000, height: 700});
    for (const id of [tree, sequence, graph]) {
      await open(id);
      const bounds = await page.locator('.groups:not([hidden]) .viewer-zoom').first().boundingBox();
      assert(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 1000);
    }
    const artifacts = path.resolve('build/viewer-zoom-qa'); await fs.mkdir(artifacts, {recursive: true});
    await page.screenshot({path: path.join(artifacts, 'graph-toolbar.png')});
    assert.deepEqual(errors, []);
    console.log('Viewer zoom toolbar: tree axes, selection and whole fits, figures, linear/circular sequences, graphs and narrow layout passed');
  } finally {
    if (browser) await browser.close(); server.kill();
    if (server.exitCode === null) await new Promise(r => server.once('exit', r));
    await fs.rm(scratch, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
