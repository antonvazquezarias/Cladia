// Overlapping overview annotations and zoom anchors, without a browser.
const assert = require('node:assert/strict');
global.window = global;
require('../cladia/static/seqview.js');
const v = Object.create(SeqView.prototype);
for (const key of ['seqWidth','single','aligned']) Object.defineProperty(v,key,{value:null,writable:true});
Object.assign(v, {length:10000,seqWidth:1000,nameWidth:150,charWidth:1,scrollX:1000,
  scrollY:0,selection:null,readSel:null,readCaret:null,caret:1200,
  minCharWidth:()=>.1,layoutKey:()=>'',laidOutKey:'',draw:()=>{},
  clampScroll:()=>{},centerReadRow:()=>{}});
const screen = () => v.caret*v.charWidth-v.scrollX;
v.zoom(1.25); assert.equal(screen(),v.seqWidth/2);
v.zoom(.8); assert.equal(screen(),v.seqWidth/2);
v.selection={c0:1200,c1:1400,r0:0,r1:0};
const middle=v.selectionMiddle(v.selection), x=middle*v.charWidth-v.scrollX;
v.zoom(1.1); assert(Math.abs(middle*v.charWidth-v.scrollX-v.seqWidth/2)<1e-8);
v.selection=null;v.caret=null;
const mouse=650,at=(mouse-v.nameWidth+v.scrollX)/v.charWidth;
v.zoom(1.25,mouse);assert(Math.abs(at*v.charWidth-v.scrollX-(mouse-v.nameWidth))<1e-8);
v.caret=9000; v.charWidth=18;const offset=v.scrollX;v.zoom(2);assert.equal(v.scrollX,offset);
const rects=[];
v.ctx={fillRect(x,y,w,h){rects.push({color:this.fillStyle,x,y,w,h});},fillText(){},strokeRect(){}};
Object.assign(v,{single:true,aligned:false,trace:null,compare:null,circularMap:false,width:1150,height:200,
  length:1000,seqWidth:1000,selectionPieces:()=>[],charWidth:1,scrollX:0});
const gene={type:'gene',color:'#00aa00',c0:100,c1:900,spans:[[100,900]],parts:[{c0:100,c1:199},{c0:800,c1:900}]};
const cds={type:'CDS',color:'#ffff00',c0:400,c1:499,spans:[[400,499]],parts:[{c0:400,c1:499}]};
const intron={type:'intron',color:'#ffffff',c0:200,c1:799,spans:[[200,799]],parts:[{c0:200,c1:799}]};
v.rows=[{features:[cds,gene,intron]}];v.drawOverview();
const bodies=rects.filter(r=>r.h>1 && ['#00aa00','#ffff00','#ffffff'].includes(r.color));
assert.equal(bodies.filter(r=>r.color==='#00aa00').length,2);
const yellow=bodies.find(r=>r.color==='#ffff00');
const covers=rects.filter(r=>r.x<=yellow.x+10 && r.x+r.w>yellow.x+10 && r.y<=yellow.y+1 && r.y+r.h>yellow.y+1);
assert.equal(covers.at(-1).color,'#ffff00','nested CDS must paint over gene/intron regardless of input order');
rects.length=0;
v.rows=[{features:[{...gene,c0:0,c1:999,spans:[[900,999],[0,100]],parts:[{c0:900,c1:999},{c0:0,c1:100}]}]}];
v.drawOverview();assert(!rects.some(r=>r.color==='#00aa00' && r.x<650 && r.x+r.w>650),'origin-spanning feature must not fill the genome interior');
console.log('Sequence overview: joined parts, nested CDS, origin wrapping and stable zoom anchors passed');

// A nested unnamed CDS must use its own gene, not the enclosing opposite strand.
function paired(features) {
 const view=Object.create(SeqView.prototype);
 view.mode='single';view.length=1000;view.rows=[{name:'test',seq:'A'.repeat(1000),bases:1000}];
 view.layout=()=>{};view.draw=()=>{};
 view.setFeatures({test:{features:features.map(f=>({...f,locations:f.locations||[{start:f.start,end:f.end}]}))}});
 return view.rows[0].features;
}
for (const strand of [-1,1]) for (const reverse of [false,true]) {
 const input=[
  {type:'gene',name:'rnl',start:100,end:900,strand:-strand},
  {type:'rRNA',name:'rnl',start:100,end:900,strand:-strand},
  {type:'gene',name:'gene',start:300,end:399,strand},
  {type:'CDS',name:'hypothetical protein',start:300,end:399,strand},
 ];
 const fs=paired(reverse?input.reverse():input),cds=fs.find(f=>f.type==='CDS');
 assert.equal(cds.gene.name,'gene');assert.equal(cds.gene.strand,strand);
 assert.equal(cds.lane,cds.gene.lane+1,'off-track pair must retain the same gene/CDS lane order');
}
const sameStrand=paired([
 {type:'gene',name:'outer',start:0,end:999,strand:1},
 {type:'gene',name:'gene',start:300,end:399,strand:1},
 {type:'CDS',name:'hypothetical protein',start:300,end:399,strand:1},
]);assert.equal(sameStrand.find(f=>f.type==='CDS').gene.name,'gene');
const orphan=paired([{type:'gene',name:'x',start:100,end:500,strand:-1},
 {type:'CDS',name:'x',start:100,end:500,strand:1}]);
assert.equal(orphan.find(f=>f.type==='CDS').gene,null);
console.log('Gene/CDS lanes: nested unnamed genes, both strands, input order and unpaired opposite strands passed');

