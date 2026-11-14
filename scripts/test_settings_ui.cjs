// Settings positioning and isolated Trash scrolling in a private library.
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
    browser = await chromium.launch({executablePath: process.env.CLADIA_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true});
    const page = await browser.newPage({viewport: {width: 1450, height: 1000}}), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {
      localStorage.setItem('cladia.openFolders', 'null');
      localStorage.setItem('cladia.codes', '[]');
    });
    await page.route('**/api/settings/space', route => route.fulfill({json: {library: 1000,logs: 0,log_count: 0,jobs: 0,images:[{name:'Cladia',size:4000000000}],trash:100000,trash_count:200,history:0,transfers:0}}));
    // Layout assertions must not depend on which WSL tools this PC has installed.
    await page.route('**/api/tool-engines/storage', route => route.fulfill({json: {
      mafft: {kind:'linux',bytes:20000000},
      reference_annotation: {kind:'shared',bytes:758000000,sharedWith:'BLAST+'}
    }}));
    await page.route('**/api/trash', route => route.fulfill({json: Array.from({length:200}, (_,i)=>({id:String(i),label:'Deleted sequence '+i,kind:'sequence',deleted:1700000000+i,folder:'Results',size:1000}))}));
    await page.goto(base); await page.locator('#settings-button').click();
    assert.deepEqual(await page.evaluate(() => {
      const values = ['null', '[]', 'true', '7', '"text"', '{broken'];
      const recovered = values.map(value => {
        localStorage.setItem('cladia.invalid-fixture', value);
        return loadJSON('cladia.invalid-fixture');
      });
      localStorage.removeItem('cladia.invalid-fixture');
      return recovered;
    }), Array.from({length:6}, () => ({})), 'Malformed browser preferences recover to empty objects');
    const dialog = page.locator('#settings-dialog');
    await dialog.waitFor({state:'visible'});
    const top = (await dialog.boundingBox()).y;
    const toolbar = await page.locator('#toolbar').boundingBox();
    assert(Math.abs(top - toolbar.y - toolbar.height - 8) < 2, 'Settings opens under the toolbar');
    const generalHeight=(await dialog.boundingBox()).height;
    const panel=page.locator('#settings-dialog .tab-panel:visible');
    assert(await panel.evaluate(el=>el.scrollHeight<=el.clientHeight+2),'General fits without an unnecessary scrollbar');
    await page.locator('#settings-tabs [data-tab="ncbi"]').click();
    const shortHeight=(await dialog.boundingBox()).height;
    assert(shortHeight<generalHeight-30,'A short panel shrinks the Settings dialog');
    await panel.evaluate(el=>{const content=document.createElement('div');content.id='height-fixture';content.style.height='250px';el.append(content);});
    assert((await dialog.boundingBox()).height>shortHeight+240,'Asynchronous content can grow the dialog beyond its previous size');
    assert(await panel.evaluate(el=>el.scrollHeight<=el.clientHeight+2),'Content that fits the window does not scroll');
    await page.locator('#height-fixture').evaluate(el=>el.style.height='2000px');
    const capped=await dialog.boundingBox();
    assert(capped.y+capped.height<=1000-10,'Long panels are capped by the available window height');
    assert(capped.height>540,'Tall windows can use more than the old fixed 540px height');
    assert(await panel.evaluate(el=>el.scrollHeight>el.clientHeight+100),'Only overflowing content gets a scrollbar');
    const actions=await page.locator('#settings-dialog .actions').boundingBox();
    assert(actions.y>=capped.y && actions.y+actions.height<=capped.y+capped.height,'Actions stay visible while the panel scrolls');
    await page.locator('#height-fixture').evaluate(el=>el.remove());
    assert(Math.abs((await dialog.boundingBox()).height-shortHeight)<2,'Removing content shrinks the dialog again');
    await page.locator('#settings-tabs [data-tab="space"]').click();
    await page.locator('#trash-list tr').last().waitFor({state:'attached'});
    await page.waitForFunction(()=>Math.abs(document.querySelector('#settings-dialog').getBoundingClientRect().y-
      document.querySelector('#toolbar').getBoundingClientRect().bottom-8)<2);
    const dimensions = await page.locator('#trash-list').evaluate(el=>({h:el.clientHeight,total:el.scrollHeight}));
    for (const height of [1100,1000,900,1100]) {
      await page.setViewportSize({width:1450,height});
      await page.waitForFunction(height => {
        const panel=document.querySelector('#settings-dialog .tab-panel[data-tab="space"]');
        const trash=document.querySelector('#trash-list').clientHeight;
        return panel.scrollHeight<=panel.clientHeight+1 && (height===1100 ? trash===200 : trash<200);
      },height);
      const trashHeight=await page.locator('#trash-list').evaluate(el=>el.clientHeight);
      if(height===1100) assert.equal(trashHeight,200,'Trash regains its normal height when space returns');
      else assert(trashHeight<200,'Trash shrinks before Storage needs an outer scrollbar');
    }
    await page.setViewportSize({width:1450,height:1000});
    await page.waitForFunction(()=>document.querySelector('#trash-list').clientHeight<200);
    assert(dimensions.h <= 201 && dimensions.total > dimensions.h * 5);
    await page.locator('#trash-list').scrollIntoViewIfNeeded();
    const initial = await dialog.evaluate(el=>el.scrollTop);
    await page.locator('#trash-list').hover();
    await page.mouse.wheel(0,500);
    await page.waitForFunction(()=>document.querySelector('#trash-list').scrollTop>0);
    assert.equal(await dialog.evaluate(el=>el.scrollTop),initial,'Scrolling Trash does not scroll Settings');
    await page.locator('#trash-list').evaluate(el=>el.scrollTop=el.scrollHeight);
    await page.mouse.wheel(0,500);
    await page.waitForTimeout(200);
    assert.equal(await dialog.evaluate(el=>el.scrollTop),initial,'Trash does not chain scrolling into Settings at its end');
    await page.locator('#trash-list input').last().check();
    assert(await page.locator('#trash-restore').isEnabled());
    const artifacts=path.resolve('build/settings-qa');await fs.mkdir(artifacts,{recursive:true});
    await page.screenshot({path:path.join(artifacts,'storage-trash.png')});
    await page.locator('#settings-tabs [data-tab="tools"]').click();
    await page.locator('#tool-engines-list details').first().waitFor();
    assert.equal(await page.locator('#tool-engines-list details[open]').count(),0,'Tool groups start collapsed');
    await page.locator('#tool-engines-list details').evaluateAll(groups=>groups.forEach(group=>group.open=true));
    await page.locator('#tool-engines-list [data-engine="mafft"]').waitFor();
    assert(await page.locator('#tool-engines-list [data-engine]').count()>=14,'Built-in tools are listed');
    const row = id => page.locator(`#tool-engines-list [data-engine="${id}"]`);
    const groups = await page.locator('#tool-engines-list .tool-type-heading').allTextContents();
    assert.deepEqual(groups,['Annotate','Align','Build a tree','Map reads to a reference','Assemble','Variants','Primers','BLAST','Download']);
    const manage = id => row(id).getByRole('button',{name:/^Manage /});
    assert.equal(await row('mafft').locator('button').count(),1);
    assert.equal(await row('mafft').locator('small').count(),0,'Grid cards show only a name and Manage button');
    const boxes=await page.locator('#tool-engines-list .tool-card').evaluateAll(elements=>elements.slice(0,2).map(e=>({x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y})));
    assert(boxes[0].x!==boxes[1].x && Math.abs(boxes[0].y-boxes[1].y)<1,'Tools use a compact two-column grid');
    await manage('mafft').click();
    assert(/on this PC|About .* to install/.test(await page.locator('#tool-engine-description').textContent()));
    assert(await page.locator('#tool-engine-confirm').isHidden(),'Built-in details do not offer an unsupported install action');
    // Clicking inside or dragging a field gesture out must retain the modal.
    const inner=page.locator('#tool-engine-dialog');
    await inner.click({position:{x:8,y:8}});
    assert(await inner.isVisible());
    const innerBox=await inner.boundingBox();
    await page.mouse.move(innerBox.x+20,innerBox.y+20);await page.mouse.down();
    await page.mouse.move(10,10);await page.mouse.up();
    assert(await inner.isVisible(),'Dragging from inside to outside does not dismiss');
    await page.evaluate(()=>document.querySelector('#tool-engine-dialog').addEventListener('cancel',e=>e.preventDefault(),{once:true}));
    await page.mouse.click(10,10);
    assert(await inner.isVisible(),'Existing cancel guards are respected');
    await page.mouse.click(10,10);
    await inner.waitFor({state:'hidden'});
    assert(await dialog.isVisible(),'Backdrop dismisses only the topmost modal');
    await manage('reference_annotation').click();
    await page.waitForFunction(() => document.querySelector('#tool-engine-description').textContent.includes('counted once'));
    const shared=await page.locator('#tool-engine-description').textContent();
    assert(shared.includes('No additional tool files · uses BLAST+'));
    assert(shared.includes('counted once'),'Shared BLAST storage is explained separately from transfer storage');
    await page.locator('#tool-engine-dialog button[value="cancel"]').click();
    // Optional tools: Install (with packages), then Uninstall once installed
    await row('getorganelle').getByRole('button',{name:'Install GetOrganelle'}).click();
    assert((await page.locator('#tool-engine-description').textContent()).includes('Downloaded from bioconda'));
    assert(await page.locator('#tool-engine-confirm').isDisabled(),'A package must be chosen first');
    await page.locator('#tool-engine-packages input').first().check();
    assert(await page.locator('#tool-engine-confirm').isEnabled(),'Installation is available');
    await page.locator('#tool-engine-dialog button[value="cancel"]').click();
    await page.evaluate(()=>{toolEngineCatalog.find(e=>e.id==='clustal_omega').installed=true;
      toolEngineCatalog.find(e=>e.id==='clustal_omega').outdated=true;});
    await page.evaluate(()=>openToolEngineDetails('clustal_omega'));
    assert.equal(await page.locator('#tool-engine-confirm').textContent(),'Update','An outdated tool offers Update');
    let removed=false;
    await page.route('**/api/tool-engines/clustal_omega/uninstall', route=>{removed=true;return route.fulfill({json:{id:'uninstall'}});});
    page.once('dialog', d=>d.accept());
    await page.locator('#tool-engine-actions').getByRole('button',{name:'Uninstall',exact:true}).click();
    await page.locator('#tool-engine-dialog').waitFor({state:'hidden'});
    assert(removed,'Uninstall is available through Manage');
    await page.locator('#settings-dialog').evaluate(el=>el.scrollTop=0);
    await page.screenshot({path:path.join(artifacts,'tools-footprints.png')});
    await page.locator('#settings-tabs [data-tab="space"]').click();
    for(const size of [{width:1000,height:700},{width:800,height:600}]){
      await page.setViewportSize(size);
      await page.waitForFunction(()=>{
        const dialog=document.querySelector('#settings-dialog').getBoundingClientRect();
        const toolbar=document.querySelector('#toolbar').getBoundingClientRect();
        return Math.abs(dialog.y-toolbar.bottom-8)<2 && dialog.bottom<=innerHeight-10;
      });
      await page.locator('#settings-dialog button[value="cancel"]').click();
      await page.locator('#settings-button').click();
      await dialog.waitFor({state:'visible'});
      const r=await dialog.boundingBox();
      assert(r.y>=0 && r.y+r.height<=size.height, 'Settings stays within small windows');
      const trash=await page.locator('#trash-list').evaluate(el=>({h:el.clientHeight,total:el.scrollHeight}));
      assert(trash.h<=size.height*.24+1 && trash.total>trash.h);
    }
    await page.locator('#settings-tabs [data-tab="general"]').click();
    const savedCores=await page.evaluate(async()=>(await api('GET','/api/settings')).cores);
    await page.locator('#settings-dialog [name="cores"]').fill('3');
    await page.mouse.click(10,10);
    await dialog.waitFor({state:'hidden'});
    assert.equal(await page.evaluate(async()=>(await api('GET','/api/settings')).cores),savedCores,'Backdrop cancellation does not save Settings');
    await page.evaluate(()=>{
      const d=document.createElement('dialog');d.id='backdrop-fixture';d.innerHTML='<p>Dynamic menu</p>';
      d.returnValue='save';d.onclose=()=>{window.backdropResult=d.returnValue;d.remove();};
      document.body.append(d);d.showModal();
    });
    await page.mouse.click(10,10);
    await page.waitForFunction(()=>window.backdropResult==='');
    assert.deepEqual(errors,[]);console.log('Settings: content-sized panels, viewport cap, live resize, fixed top across tabs, capped Trash scrolling, no scroll chaining, selection, type-grouped tool grid, Manage actions, backdrop cancel/guards/dragging, shared footprints and small-window layout passed');
  } finally {
    if (browser) await browser.close(); server.kill();
    if (server.exitCode === null) await new Promise(r => server.once('exit', r));
    await fs.rm(scratch, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
