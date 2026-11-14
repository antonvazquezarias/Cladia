// Local/Online BLAST tabs, option persistence and dispatch without live BLAST requests.
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
    { cwd: process.env.CLADIA_APP_ROOT || path.resolve(__dirname, '..'), env: { ...process.env, CLADIA_DATA: path.join(scratch, 'data') }, windowsHide: true, stdio: 'pipe' });
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
    browser = await chromium.launch({executablePath: process.env.CLADIA_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true});
    const page = await browser.newPage({viewport: {width: 1450, height: 1000}}), errors = [];
    page.on('pageerror', e => errors.push(e.message));

    const upload=async(name,text)=>{
      const response=await fetch(base+'/api/library/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:name+'.fa',text:'>'+name+'\n'+text+'\n'})});
      assert(response.ok,await response.clone().text());return (await response.json()).ids[0];
    };
    const dna=await upload('dna','ACGT'.repeat(50)),protein=await upload('protein','MELWVKPQFSHDE'.repeat(10));
    let emptyLocal=false;const submitted=[];
    await page.route('**/api/tool-registry',async route=>{
      const response=await route.fetch(),registry=await response.json();
      const db=registry.engines.find(e=>e.id==='local_blast').options.find(o=>o.key==='database');
      db.choices=emptyLocal?[{value:'',label:'No local databases; add one in Settings → BLAST databases'}]:[
        {value:'local:dna-fixture',label:'DNA fixture',when:{key:'program',values:['megablast','dc-megablast','blastn','tblastn']}},
        {value:'local:protein-fixture',label:'Protein fixture',when:{key:'program',values:['blastx','blastp']}}];
      db.default=db.choices[0].value;
      await route.fulfill({json:registry});
    });
    await page.route('**/api/tool-registry/run',route=>{submitted.push(route.request().postDataJSON());return route.fulfill({json:{id:'ui-only-fixture'}});});
    await page.goto(base);await page.waitForFunction(()=>items.length===2);
    await page.evaluate(dna=>{selected=[dna];return openTool('blast');},dna);
    const dialog=page.locator('#blast-registered-dialog'),local=dialog.getByRole('tab',{name:'Local',exact:true}),online=dialog.getByRole('tab',{name:'Online',exact:true});
    const program=dialog.locator('[name=program]'),database=dialog.locator('[name=database]'),run=dialog.locator('button[value=run]');
    assert.equal(await dialog.locator('select[name=engine]').count(),0,'Location is not buried in an Engine dropdown');
    assert.equal(await online.getAttribute('aria-selected'),'true','Existing online default is preserved');
    assert((await online.boundingBox()).y<(await program.boundingBox()).y,'Location is chosen before the program');
    await program.selectOption('blastx');await dialog.locator('[name=entrez]').fill('Rhodophyta[organism]');await dialog.locator('[name=max_hits]').selectOption('100');
    await local.click();await program.selectOption('blastn');await dialog.locator('[name=max_hits]').selectOption('20');
    assert.equal(await database.inputValue(),'local:dna-fixture');assert.equal(await dialog.locator('[name=entrez]').count(),0);
    await online.click();assert.equal(await program.inputValue(),'blastx');assert.equal(await dialog.locator('[name=entrez]').inputValue(),'Rhodophyta[organism]');
    assert.equal(await dialog.locator('[name=max_hits]').inputValue(),'100');
    await online.focus();await page.keyboard.press('Home');
    assert.equal(await local.getAttribute('aria-selected'),'true');assert.equal(await program.inputValue(),'blastn');
    assert.deepEqual(await page.evaluate(()=>document.querySelector('#blast-registered-dialog form')._pickers.queries.ids()),[dna]);
    const artifacts=path.resolve('build/blast-location-qa');await fs.mkdir(artifacts,{recursive:true});await page.screenshot({path:path.join(artifacts,'local.png')});
    await run.click();await dialog.waitFor({state:'hidden'});
    assert.equal(submitted[0].engine,'local_blast');assert.equal(submitted[0].options.database,'local:dna-fixture');
    assert(!Object.hasOwn(submitted[0].options,'entrez'),'Online-only settings never leak into a local request');
    await page.evaluate(()=>openTool('blast'));assert.equal(await local.getAttribute('aria-selected'),'true');
    await online.click();await page.screenshot({path:path.join(artifacts,'online.png')});
    await run.click();await dialog.waitFor({state:'hidden'});
    assert.equal(submitted[1].engine,'ncbi_blast');assert.equal(submitted[1].options.database,'nr');assert.equal(submitted[1].options.entrez,'Rhodophyta[organism]');
    await page.evaluate(()=>openTool('blast'));
    await page.evaluate(protein=>document.querySelector('#blast-registered-dialog form')._pickers.queries.set([protein]),protein);
    assert.equal(await program.inputValue(),'blastp');await local.click();assert.equal(await program.inputValue(),'blastp');assert.equal(await database.inputValue(),'local:protein-fixture');
    await page.keyboard.press('End');assert.equal(await online.getAttribute('aria-selected'),'true');
    await dialog.locator('button[value=cancel]').click();
    emptyLocal=true;await page.evaluate(()=>openTool('blast'));await local.click();
    assert(await run.isDisabled(),'Local runs require an installed database');await online.click();assert(await run.isEnabled(),'Online search stays available without local databases');
    await dialog.locator('button[value=cancel]').click();assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:['prominent location tabs','keyboard navigation','per-engine option memory','shared query preservation','local/online request dispatch','protein compatibility','empty local database state'],errors}));
  } finally {
    if (browser) await browser.close(); server.kill();
    if (server.exitCode === null) await new Promise(r => server.once('exit', r));
    await fs.rm(scratch, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
