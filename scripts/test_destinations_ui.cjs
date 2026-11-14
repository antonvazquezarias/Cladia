// Real browser/export QA. Uses a private library/server, never the user's app.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises'), os = require('node:os'), path = require('node:path'), net = require('node:net');
const assert = require('node:assert/strict');
(async () => {
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'cladia-destinations-'));
  const artifacts = path.resolve(process.env.CLADIA_FIGURE_ARTIFACTS || 'build/destination-qa');
  await fs.mkdir(artifacts, { recursive: true });
  await fs.mkdir(path.join(scratch, 'data', 'library'), { recursive: true });
  await fs.mkdir(path.join(scratch, 'data', '.cladia'), { recursive: true });
  if (process.env.CLADIA_LONG_READ_DATA) await fs.cp(process.env.CLADIA_LONG_READ_DATA, path.join(scratch, "data"), { recursive: true });
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

    for(const name of ['Project A','Project B','References']) await api('/api/folders','POST',{name});
    async function upload(name,folder){return (await api('/api/library/upload','POST',{filename:name+'.fa',text:'>'+name+'\n'+'ACGT'.repeat(100)+'\n',folder})).ids[0];}
    const a=await upload('a','Project A'),a2=await upload('a2','Project A'),b=await upload('b','Project B'),ref=await upload('ref','References');
    browser=await chromium.launch({executablePath:process.env.CLADIA_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
    const page=await browser.newPage({viewport:{width:1450,height:1000}}),errors=[],requests=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/api/jobs/mafft',async route=>{requests.push(route.request().postDataJSON());await route.fulfill({json:{id:'qa',status:'queued'}});});
    await page.goto(base);await page.waitForFunction(()=>items.length>=4);
    const choice=()=>page.locator('#mafft-dialog .result-destination select');
    async function open(ids){await page.evaluate(ids=>{selected=ids;openTool('mafft');toolTab(document.querySelector('#mafft-dialog form'),'mafft');},ids);}
    await open([a,a2]);assert.equal(await choice().inputValue(),'Project A');
    // Picker changes refresh the inferred folder without changing manual choices.
    await page.evaluate(b=>document.querySelector('#mafft-dialog form')._pickers.sequences.set([b]),b);
    assert.equal(await choice().inputValue(),'Project B');
    await page.evaluate(ids=>document.querySelector('#mafft-dialog form')._pickers.sequences.set(ids),[a,b]);
    assert.equal(await choice().inputValue(),'');
    await page.locator('#mafft-dialog button[value=run]').click();assert.equal(requests.length,0);
    await choice().selectOption('Project A');
    await page.evaluate(ids=>document.querySelector('#mafft-dialog form')._pickers.sequences.set(ids),[b,a2]);
    assert.equal(await choice().inputValue(),'Project A');
    await page.screenshot({path:path.join(artifacts,'combined-destination.png')});
    await page.locator('#mafft-dialog button[value=run]').click();await page.waitForFunction(()=>!document.querySelector('#mafft-dialog').open);
    assert.equal(requests.at(-1).folder,'Project A');
    await open([a]);
    await page.evaluate(ref=>{const f=document.querySelector('#mafft-dialog form');toolTab(f,'add');f._pickers.add_to.set([ref]);},ref);
    assert.equal(await choice().inputValue(),'Project A','reference folder must not override the query');
    await choice().selectOption('/');await page.locator('#mafft-dialog button[value=run]').click();
    await page.waitForFunction(()=>!document.querySelector('#mafft-dialog').open);
    assert.equal(requests.at(-1).folder,null);assert.equal(requests.at(-1).add_to,ref);
    for(const name of ['assemble','primers','map']) {
      await page.evaluate(({name,ids})=>{selected=ids;setFolder('Project A');openTool(name);if(name==='primers')toolTab(document.querySelector('#primers-dialog form'),'test');},{name,ids:[a2,b]});
      const result=page.locator(`#${name}-dialog .result-destination select`);
      assert.equal(await result.inputValue(),name==='map'?'Project A':'');
      await result.selectOption('Project B');
      await page.evaluate(name=>document.querySelector(`#${name}-dialog`).close(),name);
    }
    // Grouping mixed-folder sequences requires the same explicit destination.
    await page.evaluate(ids=>{window.grouping=groupSelected(ids,'Combined',false);},[a,b]);
    const dialog=page.getByRole('dialog').filter({has:page.getByRole('heading',{name:'Result destination',exact:true})});
    await dialog.waitFor();await dialog.getByLabel('Save result in').selectOption('Project B');
    await dialog.getByRole('button',{name:'Continue',exact:true}).click();await page.evaluate(()=>window.grouping);
    const grouped=await page.evaluate(()=>items.find(it=>it.name==='Combined'));
    assert.equal(grouped.folder,'Project B');
    assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,requests:requests.length,artifacts,errors}));
  } finally {
    if (browser) await browser.close(); server.kill();
    if (server.exitCode === null) await new Promise(r => server.once('exit', r));
    await fs.rm(scratch, { recursive: true, force: true });
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