// Deterministic frame clock: exact destinations, shortest turns and interruption.
require('../cladia/static/seqview-circular.js');
let now=0, serial=0;const frames=new Map();
global.performance={now:()=>now};
global.requestAnimationFrame=fn=>{frames.set(++serial,fn);return serial;};
global.cancelAnimationFrame=id=>frames.delete(id);
const step=ms=>{now+=ms;const current=[...frames.values()];frames.clear();current.forEach(fn=>fn(now));};
Object.assign(v,{rotation:950,length:1000,charWidth:1,scrollX:0,caret:800,seqWidth:500});
v.navigate({rotation:50});assert.equal(v.rotation,950);step(50);
assert(v.rotation>950 && v.rotation<1050,'first frames must take the short path across zero');
step(120);assert.equal(v.rotation,50);assert.equal(v.navigation,null);
v.navigate({scrollX:1000});step(50);assert(v.scrollX>0 && v.scrollX<1000);
const shown=v.scrollX;v.navigate({scrollX:100});assert.equal(v.scrollX,shown);
step(170);assert.equal(v.scrollX,100);
v.zoom(1.25);v.zoom(1.25);assert.equal(v.navigation.target.charWidth,1.5625);
step(170);assert.equal(v.charWidth,1.5625);assert.equal(v.caret*v.charWidth-v.scrollX,250);
// A centred anchor stays centred on every frame, even with repeated retargeting.
for (const factor of [1.25,.8,1.6,.625]) {
 v.zoom(factor);
 for(let frame=0;frame<10;frame++) {
  step(17);assert(Math.abs(v.caret*v.charWidth-v.scrollX-250)<1e-8,'centred zoom anchor must not oscillate');
 }
}
v.zoom(1.25);step(34);v.zoom(1.25);
for(let frame=0;frame<10;frame++){step(17);assert(Math.abs(v.caret*v.charWidth-v.scrollX-250)<1e-8);}
v.caret=null;
const mouseX=200,mouseBase=(mouseX+v.scrollX)/v.charWidth;
v.zoom(1.25,v.nameWidth+mouseX);
for(let frame=0;frame<10;frame++){step(17);assert(Math.abs(mouseBase*v.charWidth-v.scrollX-mouseX)<1e-8);}
v.caret=800;v.scrollX=v.caret*v.charWidth-100;
v.zoom(1.5);let lastX=100;
for(let frame=0;frame<10;frame++) {
 step(17);const x=v.caret*v.charWidth-v.scrollX;
 assert(x>=lastX-1e-8 && x<=250+1e-8,'off-centre zoom must slide monotonically toward the centre');lastX=x;
}
v.navigate({scrollX:900});step(30);const interrupted=v.scrollX;v.cancelNavigation();step(200);assert.equal(v.scrollX,interrupted);
global.matchMedia=()=>({matches:true});v.navigate({scrollX:50});assert.equal(v.scrollX,50);assert.equal(v.navigation,null);
delete global.requestAnimationFrame;delete global.cancelAnimationFrame;delete global.matchMedia;
console.log('Navigation animation: exact endpoints, shortest turn, retargeting, accumulated zoom, cancellation and reduced motion passed');

// Linear names remain centred in the viewport-clipped exon at high zoom.
const labels=[];
v.ctx=new Proxy({measureText:text=>({width:text.length*6}),fillText:(text,x)=>labels.push({text,x})},
 {get:(target,key)=>key in target?target[key]:(()=>{})});
Object.assign(v,{single:true,aligned:false,width:1150,nameWidth:150,charWidth:10,scrollX:4500,
 selection:null,tracksOn:()=>false,afterStop:()=>null});
const exon={start:500,end:950,c0:500,c1:950};
const labelFeature={type:'gene',name:'Visible gene',strand:1,color:'#00aa00',c0:500,c1:950,lane:0,parts:[exon]};
const labelRow={bases:1000,features:[labelFeature],laneTops:[0]};v.rows=[labelRow];
v.drawFeatures(labelRow,0,450,1000,col=>v.nameWidth+col*v.charWidth-v.scrollX);
assert.equal(labels.find(l=>l.text==='Visible gene').x,900);
console.log('Linear labels: high-zoom gene start uses the visible exon midpoint');

// Explicit fits leave the selection intact, including a range across the origin.
{
 const v = Object.create(SeqView.prototype);
 Object.defineProperty(v, 'seqWidth', {value: 800});
 Object.assign(v, {length: 10000, circularMap: false, selection: {c0:2000,c1:2999,r0:0,r1:0},
   readSel:null,readCaret:null, minCharWidth:()=>.079, navigate(target, animate, anchor){this.target=target;this.anchor=anchor;}, scrollToRow(){}});
 v.fitSelection();
 assert(v.target.charWidth > .7 && v.target.charWidth < .8);
 assert.equal(v.anchor,2500,'Fit selection uses the stable camera anchor');
 assert.equal(v.selection.c0,2000);
 v.fitWhole(); assert.equal(v.target.charWidth,.079); assert.equal(v.target.scrollY,0);
 v.circularMap=true;v.width=800;v.circGeometry=()=>({base:200});
 v.selection={c0:9900,c1:99,wraps:true,r0:0,r1:0};v.fitSelection();
 assert(v.target.circZoom>1);assert.equal(v.target.rotation,0);
 v.fitWhole();assert.equal(v.target.circZoom,1);
 v.selection=null;v.readCaret={read:[4000,4500],col:4100};
 v.centerReadRow=()=>{};v.circularMap=false;v.fitSelection();assert.equal(v.anchor,4250.5);
}
