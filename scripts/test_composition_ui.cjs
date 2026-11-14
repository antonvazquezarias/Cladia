// Real browser/export QA. Uses a private library/server, never the user's app.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises'), os = require('node:os'), path = require('node:path'), net = require('node:net');
const assert = require('node:assert/strict');
(async () => {
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'cladia-composition-'));
  const artifacts = path.resolve(process.env.CLADIA_FIGURE_ARTIFACTS || 'build/composition-qa');
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

    const mito = (await api('/api/library/upload', 'POST', { filename: 'synthetic_mt.gb', text: await fs.readFile('tests/data/synthetic_mt.gb', 'utf8') })).ids[0];
    const big = (await api('/api/library/upload', 'POST', { filename:'large.fa', text: '>Large\n' + 'GGCA'.repeat(15000) + 'ATAT'.repeat(7500) + 'ACGT'.repeat(15000) })).ids[0];
    const protein = (await api('/api/library/upload', 'POST', { filename:'protein.faa', text: '>Protein\nMKWVTFISLLFLFSSAYSR' })).ids[0];
    browser = await chromium.launch({ executablePath: process.env.CLADIA_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
    const page = await browser.newPage({ viewport: {width:1450,height:1000} });
    const errors=[]; page.on('pageerror', e=>errors.push(e.message));
    await page.goto(base); await page.waitForFunction(()=>items.length>=3);
    async function open(id) { await page.evaluate(id=>{selected=[id];showSelection();},id); await page.waitForFunction(id=>seqShown?.items[0]?.id===id && seqView?.length>0 && (seqShown.doc || seqShown.mapping),id); }
    await open(mito);
    await page.locator('#seq-show').click();
    await page.locator('#seq-gc').check(); await page.locator('#seq-gc-skew').check();
    await page.locator('#seq-gc-window').selectOption('100');
    assert(await page.locator('#seq-annotation-options #seq-features').isVisible());
    assert(await page.locator('#seq-annotation-options #seq-types').isVisible());
    assert.equal(await page.locator('#seq-annotation-options #seq-gc').count(), 0);
    await page.locator('#seq-features').uncheck();
    assert(await page.locator('#seq-types').isHidden());
    assert(await page.locator('#seq-gc-options').isVisible());
    assert.equal(await page.evaluate(()=>seqView.gcTracks().length),2);
    await page.locator('#seq-features').check();
    await page.locator('#seq-gc-size').selectOption('large');
    assert.equal(await page.evaluate(()=>seqView.gcHeight()),128);
    await page.locator('#seq-gc-size').selectOption('compact');
    await page.screenshot({path:path.join(artifacts,'show-panel.png')});
    await page.locator('#seq-show').click();
    await page.evaluate(()=>setSeqMap('linear'));
    assert.equal(await page.evaluate(()=>seqView.gcHeight()),88);
    const anchorMotion = await page.evaluate(async()=>{
      const v=seqView;
      v.charWidth=.3; v.scrollX=500; v.caret=2500;
      const before=v.caret*v.charWidth-v.scrollX;
      v.zoom(1.1);
      while(v.navigation) await new Promise(requestAnimationFrame);
      const after=v.caret*v.charWidth-v.scrollX;
      for(const factor of [1.25,.8]) {
        v.zoom(factor);
        while(v.navigation) {
          await new Promise(requestAnimationFrame);
          if(Math.abs(v.caret*v.charWidth-v.scrollX-v.seqWidth/2)>.01)
            throw Error('Centred zoom target moved during an animation frame');
        }
      }
      v.caret=null; v.charWidth=v.minCharWidth(); v.scrollX=0; v.draw();
      return after-v.seqWidth/2;
    });
    assert(Math.abs(anchorMotion)<.01,'clicked base must finish exactly at the centre after animated zoom');
    await page.evaluate(async()=>{
      const v=seqView, settle=async()=>{while(v.navigation) await new Promise(requestAnimationFrame);};
      v.charWidth=.5;v.scrollX=1000;
      v.revealFeature(v.rows[0].features.at(-1),0);
      if(!v.navigation) throw Error('Feature reveal must animate');
      const destination=v.navigation.target.scrollX;await settle();
      if(Math.abs(v.scrollX-destination)>.01) throw Error('Feature reveal missed its destination');
      v.matches=[{c0:5000,c1:5100,row:0}];v.matchIndex=-1;v.findNext();
      if(!v.navigation) throw Error('Search navigation must animate');await settle();
      if(Math.abs((5050.5*v.charWidth-v.scrollX)-v.seqWidth/2)>.01) throw Error('Search did not centre the match');
      v.matches=[];v.matchIndex=-1;v.selection=null;v.charWidth=v.minCharWidth();v.scrollX=0;v.draw();
    });
    await page.screenshot({path:path.join(artifacts,'mito-linear.png')});
    await page.evaluate(()=>{seqView.onHover({clientX:seqView.canvas.getBoundingClientRect().left+300,clientY:seqView.canvas.getBoundingClientRect().top+40});});
    assert.match(await page.evaluate(()=>seqView.hover),/GC content/);
    assert.match(await page.evaluate(()=>seqView.hover),/percentage points.*genome mean/);
    const skewTip = await page.evaluate(()=>seqView.gcTip(seqView.gcTracks()[1],0));
    assert.match(skewTip,/\(G − C\) \/ \(G \+ C\)/);
    assert(!skewTip.includes('%'));
    await page.evaluate(()=>{seqView.charWidth*=5;seqView.scrollX=100;seqView.draw();});
    await page.evaluate(()=>setSeqMap('circular'));
    const turn = await page.evaluate(async()=>{
      const v=seqView; v.circZoom=1; v.rotation=v.length*.9; v.caret=v.length*.1;
      const before=v.rotation;v.circularZoom(1.1);
      const initial=v.rotation;while(v.navigation) await new Promise(requestAnimationFrame);const after=v.rotation;
      v.circZoom=1;v.rotation=0;v.caret=null;v.draw();
      return {before,initial,after,length:v.length};
    });
    assert.equal(turn.initial,turn.before,'rotation must not snap before its first frame');
    assert(Math.abs(turn.after-turn.length*.1)<.01,'animated rotation must finish exactly at the clicked base');
    await page.evaluate(async()=>{
      const v=seqView;v.rotateTo(v.length*.6);
      await new Promise(requestAnimationFrame);v.rotateBy(35);
      const rotation=v.rotation;
      await new Promise(resolve=>setTimeout(resolve,200));
      if(v.navigation || v.rotation!==rotation) throw Error('Manual rotation must cancel the active animation');
      v.rotation=0;v.draw();
    });
    await page.screenshot({path:path.join(artifacts,'mito-circular.png')});
    await page.evaluate(()=>{
      const v=seqView,f=v.rows[0].features.find(f=>f.type==='CDS' && f.parts.some(p=>p.end-p.start>500));
      const p=f.parts.reduce((a,b)=>b.end-b.start>a.end-a.start?b:a);
      v.rotation=p.start;v.circZoom=v.length*10/(Math.PI*2*v.circGeometry().base);
      const labels=[],original=v.textAlong;
      v.textAlong=function(g,a,r,text){labels.push({a,text});return original.call(this,g,a,r,text);};
      try {v.draw();} finally {v.textAlong=original;}
      const label=labels.find(label=>label.text===f.name);
      if(!label || label.a<=0) throw Error('Gene-start zoom must centre the feature label inside its visible arc');
    });
    await page.screenshot({path:path.join(artifacts,'gene-start-label-circular.png')});
    await page.evaluate(()=>{seqView.circZoom=1;seqView.rotation=0;seqView.draw();});
    await open(big); await page.evaluate(()=>setSeqMap('linear'));
    await page.evaluate(()=>{
      seqShown.doc.features=[
        {type:'gene',name:'rnl',start:20000,end:60000,strand:1},
        {type:'rRNA',name:'rnl',start:20000,end:60000,strand:1},
        {type:'gene',name:'gene',start:30000,end:34000,strand:-1},
        {type:'CDS',name:'hypothetical protein',start:30000,end:34000,strand:-1},
      ];
      refreshSingle();
    });
    for (const mode of ['linear','circular']) {
      await page.evaluate(mode=>setSeqMap(mode),mode);
      const pair=await page.evaluate(()=>{
        const f=seqView.rows[0].features.find(f=>f.type==='CDS');
        const lanes=seqView.circularMap?seqView.circLanes():null;
        return {lane:f.lane,parent:f.gene?.lane,strand:f.gene?.strand,
          radius:lanes?.tops[f.lane],parentRadius:lanes?.tops[f.gene?.lane]};
      });
      assert.equal(pair.strand,-1);assert.equal(pair.lane,pair.parent+1);
      if(mode==='circular') assert(pair.radius>pair.parentRadius);
      await page.screenshot({path:path.join(artifacts,`nested-gene-${mode}.png`)});
    }
    await page.evaluate(()=>setSeqMap('linear'));
    await page.screenshot({path:path.join(artifacts,'large-linear.png')});
    const old = await page.evaluate(()=>seqView.gcCache.sequence);
    await page.evaluate(()=>{seqShown.doc.sequence='A'+seqShown.doc.sequence.slice(1);refreshSingle();});
    assert.notEqual(await page.evaluate(()=>seqView.gcCache.sequence),old);
    await page.evaluate(()=>changeSeqShow({gcCumulative:true,gcWindow:500}));
    assert.equal(await page.evaluate(()=>seqView.gcTracks()[1].key),'cumulative');
    await page.evaluate(()=>setSeqMap('circular'));
    await page.screenshot({path:path.join(artifacts,'large-circular.png')});
    await page.evaluate(()=>changeSeqShow({gc:false,gcSkew:false}));
    assert.equal(await page.evaluate(()=>seqView.gcHeight()),0);
    await page.evaluate(()=>changeSeqShow({gc:true,gcSkew:true}));
    await page.reload(); await page.waitForFunction(()=>items.length>=3); await open(big);
    assert.equal(await page.evaluate(()=>seqShow.gcWindow),500);
    assert.equal(await page.evaluate(()=>seqShow.gcSize),'compact');
    await page.evaluate(ids=>{selected=ids;showSelection();},[big,mito]);
    await page.waitForFunction(()=>seqShown?.mode==='sequences' && seqView.mode==='sequences');
    assert.equal(await page.evaluate(()=>seqView.gcHeight()),0);
    const mapped = await page.evaluate(()=>items.find(it=>it.kind==='mapping' && it.name==='ont')?.id);
    if (mapped) {
      await open(mapped); await page.waitForFunction(()=>!!seqShown.mapping);
      await page.evaluate(()=>setMappingReadsMode('all'));
      await page.waitForFunction(()=>seqView.reads?.reads?.length>0);
      assert.equal(await page.evaluate(()=>seqView.gcHeight()),0);
      await page.screenshot({path:path.join(artifacts,'long-reads.png')});
      await page.evaluate(()=>{seqView.charWidth*=3;seqView.scrollX+=100;seqView.draw();});
      await page.screenshot({path:path.join(artifacts,'long-reads-zoom.png')});
    }
    await open(protein); await page.locator('#seq-show').click();
    assert(await page.locator('#seq-gc-controls').isHidden());
    await page.locator('#seq-show').click();
    await page.evaluate(async()=>{localStorage.removeItem('cladia.tool.options.map.reads.minimap2');await openTool('map');});
    const readType=page.locator('#map-registered-dialog [name="read_type"]');
    const extension=page.locator('#map-registered-dialog label').filter({has:page.locator('[name="extend"]')});
    assert.equal(await readType.inputValue(),'ont_hq','High-accuracy Nanopore is the generated dialog default');
    await readType.selectOption('ont');assert(await extension.isHidden());
    await readType.selectOption('hifi');assert(await extension.isVisible());
    await readType.selectOption('ont_hq');assert(await extension.isHidden());
    assert.match(await readType.locator('option:checked').textContent(),/high accuracy/);
    await page.screenshot({path:path.join(artifacts,'map-long-reads.png')});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,artifacts,errors}));
  } finally {
    if (browser) await browser.close(); server.kill();
    if (server.exitCode === null) await new Promise(r => server.once('exit', r));
    await fs.rm(scratch, { recursive: true, force: true });
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
