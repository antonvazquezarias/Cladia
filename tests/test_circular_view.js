// Run with node tests/test_circular_view.js.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function SeqView() {}
SeqView.looks = {};
vm.runInNewContext(fs.readFileSync('cladia/static/seqview-circular.js','utf8'), {window:{SeqView}});
const paths=[];
let current=[];
const view=Object.create(SeqView.prototype);
view.width=800;
view.ctx={beginPath(){paths.length=0;current=[];},moveTo(x,y){current=[[x,y]];},lineTo(x,y){current.push([x,y]);},closePath(){paths.push(current);},arc(){}};
const tau=2*Math.PI;
function draw(a,b,point=0,r=6000){
  paths.length=0;
  view.arcBand({cx:400,cy:r+30},a,b,r-14,r,point,0.002);
  return paths.map(p=>p.map(v=>v.slice()));
}
// Both ends of a long annotation are visible on opposite sides of the origin.
for(const point of [0,1,-1]){
 const p=draw(0.02,tau-0.02,point);
 assert.equal(p.length,2,'both visible portions must survive high-zoom clipping');
 assert(p.some(part=>part.some(([x])=>x<400)));
 assert(p.some(part=>part.some(([x])=>x>400)));
}
const expected=draw(0.02,tau-0.02);
for(const turn of [-3,-1,1,3]){
 const p=draw(0.02+turn*tau,tau-0.02+turn*tau);
 assert.equal(p.length,expected.length);
 for(let i=0;i<p.length;i++){
  assert.equal(p[i].length,expected[i].length);
  for(let j=0;j<p[i].length;j++)for(let k=0;k<2;k++)assert(Math.abs(p[i][j][k]-expected[i][j][k])<1e-6);
 }
}
assert.equal(draw(0.4,1).length,0);
assert.equal(draw(-0.02,0.02).length,1);
console.log('Circular annotations: both origin edges, strands, rotations and offscreen clipping passed.');
// Long features remain visible when scrolling into their middle, even when
// neither endpoint is on screen and the feature covers more than half a turn.
view.length=1000;
assert(view.inView({from:490,to:510},100,900));
assert(view.inView({from:1490,to:1510},100,900));
assert(view.inView({from:-510,to:-490},100,900));
assert(!view.inView({from:950,to:970},100,900));
assert(view.inView({from:-10,to:10},900,1100));
assert(view.inView({from:-10,to:10},900,100));
console.log('Circular annotation visibility: long interiors and wrapped intervals remain visible while scrolling.');

// Zoom approaches the clicked base by the shortest turn, never a snap to the top.
Object.assign(view,{circGeometry:()=>({base:100}),circZoom:1,rotation:950,caret:50,selection:null,draw:()=>{}});
view.navigate=target=>Object.assign(view,target);
view.circularZoom(1.25);assert.equal(view.rotation,50);
view.rotation=50;view.caret=950;view.circularZoom(1.25);assert.equal(view.rotation,950);
view.circZoom=1;view.rotation=500;view.circularZoom(.8);assert.equal(view.rotation,500,'zoom limit must not rotate the view');
console.log('Circular zoom: exact anchor targets and stable limits passed');

// Labels occupy the visible arc, including when zoom is anchored at its start.
Object.assign(view,{length:1000,rotation:100,height:600,width:800});
const geometry={cx:400,cy:6020};
let visible=view.visibleLabelArc(geometry,[{start:100,end:800}],6000);
assert(visible && visible.start>=0 && visible.end>visible.start);
assert((visible.start+visible.end)/2>0,'gene-start zoom must place the label inside the visible gene');
for(const p of [{start:100,end:800},{start:0,end:100},{start:0,end:999}]) {
 const arc=view.visibleLabelArc(geometry,[p],6000);assert(arc);
 for(let i=0;i<=20;i++) {
  const [x,y]=view.pointAt(geometry,arc.start+(arc.end-arc.start)*i/20,6000);
  assert(x>=9-1e-6 && x<=791+1e-6 && y>=9-1e-6 && y<=539+1e-6);
 }
}
// Choose visible length rather than the longest complete exon off-screen.
visible=view.visibleLabelArc(geometry,[{start:100,end:101},{start:94,end:99}],6000);
assert(visible && visible.start<0,'the wider visible exon should carry the label');
// A fully visible circular map keeps the normal feature midpoint.
view.rotation=0;
visible=view.visibleLabelArc({cx:400,cy:270},[{start:100,end:200}],150);
assert(Math.abs((visible.start+visible.end)/2-view.angleOf(150.5))<1e-9);
console.log('Circular labels: visible arc centres, start/end anchors, exon choice and viewport bounds passed');

// Base 1 is a regular coordinate label, independent of the chosen tick interval.
const originText=[];
view.ctx={beginPath(){},moveTo(){},lineTo(){},stroke(){}};
view.textAt=(g,a,r,text)=>originText.push({a,r,text});
for(const rotation of [0,250,500,750]) {
 originText.length=0;view.rotation=rotation;view.drawCircTicks({cx:400,cy:270,radius:150},null);
 const label=originText.find(l=>l.text==='1');assert(label);
 assert.equal(originText.filter(l=>l.text==='1').length,1);
 assert.equal(label.a,view.angleOf(.5));assert.equal(label.r,134);
 assert.equal(view.ctx.fillStyle,'#555');assert(!view.ctx.font.includes('bold'));
}
originText.length=0;view.rotation=500;
view.drawCircTicks({cx:400,cy:6020,radius:6000},{from:450,to:550});
assert(!originText.some(l=>l.text==='1'));
console.log('Circular origin: plain base-1 tick always included, styled consistently and not duplicated');
