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
    const added = await (await fetch(base + '/api/library/upload', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: 'samples.fa', text: '>sampleA\nACGTACGT\n>sampleB\nACGTACGA\n' }) })).json();
    browser = await chromium.launch({ executablePath: process.env.CLADIA_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
    const page = await browser.newPage({ acceptDownloads: true, viewport: { width: 1200, height: 900 } });
    const errors = [];
    page.on('pageerror', e => { errors.push(e.message); console.error('Page error:', e.message); });
    await page.goto(base);
    await page.waitForFunction(id => items.some(i => i.id === id), added.ids[0]);
    await page.evaluate(id => { selected = [id]; openMetaImport(); }, added.ids[0]);
    await page.locator('#meta-input').setInputFiles({ name: 'ambiguous.csv', mimeType: 'text/csv', buffer: Buffer.from('id,host\nsampleA,x\nsampleA,y\n') });
    await page.locator('#meta-preview').filter({ hasText: 'More than one row matches' }).waitFor();
    assert(await page.locator('#meta-run').isDisabled());
    await page.locator('#meta-input').setInputFiles({ name: 'metadata.csv', mimeType: 'text/csv', buffer: Buffer.from('id,host\nsampleA,Alga\nsampleB,Seaweed\n') });
    await page.waitForFunction(() => !document.getElementById('meta-run').disabled);
    await page.locator('#meta-run').click();
    await page.waitForFunction(() => !document.getElementById('meta-dialog').open);
    await page.locator('#settings-button').click();
    await page.locator('#settings-tabs [data-tab="space"]').click();
    await page.locator('#project-history').check();
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#project-export').click();
    const download = await downloadPromise;
    const zip = path.join(scratch, 'Shared.zip');
    await download.saveAs(zip);
    await page.waitForFunction(() => !document.getElementById('project-choose').disabled);
    await page.locator('#project-file').setInputFiles(zip);
    await page.locator('#project-dialog[open]').waitFor();
    assert.match(await page.locator('#project-summary').textContent(), /1 items.*saved versions/);
    await page.locator('#project-name').fill('Shared project');
    await page.locator('#project-import').click();
    await page.waitForFunction(() => !document.getElementById('project-dialog').open);
    const all = await (await fetch(base + '/api/library')).json();
    assert.equal(all.filter(i => i.kind === 'list').length, 2);
    const imported = all.find(i => i.kind === 'sequence' && i.folder === 'Shared project' && i.name === 'sampleA');
    const metadata = await (await fetch(base + `/api/library/${imported.id}/metadata`)).json();
    assert.equal(metadata.host, 'Alga');
    await page.locator('#project-file').setInputFiles({ name: 'bad.zip', mimeType: 'application/zip', buffer: Buffer.from('bad archive') });
    await page.locator('#project-status').filter({ hasText: 'Invalid project archive' }).waitFor();
    assert.deepEqual(errors, []);
    console.log('Browser smoke passed: ambiguous metadata blocked; metadata saved; ZIP exported/imported with history; invalid ZIP reported; no page errors.');
  } finally {
    if (browser) await browser.close();
    server.kill();
    if (server.exitCode === null) await new Promise(resolve => server.once('exit', resolve));
    await fs.rm(scratch, { recursive: true, force: true });
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
