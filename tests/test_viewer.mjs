import assert from 'node:assert/strict';
import { readFile,writeFile,mkdir } from 'node:fs/promises';
import * as THREE from '../frontend/src/vendor/three.module.js';
import { PartViewer } from '../frontend/src/viewer.js';

// Exercise actual geometry, raycasting, pointer handlers and colour mapping.
// Construction alone uses WebGL, unavailable in the execution sandbox.
const viewer=Object.create(PartViewer.prototype);
const rectangle={left:0,top:0,width:900,height:620};
const canvas={style:{},getBoundingClientRect:()=>rectangle,setPointerCapture:()=>{}};
let selections=0,edits=0,directions=0;
Object.assign(viewer,{
  renderer:{domElement:canvas},scene:new THREE.Scene(),markers:new THREE.Group(),
  camera:new THREE.PerspectiveCamera(38,900/620,.01,10000),
  raycaster:new THREE.Raycaster(),pointer:new THREE.Vector2(),
  brush:new THREE.Mesh(new THREE.RingGeometry(.88,1,32),new THREE.MeshBasicMaterial()),
  radius:6,mode:'orbit',fixtures:[],load:null,direction:[0,0,-1],span:100,heatmap:true,
  callbacks:{edit:()=>edits++,selection:()=>selections++,direction:()=>directions++},
});
viewer.camera.up.set(0,0,1);
viewer.controls={target:new THREE.Vector3(),update(){viewer.camera.lookAt(this.target);viewer.camera.updateMatrixWorld();}};
const data=await readFile(new URL('../backend/data/backpack-bracket.stl',import.meta.url));
const buffer=data.buffer.slice(data.byteOffset,data.byteOffset+data.byteLength);
const dimensions=await viewer.setFile(buffer);
console.log('STL loaded and fitted.');
assert.deepEqual(dimensions,[79,16,50]);
assert.ok(viewer.part.geometry.boundingBox.getCenter(new THREE.Vector3()).length()<1e-5);
assert.ok(viewer.part.geometry.attributes.position.count>data.readUInt32LE(80)*3);
const project=(point)=>{
  const p=new THREE.Vector3(...point).project(viewer.camera);
  return {clientX:(p.x+1)/2*rectangle.width,clientY:(1-p.y)/2*rectangle.height};
};
const event=(point)=>({...project(point),button:0,pointerId:1,preventDefault(){},stopPropagation(){}});
viewer.setMode('hold');
const held=event([-25.5,-8,0]);viewer.onDown(held);
viewer.onMove({...held,clientY:held.clientY-12});viewer.onUp();
assert.ok(viewer.fixtures.length>=1,'Surface painting must create a hold');
assert.ok(viewer.colors.some((v,i)=>Math.abs(v-[new THREE.Color('#9FB3C8').r,new THREE.Color('#9FB3C8').g,new THREE.Color('#9FB3C8').b][i%3])>1e-5));
viewer.setMode('pull');const pulled=event([32.5,-6,-17]);viewer.onDown(pulled);
viewer.onMove({...pulled,clientX:pulled.clientX+20,clientY:pulled.clientY+50});viewer.onUp();
assert.ok(viewer.load,'A surface click must create a load patch');
assert.ok(directions>0,'Dragging must change the arrow direction');
assert.ok(Math.abs(new THREE.Vector3(...viewer.direction).length()-1)<1e-8);
viewer.setDirection([0,0,-1]);
console.log('Painted holds and dragged a force arrow.');
const payload={model_id:'viewer-test',material_id:'generic-pla',fixtures:viewer.fixtures,load:viewer.load,direction:viewer.direction,magnitude:25,unit:'lbf'};
const qaDirectory=process.env.MAKERSIM_QA_DIRECTORY||'/tmp/makersim-qa';
await mkdir(qaDirectory,{recursive:true});
if(process.argv.includes('--prepare')) {
  await writeFile(`${qaDirectory}/request.json`,JSON.stringify(payload));
  console.log('Prepared a solver request from real surface interactions.');
  process.exit(0);
}
const result=JSON.parse(await readFile(`${qaDirectory}/result.json`,'utf8'));assert.ok(result.patches.loaded_nodes>=3);
console.log('Real solver returned a distributed-load field.');
viewer.setResult(result);
assert.ok(viewer.stress.every(Number.isFinite));assert.ok(viewer.displacements.every(Number.isFinite));
assert.ok(Math.max(...viewer.stress)>Math.min(...viewer.stress));
const before=new Float32Array(viewer.part.geometry.attributes.position.array);
viewer.setDeformation(true);assert.ok(viewer.deform);assert.equal(viewer.markers.visible,false);
assert.ok(viewer.part.geometry.attributes.position.array.some((v,i)=>Math.abs(v-before[i])>1e-5));
viewer.clearResult();assert.deepEqual(viewer.part.geometry.attributes.position.array,before);
assert.equal(viewer.markers.visible,true);assert.equal(viewer.result,null);
viewer.undoHold();viewer.clearSelections();assert.equal(viewer.fixtures.length,0);assert.equal(viewer.load,null);
await viewer.setFile(buffer,'inch');assert.ok(Math.abs(viewer.span-79*25.4)<1e-3);
assert.ok(selections>2&&edits>1);
console.log('Viewer interaction checks passed: STL fit, painting, arrow drag, real solver integration, heatmap, deformation, reset, inch scaling.');
