// Generated-tool regression smoke: real dialogs, registry submissions and native MAFFT.
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
      const response = await fetch(base + route, { method, headers: { 'Content-Type': 'application/json' }, body: data ? JSON.stringify(data) : undefined });
      assert(response.ok, `${route}: ${response.status} ${await response.clone().text()}`);
      return response.json();
    };
    await api('/api/folders', 'POST', {name: 'Results'});
    const ids = [];
    for (const name of ['alpha', 'beta']) {
      const text = `>${name}\nATG${'GCT'.repeat(130)}TAA\n`;
      ids.push((await api('/api/library/upload', 'POST', {filename: name + '.fa', text, folder: 'Results'})).ids[0]);
    }
    browser = await chromium.launch({executablePath: process.env.CLADIA_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true});
    const page = await browser.newPage({viewport: {width: 1450, height: 1000}});
    const errors = [], submitted = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', req => { if (req.method() === 'POST' && req.url().endsWith('/api/tool-registry/run')) submitted.push(req.postDataJSON()); });
    await page.goto(base);
    await page.waitForFunction(() => items.length >= 2 && document.querySelector('[data-tool=align]'));
    async function select(selection) {
      await page.evaluate(selection => { selected = selection; scheduleToolStates(); }, selection);
    }
    async function submit(category, operation, selection) {
      await select(selection);
      await page.locator(`#toolbar [data-tool=${category}]`).click();
      const dialog = page.locator(`#${category}-registered-dialog`);
      await dialog.waitFor({state: 'visible'});
      const label = operation === 'align.new' ? 'New alignment' : 'Find ORFs';
      await dialog.getByRole('button', {name: label, exact: true}).first().click();
      if (operation === 'annotate.orfs') await dialog.locator('[name=save_copy]').check();
      const destination = dialog.locator('.result-destination select');
      if (await destination.count()) await destination.selectOption('Results');
      const responsePromise = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/api/tool-registry/run'));
      await dialog.locator('.actions button[value=run]').click();
      const response = await responsePromise;
      assert(response.ok(), await response.text());
      const result = await response.json(), jobs = Array.isArray(result) ? result : [result];
      await dialog.waitFor({state: 'hidden'});
      const outputs = [];
      for (const job of jobs) {
        let completed;
        for (let i = 0; i < 300; i++) {
          completed = await api('/api/logs/' + job.id);
          if (['done', 'failed', 'cancelled'].includes(completed.status)) break;
          await new Promise(r => setTimeout(r, 200));
        }
        assert.equal(completed.status, 'done', JSON.stringify(completed));
        assert(completed.outputs.length);
        assert.match(completed.log, /Run details/);
        outputs.push(...completed.outputs);
      }
      assert.equal(submitted.at(-1).operation, operation);
      return outputs;
    }
    const aligned = await submit('align', 'align.new', ids);
    await submit('annotate', 'annotate.orfs', [ids[0]]);
    const registry = await api('/api/tool-registry');
    await page.evaluate(async () => { await refreshShown(); });
    for (const category of registry.categories) {
      await select(category.id === 'tree' ? [aligned[0].id] : ids);
      await page.locator(`#toolbar [data-tool=${category.id}]`).click();
      const dialog = page.locator(`#${category.id}-registered-dialog`);
      await dialog.waitFor({state: 'visible'});
      assert(await dialog.locator('h3').textContent());
      await dialog.locator('.actions button[value=cancel]').click();
      await dialog.waitFor({state: 'hidden'});
    }
    const library = await api('/api/library');
    const entries = Array.isArray(library) ? library : library.items;
    for (const output of aligned) assert.equal(entries.find(i => i.id === output.id).folder, 'Results');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({passed: true, dialogs: registry.categories.length, submissions: submitted.map(s => s.operation), errors}));
  } finally {
    if (browser) await browser.close();
    server.kill();
    if (server.exitCode === null) await new Promise(r => server.once('exit', r));
    await fs.rm(scratch, { recursive: true, force: true });
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
