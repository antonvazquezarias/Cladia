// Folder navigation, mutation safeguards, keyboard/drag behavior and saved state.
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


    const call=async(route,method='GET',data)=>{const r=await fetch(base+route,{method,headers:{'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});assert(r.ok,await r.clone().text());return r.json();};
    for(const name of ['Alpha','Beta','MoveMe','ZParent','DeleteMe','Survivor',...Array.from({length:28},(_,i)=>'Filler '+String(i).padStart(2,'0'))]) await call('/api/folders','POST',{name});
    for(const [parent,name] of [['Alpha','Child'],['MoveMe','Deep'],['ZParent','Needle'],['DeleteMe','Survivor']]) await call('/api/folders','POST',{name,parent});
    const upload=async(name,folder)=>(await call('/api/library/upload','POST',{filename:name+'.fa',text:'>'+name+'\nACGTACGTACGT\n',folder})).ids[0];
    const a=await upload('matching_alpha','Alpha'),b=await upload('matching_beta','Beta');
    const extra=await upload('beta_extra_one','Beta');await upload('beta_extra_two','Beta');
    await upload('nested_item','Alpha/Child');
    await upload('moved_child_item','DeleteMe/Survivor');await upload('existing_child_item','Survivor');
    await page.goto(base);await page.waitForFunction(()=>items.length===7);
    const current=()=>page.evaluate(()=>currentFolder);
    const navigate=folder=>page.evaluate(folder=>setFolder(folder),folder);
    const clickLibrarySpace=async()=>{
      const box=await page.locator('#lists').boundingBox();
      await page.locator('#lists').click({position:{x:10,y:box.height-8}});
    };
    const folderRow=folder=>page.locator('#folder-tree [role=treeitem]').filter({has:page.getByText(folder,{exact:true})});
    const submitFolder=async name=>{await page.locator('#folder-dialog input[name=name]').fill(name);await page.locator('#folder-dialog button[value=ok]').click();};
    const waitFolder=folder=>page.waitForFunction(folder=>currentFolder===folder,folder);
    for (const destination of [null, 'Beta', 'Filler 00']) {
      await navigate('Alpha');await page.locator(`#lists li[data-id="${a}"]`).click();
      await page.waitForFunction(a=>shownIds.includes(a) && !!seqShown?.doc,a);
      if (destination) await navigate(destination);
      if (destination==='Filler 00') await page.locator('#lists li.empty:visible').click();
      else await clickLibrarySpace();
      assert.deepEqual(await page.evaluate(()=>shownIds),[],`Blank library clicks clear the viewer after ${destination || 'item selection'}`);
      assert(await page.locator('#placeholder').evaluate(el=>!el.classList.contains('inactive')));
    }
    await navigate('Alpha');await page.locator('#folder-button').click();await page.locator('#folder-new').click();await submitFolder('Created');
    await waitFolder('Alpha/Created');assert.equal(await current(),'Alpha/Created','New folder opens immediately');
    await navigate('Alpha');await page.locator('#folder-button').click();
    assert.equal(await folderRow('Alpha').locator('small').textContent(),'1','Counts describe own items');
    assert((await folderRow('Alpha').locator('small').getAttribute('title')).includes('1 in subfolders'));
    await page.locator('#folder-button').click();
    await page.locator('#filter').fill('matching');
    assert((await page.locator('#folder-button').textContent()).includes('all folders'));
    await page.locator('#folder-button').click();await page.locator('#folder-popup').getByText('Beta',{exact:true}).click();
    assert.equal(await current(),'Beta');assert.equal(await page.locator('#filter').inputValue(),'');
    assert.deepEqual(await page.evaluate(()=>[...new Set(visibleRows('sequence').map(it=>it.folder))]),['Beta']);

    await navigate('Alpha');await page.locator(`#lists li[data-id="${a}"]`).click();
    await page.waitForFunction(a=>seqShown?.items[0]?.id===a && !!seqShown.doc,a);
    await page.locator('#seq-lock').click();
    await page.evaluate(()=>{window.folderTestDoc=seqShown.doc;seqShown.doc.insert(0,"A");refreshSingle();});
    await navigate('Beta');await page.evaluate(()=>refreshShown());
    assert(await page.evaluate(()=>seqShown.doc===window.folderTestDoc && hasUnsavedChanges()),'Browsing preserves the edited document');
    assert.deepEqual(await page.evaluate(()=>selected),[]);assert.equal(await page.evaluate(()=>anchorId),null);
    page.once('dialog',d=>d.dismiss());await clickLibrarySpace();
    assert(await page.evaluate(()=>seqShown.doc===window.folderTestDoc && hasUnsavedChanges()),
      'Cancelling an empty-space click after navigation preserves unsaved edits');
    assert.deepEqual(await page.evaluate(()=>selected),[]);assert.equal(await page.evaluate(()=>anchorId),null);
    page.once('dialog',d=>d.dismiss());
    await page.locator('#sequences li[data-id]').first().click();
    assert.deepEqual(await page.evaluate(()=>selected),[]);
    assert.equal(await page.evaluate(()=>anchorId),null,'Cancelling an item switch restores the cleared sidebar anchor');
    assert(await page.evaluate(()=>seqShown.doc===window.folderTestDoc && hasUnsavedChanges()));
    page.once('dialog',d=>d.dismiss());
    await page.locator('#sequences li[data-id]').first().click({button:'right'});
    assert.deepEqual(await page.evaluate(()=>selected),[]);
    assert.equal(await page.evaluate(()=>anchorId),null);
    assert(await page.locator('#list-menu').isHidden(),'Cancelled context switches do not offer actions for another item');
    await page.evaluate(()=>{seqShown.doc.undo();refreshSingle();});await page.evaluate(()=>refreshShown());
    assert.equal(await page.evaluate(()=>seqShown.items[0].id),a,'Clean viewer also survives a refresh with no sidebar selection');
    await page.evaluate(a=>api('POST','/api/library/rename',{id:a,name:'renamed_alpha'}),a);await page.evaluate(()=>refreshShown());
    await page.waitForFunction(()=>seqShown?.items[0]?.label==='renamed_alpha');
    assert.deepEqual(await page.evaluate(()=>selected),[],'Viewer refresh does not recreate hidden sidebar selection');
    await page.evaluate(()=>openSideTab('metadata'));
    await page.waitForFunction(()=>document.querySelector('#metadata-title').textContent==='renamed_alpha');
    await page.evaluate(()=>openSideTab('metadata'));
    await page.evaluate(a=>{anchorId=a;},a);
    await page.locator('#sequences li[data-id]').first().click({modifiers:['Shift']});
    assert.equal((await page.evaluate(()=>selected)).length,1,'Shift-click with an invisible anchor selects the clicked item');
    await page.locator('#filter').fill('matching');
    assert.deepEqual(await page.evaluate(()=>selected),[],'Filtering removes hidden sidebar selections');
    await page.locator('#filter').fill('');

    await navigate('MoveMe');await page.evaluate(()=>{openBranches.add('MoveMe');saveLibraryView();});
    await page.evaluate(()=>moveFolder('MoveMe','Beta'));await waitFolder('Beta/MoveMe');
    assert(await page.evaluate(()=>openBranches.has('Beta/MoveMe') && !openBranches.has('MoveMe')));
    await navigate('Beta/MoveMe/Deep');await page.evaluate(()=>folderDialog({renaming:'Beta/MoveMe'}));await submitFolder('Renamed');
    await waitFolder('Beta/Renamed/Deep');
    assert(await page.evaluate(()=>openBranches.has('Beta/Renamed') && !openBranches.has('Beta/MoveMe')));

    await page.locator('#folder-button').click();await page.locator('#folder-filter').fill('Needle');await page.locator('#folder-filter').press('Enter');
    await waitFolder('ZParent/Needle');
    await page.locator('#folder-button').click();await page.locator('#folder-filter').fill('Needle');await page.locator('#folder-filter').press('ArrowDown');
    assert.equal(await page.evaluate(()=>document.activeElement.dataset.path),'ZParent/Needle');
    await page.keyboard.press('ArrowLeft');assert.equal(await page.evaluate(()=>document.activeElement.dataset.path),'ZParent');
    await page.keyboard.press('ArrowRight');assert.equal(await page.evaluate(()=>document.activeElement.dataset.path),'ZParent/Needle');
    await page.keyboard.press('F2');await page.locator('#folder-dialog').waitFor({state:'visible'});
    assert.equal(await page.locator('#folder-dialog input[name=name]').inputValue(),'Needle');
    await page.keyboard.press('Escape');
    await page.locator('#folder-button').click();await page.locator('#folder-filter').fill('nothing-matches-this');
    assert(await page.locator('#folder-empty').isVisible());
    await page.locator('#folder-filter').press('Enter');assert.equal(await current(),'ZParent/Needle');
    await page.keyboard.press('Escape');assert(await page.locator('#folder-popup').isHidden());
    assert.equal(await page.evaluate(()=>document.activeElement.id),'folder-button');

    await page.evaluate(()=>{openBranches.delete('ZParent');springOpen('ZParent');closeFolderPopup();openFolderPopup();springOpen('ZParent');});
    await page.waitForFunction(()=>openBranches.has('ZParent'));
    await page.evaluate(()=>{openBranches.delete('ZParent');springOpen('ZParent');springOpen('Alpha/Created');});
    await page.waitForTimeout(700);assert.equal(await page.evaluate(()=>openBranches.has('ZParent')),false,'Hovering a leaf cancels an earlier branch expansion');
    await page.evaluate(()=>{springOpen('ZParent');window.dispatchEvent(new Event('dragend'));});
    assert.equal(await page.evaluate(()=>springPath),null);
    await page.evaluate(()=>closeFolderPopup());

    // Real mouse drag: the item opens the picker, then drops onto another folder.
    await navigate('Beta');
    const source=await page.locator(`#lists li[data-id="${extra}"]`).boundingBox();
    const button=await page.locator('#folder-button').boundingBox();
    await page.mouse.move(source.x+source.width/2,source.y+source.height/2);await page.mouse.down();
    await page.mouse.move(button.x+button.width/2,button.y+button.height/2,{steps:12});
    await page.locator('#folder-popup').waitFor({state:'visible'});
    await folderRow('Alpha').scrollIntoViewIfNeeded();
    const target=await folderRow('Alpha').boundingBox();
    await page.mouse.move(target.x+target.width/2,target.y+target.height/2,{steps:8});await page.mouse.up();
    await page.waitForFunction(extra=>itemById(extra)?.folder==='Alpha',extra);
    assert.equal(await current(),'Beta','Moving existing items keeps the source folder open');

    await page.evaluate(b=>folderDialog({parent:'Beta',moving:[b]}),b);await submitFolder('Collected');await waitFolder('Beta/Collected');
    assert.equal((await call('/api/library')).find(it=>it.id===b).folder,'Beta/Collected');
    await page.evaluate(()=>folderDialog({parent:'Beta'}));await page.locator('#folder-dialog input[name=name]').fill('Cancelled');await page.keyboard.press('Escape');
    assert(!(await call('/api/folders')).includes('Beta/Cancelled'));
    await page.evaluate(()=>folderDialog({parent:'Beta'}));await submitFolder('Collected');
    await page.locator('#folder-dialog .error').waitFor({state:'visible'});assert.equal(await current(),'Beta/Collected');
    await page.locator('#folder-dialog button[value=cancel]').click();

    await navigate('DeleteMe/Survivor');
    page.once('dialog',d=>d.accept());await page.evaluate(()=>deleteFolder('DeleteMe'));
    assert.equal(await current(),'DeleteMe/Survivor');
    assert((await page.locator('#toast').textContent()).includes('would merge'));
    assert((await call('/api/folders')).includes('DeleteMe'));
    await call('/api/folders/rename','POST',{name:'Survivor',new:'Existing'});await page.evaluate(()=>loadList());
    page.once('dialog',d=>d.accept());await page.evaluate(()=>deleteFolder('DeleteMe'));await waitFolder('Survivor');
    const library=await call('/api/library');
    assert.equal(library.find(it=>it.label==='moved_child_item').folder,'Survivor');
    assert.equal(library.find(it=>it.label==='existing_child_item').folder,'Existing');

    await call('/api/folders','POST',{name:'PendingParent'});await page.evaluate(()=>loadList());
    await page.evaluate(()=>folderDialog({parent:'PendingParent'}));
    await call('/api/folders/rename','POST',{name:'PendingParent',new:'OtherWindowRename'});await submitFolder('Child');
    await page.locator('#folder-dialog .error').waitFor({state:'visible'});
    assert((await page.locator('#folder-dialog .error').textContent()).includes('parent folder no longer exists'));
    assert(!(await call('/api/folders')).includes('PendingParent'));
    await page.locator('#folder-dialog button[value=cancel]').click();

    // Moving list members must not leave the viewer bound to a reused record ID.
    const list=(await call('/api/library/upload','POST',{filename:'members.fa',text:'>read0\nAAAA\n>read1\nCCCC\n>read2\nGGGG\n',folder:'Alpha'})).ids[0];
    await page.evaluate(()=>loadList());
    await page.evaluate(id=>showSelection([id]),list+'-1');
    await page.waitForFunction(id=>seqShown?.items[0]?.id===id && seqShown.doc?.sequence==='CCCC',list+'-1');
    await page.evaluate(id=>moveToFolder([id],'Beta'),list+'-0');
    await page.waitForFunction(id=>seqShown?.items[0]?.id===id && seqShown.doc?.sequence==='CCCC',list+'-0');
    await page.evaluate(id=>moveToFolder([id],'Beta'),list+'-0');
    await page.waitForFunction(list=>seqShown?.items[0]?.id!==list+'-0' && seqShown.doc?.sequence==='CCCC',list);
    assert.equal(await page.evaluate(()=>seqShown.items[0].folder),'Beta');

    await page.evaluate(a=>showSelection([a]),a);
    await page.waitForFunction(a=>seqShown?.items[0]?.id===a && !!seqShown.doc,a);
    await page.evaluate(()=>{sideState.open=false;openSideTab('metadata');});
    await page.locator('#metadata-details [data-fact="Folder"]').waitFor();
    await page.evaluate(a=>moveToFolder([a],'Beta'),a);
    assert((await page.locator('#metadata-details [data-fact="Folder"]').textContent()).includes('Beta'),
      'Open metadata follows an item move without discarding editable fields');

    await page.locator('#seq-lock').click();
    await page.evaluate(()=>{seqShown.doc.insert(0,'T');refreshSingle();});
    await navigate('Filler 00');page.once('dialog',d=>d.accept());
    await page.locator('#lists li.empty:visible').click();
    assert.deepEqual(await page.evaluate(()=>shownIds),[]);
    assert.equal(await page.evaluate(()=>hasUnsavedChanges()),false,'Confirmed blank-space dismissal discards edits and clears the viewer');

    await navigate('ZParent/Needle');await page.evaluate(()=>{openBranches.add('ZParent');openBranches.add('Beta');saveLibraryView();return libraryViewSave;});
    const context=await browser.newContext({viewport:{width:1000,height:700}}),fresh=await context.newPage();
    fresh.on('pageerror',e=>errors.push(e.message));await fresh.goto(base);await fresh.waitForFunction(()=>items.length>=7);
    assert.equal(await fresh.evaluate(()=>currentFolder),'ZParent/Needle','Folder survives an empty browser profile');
    assert(await fresh.evaluate(()=>openBranches.has('ZParent') && openBranches.has('Beta')));
    await fresh.locator('#folder-button').click();
    const active=await fresh.locator('#folder-tree li.current').boundingBox(),tree=await fresh.locator('#folder-tree').boundingBox();
    assert(active.y>=tree.y-1 && active.y+active.height<=tree.y+tree.height+1,'Picker scrolls the active folder into view');
    const artifacts=path.resolve('build/folder-qa');await fs.mkdir(artifacts,{recursive:true});
    await fresh.screenshot({path:path.join(artifacts,'folder-navigation.png')});
    const viewPage=await context.newPage();viewPage.on('pageerror',e=>errors.push(e.message));
    await viewPage.goto(base+'?view='+a);
    await viewPage.waitForFunction(a=>seqShown?.items[0]?.id===a && !!seqShown.doc,a);
    await viewPage.evaluate(()=>refreshShown());
    assert.deepEqual(await viewPage.evaluate(()=>shownIds),[a],'View-only windows retain explicit items outside the sidebar folder');
    await viewPage.close();
    await context.close();assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:['create/open','global filter exit','visible selection and Shift anchor','dirty and clean viewer preservation','metadata','move/rename expansion remapping','keyboard search/navigation','drag hover cleanup','real item drag','new move destination','list-member identity preservation','cancel/duplicate errors','delete collisions and descendant navigation','stale parent','cross-session preferences','active-row scrolling'],errors}));
  } finally {
    if (browser) await browser.close(); server.kill();
    if (server.exitCode === null) await new Promise(r => server.once('exit', r));
    await fs.rm(scratch, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
