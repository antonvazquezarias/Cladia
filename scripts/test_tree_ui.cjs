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
    const name = 'Selected_species_with_a_long_tip_label_and_sample_identifier';
    const id = (await api('/api/library/upload', 'POST', {filename: 'zoom.nwk', text: `((${name}:1,Short:0.5):1,Outgroup:10);`})).ids[0];
    const {nodes} = await api('/api/tree/' + id);
    browser = await chromium.launch({executablePath: process.env.CLADIA_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true});
    const page = await browser.newPage({viewport: {width: 1450, height: 1000}}), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base); await page.waitForFunction(id => items.some(it => it.id === id), id).catch(e => { throw new Error(`${e.message}; page errors: ${errors.join("; ")}`); });
    await page.evaluate(async id => { selected = [id]; await showSelection(); }, id);
    await page.waitForFunction(id => treeShown?.item.id === id && !!treeView?.nodes, id);
    const canvas = page.locator('#treeview canvas');
    const centerError = index => page.evaluate(index => {
      const v = treeView, n = v.nodes[index], ctx = v.ctx;
      const rows = v.rows.filter(row => row === n || v.inSelection(row));
      ctx.save();
      const right = Math.max(...rows.map(row => {
        ctx.font = `${v.matches.includes(row) ? 'bold ' : ''}${Math.max(9, Math.min(13, v.spacing * .75))}px 'Segoe UI', system-ui, sans-serif`;
        const end = row.kids.length ? 16 + row.deepest * v.xScale - v.scrollX : v.xOf(row);
        const start = v.show.align ? v.alignX() : end + 5;
        return start + ctx.measureText(row.kids.length ? `${row.tips} tips` : row.name).width;
      })); ctx.restore();
      return Math.abs((v.xOf(n) + right) / 2 - v.areaWidth / 2);
    }, index);
    const position = await page.evaluate(() => ({x: treeView.xOf(treeView.nodes[1]), y: treeView.yOf(treeView.nodes[1])}));
    await canvas.click({position});
    assert.equal(await page.evaluate(() => treeView.selected), 1);
    const box = await canvas.boundingBox();
    await page.mouse.move(box.x + position.x, box.y + position.y);
    await page.keyboard.down('Control'); await page.keyboard.down('Shift');
    await page.mouse.wheel(0, -1200);
    await page.keyboard.up('Shift'); await page.keyboard.up('Control');
    await page.waitForFunction(() => treeView.xScale > treeView.fitXScale() * 8);
    assert(await centerError(1) < 1, 'Selected branch and labels should be visually centered');
    const artifacts = path.resolve('build/tree-zoom-qa'); await fs.mkdir(artifacts, {recursive: true});
    await page.screenshot({path: path.join(artifacts, 'selected-clade.png')});
    await page.locator('#tree-show').click();  // the display options are in Show ▾
    await page.locator('#tree-align').check();
    await page.evaluate(() => treeView.zoomBranches(1.1));
    assert(await centerError(1) < 1, 'Aligned labels should contribute their actual positions');
    await page.locator('#tree-align').uncheck();
    await page.locator('#tree-show').click();
    await page.evaluate(nodes => {treeView.setTree(nodes); treeView.toggleCollapse(1); treeView.select(1); treeView.zoomBranches(8);}, nodes);
    assert(await centerError(1) < 1, 'Collapsed clades should include their summary label');
    await page.evaluate(nodes => {treeView.setTree(nodes); treeView.xScale *= 8; treeView.scrollX = 16 + treeView.nodes[2].depth * treeView.xScale + 20; treeView.clampScroll(); treeView.draw();}, nodes);
    const y = await page.evaluate(() => treeView.yOf(treeView.nodes[2]));
    await canvas.click({position: {x: 20, y}});
    assert.equal(await page.evaluate(() => treeView.selected), 2);
    await page.evaluate(() => treeView.zoomBranches(1.1));
    assert(await centerError(2) < 1, 'Clicking a visible label retains focus when its endpoint was offscreen');
    await page.evaluate(nodes => treeView.setTree(nodes), nodes);
    await page.locator('#tree-search').fill(name);
    await page.waitForFunction(() => treeView.matches.length === 1);
    await page.evaluate(() => treeView.zoomBranches(8));
    assert(await centerError(2) < 1, 'Find bold labels should be centered using their rendered width');
    await page.screenshot({path: path.join(artifacts, 'find-label.png')});
    await page.context().grantPermissions(['clipboard-read','clipboard-write']);
    await page.locator('#tree-search').fill('');
    await page.evaluate(nodes=>{treeView.setTree(nodes);treeView.fitWhole();},nodes);
    const tipPoint=index=>page.evaluate(index=>{const v=treeView,n=v.nodes[index];return {x:v.labelX(n)-v.scrollX+5,y:v.yOf(n)};},index);
    await canvas.click({position:await tipPoint(2)});
    await canvas.click({position:await tipPoint(4),modifiers:['Control']});
    assert.deepEqual(await page.evaluate(()=>treeView.selectedTipNames()),[name,'Outgroup']);
    await canvas.click({position:await tipPoint(2),button:'right'});
    assert.equal(await page.evaluate(()=>treeView.selectedTipNames().length),2);
    await page.getByText('Copy the 2 selected tip names',{exact:true}).click();
    assert.equal(await page.evaluate(async()=>(await navigator.clipboard.readText()).replace(/\r\n/g,"\n")),name+'\nOutgroup');
    await canvas.focus();await page.keyboard.press('Control+c');
    assert.equal(await page.evaluate(async()=>(await navigator.clipboard.readText()).replace(/\r\n/g,"\n")),name+'\nOutgroup');
    await page.locator('#tree-controls [data-zoom="selection"]').click();
    assert(await page.evaluate(()=>treeView.rows.filter(n=>treeView.inSelection(n)).every(n=>treeView.labelRight(n)-treeView.scrollX<=treeView.areaWidth-15)));
    await page.locator('#tree-controls [data-zoom="whole"]').click();
    await canvas.click({position:await tipPoint(2)});
    await canvas.click({position:await tipPoint(4),modifiers:['Shift']});
    assert.equal(await page.evaluate(()=>treeView.selectedTipNames().length),3);
    await canvas.click({position:await tipPoint(3),modifiers:['Control']});
    assert.deepEqual(await page.evaluate(()=>treeView.selectedTipNames()),[name,'Outgroup']);
    await page.screenshot({path:path.join(artifacts,'multiple-tips.png')});
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>treeView.selected),-1);
    assert(await page.locator('#tree-controls [data-zoom="selection"]').isDisabled());
    const denseId=(await api('/api/library/upload','POST',{filename:'dense.nwk',text:'('+Array.from({length:200},(_,i)=>`${[20,80,160].includes(i)?'Find_hit_':'Tip_'}${i}:1`).join(',')+');'})).ids[0];
    await page.evaluate(async id=>{await loadList();selected=[id];showSelection();},denseId);
    await page.waitForFunction(id=>treeShown?.item.id===id,denseId);
    await page.evaluate(()=>treeView.fitWhole());
    await page.locator('#tree-search').fill('Find_hit');
    await page.waitForFunction(()=>treeView.matches.length===3 && treeView.spacing<9);
    const markPixels=()=>page.evaluate(()=>treeView.matches.map(n=>{
      const v=treeView,x=Math.round(v.xOf(n)+3),y=Math.round(v.yOf(n));
      const center=Array.from(v.ctx.getImageData(x,y,1,1).data);
      const pixels=v.ctx.getImageData(x-6,y-6,13,13).data;
      let orange=0;for(let i=0;i<pixels.length;i+=4)if(pixels[i]>180&&pixels[i+1]>70&&pixels[i+1]<180&&pixels[i+2]<100)orange++;
      return {center,orange};
    }));
    let pixels=await markPixels();
    assert(pixels.every(p=>p.orange>8),'Every hit remains marked when labels are hidden');
    assert(pixels[0].center.slice(0,3).every(c=>c>240),'Current hit has a distinct white-centered ring');
    assert(pixels[1].center[0]>180 && pixels[1].center[1]<180,'Other hits have solid orange dots');
    await page.locator('#tree-next').click();pixels=await markPixels();
    assert(pixels[1].center.slice(0,3).every(c=>c>240),'Next match moves the active ring');
    await page.screenshot({path:path.join(artifacts,'zoomed-out-find.png')});
    await page.locator('#tree-search').fill('');
    assert((await markPixels()).length===0,'Clearing search removes the markers');
    // A fresh browser context has no localStorage from the first window.
    assert.equal(await page.locator('#tree-search').getAttribute('autocomplete'), 'off');
    await page.locator('#tree-show').click();
    await page.locator('#tree-ladderize').check();
    await page.evaluate(() => treeLayoutSave);
    assert.equal((await api('/api/settings')).tree_layout.ladderize, true);
    const fresh = await browser.newContext();
    const reopened = await fresh.newPage();
    await reopened.goto(base);
    await reopened.waitForFunction(id => items.some(it => it.id === id), id);
    await reopened.evaluate(id => { selected = [id]; showSelection(); }, id);
    await reopened.waitForFunction(id => treeShown?.item.id === id && !!treeView?.nodes, id);
    assert(await reopened.locator('#tree-ladderize').isChecked(), 'Ladderize survives an empty browser profile');
    assert(await reopened.evaluate(() => treeView.show.ladderize));
    await reopened.locator('#tree-show').click();
    await reopened.locator('#tree-ladderize').uncheck();
    await reopened.evaluate(() => treeLayoutSave);
    await reopened.reload();
    await reopened.waitForFunction(id => items.some(it => it.id === id), id);
    await reopened.evaluate(id => { selected = [id]; showSelection(); }, id);
    await reopened.waitForFunction(id => treeShown?.item.id === id && !!treeView?.nodes, id);
    assert.equal(await reopened.locator('#tree-ladderize').isChecked(), false, 'Unchecked state also survives reopening');
    await fresh.close();
    const makeTree = async label => (await api('/api/library/upload','POST', {
      filename: label+'.nwk', text: '('+Array.from({length:120},(_,i)=>`${label}_${i}:${1+i/30}`).join(',')+');'
    })).ids[0];
    const treeA=await makeTree('Viewport_A'), treeB=await makeTree('Viewport_B');
    const sequence=(await api('/api/library/upload','POST',{filename:'viewport.fa',text:'>sequence\nACGTACGT\n'})).ids[0];
    await page.evaluate(()=>loadList());
    const open = async id => {
      await page.evaluate(async id=>{selected=[id];await showSelection();},id);
      await page.waitForFunction(id=>treeShown?.item.id===id && treeKey===treeShown.key,id);
    };
    const viewport=()=>page.evaluate(()=>[treeView.spacing,treeView.xScale,treeView.scrollX,treeView.scrollY]);
    const assertViewport=(actual,expected,message)=>assert(actual.every((v,i)=>Math.abs(v-expected[i])<1e-7),message);
    await page.locator('#tree-search').fill('');
    await open(treeA);
    assert(await page.evaluate(()=>treeView.contentHeight()<=treeView.areaHeight+1),'First open fits the whole tree');
    await page.evaluate(()=>{treeView.zoomTips(4);treeView.zoomBranches(3);treeView.scrollX=150;treeView.scrollY=300;treeView.clampScroll();treeView.draw();});
    const stateA=await viewport();
    assert(stateA[2]>0 && stateA[3]>0,'Fixture exercises both scroll axes');
    await open(treeB);
    assert(await page.evaluate(()=>treeView.contentHeight()<=treeView.areaHeight+1),'An unseen tree starts fitted independently');
    await page.evaluate(()=>{treeView.zoomTips(3);treeView.zoomBranches(6);treeView.scrollX=80;treeView.scrollY=150;treeView.clampScroll();treeView.draw();});
    const stateB=await viewport();
    await open(treeA);assertViewport(await viewport(),stateA,'Tree A restores both zooms and pan offsets');
    await open(treeB);assertViewport(await viewport(),stateB,'Tree B has independent viewport memory');
    await page.evaluate(async id=>{selected=[id];await showSelection();},sequence);
    await page.waitForTimeout(50); // Let the hidden viewer receive its ResizeObserver callback.
    await open(treeA);assertViewport(await viewport(),stateA,'Visiting a sequence does not destroy tree state');
    await page.locator('#tree-search').fill('Viewport');
    await page.evaluate(()=>{treeView.scrollY=240;treeView.clampScroll();treeView.draw();});
    const searched=await viewport();
    await open(treeB);await open(treeA);
    assertViewport(await viewport(),searched,'Search highlights do not recenter a restored tree');
    await page.evaluate(()=>treeView.fitWhole());
    const fitted=await viewport();
    await open(treeB);await open(treeA);
    assertViewport(await viewport(),fitted,'Explicit Fit whole is remembered too');
    await page.evaluate(()=>{treeView.zoomTips(3);treeView.zoomBranches(2);});
    await page.evaluate(async id=>{itemById(id).mtime+=1;treeKey=null;await showTree(itemById(id));},treeA);
    assertViewport(await viewport(),fitted,'Changed tree versions discard stale viewport state');
    await open(id);
    await page.evaluate(()=>{treeView.collapsed.clear();treeView.layout();treeView.toggleCollapse(1);});
    const collapsedViewport=await viewport();
    await open(treeB);await open(id);
    assert(await page.evaluate(()=>treeView.collapsed.has(1)),'Collapsed clades remain part of the saved viewport');
    assertViewport(await viewport(),collapsedViewport,'Collapsed row positions restore consistently');

    // Tree edits require the explicit mode, including callbacks and history.
    const editableId = (await api('/api/library/upload', 'POST', {filename: 'editable.nwk', text: '((A:1,B:1):1,C:2,D:3);'})).ids[0];
    await page.evaluate(() => loadList());
    await open(editableId);
    assert.equal(await page.locator('#tree-lock').getAttribute('aria-pressed'), 'false');
    await page.evaluate(() => treeView.select(treeView.nodes.find(n => n.name === 'A').index));
    await page.locator('#tree-curate').click();
    assert(await page.getByRole('button', {name: 'Remove the tip “A”', exact: true}).isDisabled());
    await page.keyboard.press('Escape');
    await page.evaluate(async () => {await pruneTree(treeShown.item, ['A']); await rerootTree(treeShown.item, ['A']);});
    assert.equal((await api('/api/tree/' + editableId)).nodes.filter(n => n.name).length, 4);
    await page.locator('#tree-lock').click();
    await page.waitForFunction(() => treeEditable());
    // Another window cannot edit a tree while this mode holds its lock.
    const contender = await fetch(base + '/api/tree/' + editableId + '/prune', {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Cladia-Window': 'other-tree-window'}, body: JSON.stringify({tips: ['B']})});
    assert.equal(contender.status, 409);
    await page.locator('#tree-curate').click();
    await page.getByRole('button', {name: 'Remove the tip “A”', exact: true}).click();
    await page.waitForFunction(() => treeShown.tips === 3);
    assert(await page.evaluate(() => treeEditable()), 'Saved tree edits retain edit mode');
    await page.waitForFunction(() => !document.getElementById('tree-undo').disabled);
    await page.locator('#tree-undo').click();
    await page.waitForFunction(() => treeShown.tips === 4);
    await page.waitForFunction(() => !document.getElementById('tree-redo').disabled);
    await page.locator('#tree-lock').click();
    assert(await page.locator('#tree-redo').isDisabled());
    await page.evaluate(() => treeUndo(true));
    assert.equal((await api('/api/tree/' + editableId)).nodes.filter(n => n.name).length, 4);
    await page.locator('#tree-lock').click();
    await page.waitForFunction(() => treeEditable());
    await open(treeB);
    assert.equal(await page.evaluate(() => treeEditable()), false, 'Another tree opens read only');
    await open(editableId);
    assert.equal(await page.evaluate(() => treeEditable()), false, 'Returning opens read only');

    // The visible Sort button supports one selected alignment reference.
    const alignmentId = (await api('/api/library/upload', 'POST', {filename: 'distance.fasta',
      text: '>far\nTTTT--\n>missing\nNNNN--\n>ref\nACGT--\n>near\nACGA--\n'})).ids[0];
    await page.evaluate(async id => {await loadList(); selected = [id]; showSelection();}, alignmentId);
    await page.waitForFunction(id => seqShown?.alignment?.id === id && seqView.rows.length === 4, alignmentId);
    assert(await page.locator('#seq-sort').isVisible());
    await page.evaluate(() => seqView.select(0, 5, 0, 1));
    await page.locator('#seq-sort').click();
    assert(await page.getByRole('button', {name: 'Distance to selected sequence (select one row first)', exact: true}).isDisabled());
    await page.keyboard.press('Escape');
    await page.evaluate(() => seqView.select(0, 5, 2, 2));
    await page.locator('#seq-sort').click();
    await page.getByRole('button', {name: 'Distance to ref (closest first)', exact: true}).click();
    await page.waitForFunction(() => seqView.rows.map(r => r.name).join(',') === 'ref,near,far,missing' && seqView.selection?.r0 === 0);
    assert.equal((await api('/api/library')).find(it => it.id === alignmentId).sorted.reference, 'ref');
    assert.equal(await page.evaluate(() => seqView.rows[seqView.selection.r0].name), 'ref', 'The reference stays selected after sorting');
    await page.locator('#seq-sort').click();
    await page.getByRole('button', {name: '✓ Distance to ref (closest first)', exact: true}).click();
    await page.waitForFunction(() => seqView.rows.map(r => r.name).join(',') === 'far,near,ref,missing');
    await page.screenshot({path: path.join(artifacts, 'visible-sort.png')});

    assert.deepEqual(errors, []);
    console.log(JSON.stringify({passed: true, checks: ['Ctrl+Shift wheel focus', 'aligned labels', 'collapsed summary', 'label-only selection', 'Find bold text', 'Ctrl/Shift tip selection', 'clipboard and context menu', 'Fit selection', 'zoomed-out Find markers', 'tree preferences across sessions', 'autocomplete disabled', 'independent tree viewports', 'hidden viewer restoration', 'search-safe restoration', 'modified tree invalidation', 'tree edit mode and undo', 'tree edit locks across windows', 'visible Sort and selected-reference distance order'], artifacts, errors}));
  } finally {
    if (browser) await browser.close(); server.kill();
    if (server.exitCode === null) await new Promise(r => server.once('exit', r));
    await fs.rm(scratch, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
