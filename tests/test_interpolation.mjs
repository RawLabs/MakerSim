import assert from 'node:assert/strict';
import { createSurfaceSampler } from '../frontend/src/surface-interpolation.js';

// Opposed, disconnected sheets closer than a mesh pitch: nearest-node averaging
// would transfer movement from the upper sheet into the held lower one.
const result={mesh:{cell_mm:1},positions:[[0,0,0],[1,0,0],[0,1,0],[0,0,.2],[0,1,.2],[1,0,.2]],
  surface_triangles:[[0,1,2],[3,4,5]],stress:[1,2,3,100,200,300],
  displacement:[[0,0,0],[1,0,0],[0,1,0],[0,0,20],[0,0,20],[0,0,20]]};
const sample=createSurfaceSampler(result);
const value=(p,n,field)=>{const {indices,weights}=sample(p,n);return indices.reduce((v,i,k)=>v+field[i]*weights[k],0);};
assert.equal(value([0,0,0],[0,0,1],result.stress),1,'A surface node reproduces its exact field');
assert.equal(value([0,0,0],[0,0,1],result.displacement.map(p=>p[2])),0,'A fixed node must remain fixed');
assert.equal(value([0,0,0],[0,0,-1],result.stress),1,'Exact nodes remain exact even with an opposing query normal');
assert.ok(Number.isFinite(value([.2,.2,.1],[0,0,0],result.stress)),'A zero average normal leaves the nearest face eligible');
assert.ok(Math.abs(value([.25,.25,.12],[0,0,1],result.stress)-1.75)<1e-12,'Normal matching must not jump across the narrow gap');
assert.equal(value([.25,.25,.08],[0,0,-1],result.displacement.map(p=>p[2])),20);
const field=[0,1,0,0,0,1];
assert.ok(Math.abs(value([.2,.3,.01],[0,0,1],field)-.2)<1e-12,'Barycentric mapping preserves a linear field');
assert.throws(()=>sample([0,0,10],[0,0,1]),/cannot map/,'Unresolved features must not borrow a distant field');
const legacy=createSurfaceSampler({...result,surface_triangles:undefined});
assert.deepEqual(legacy([0,0,0]),{indices:[0],weights:[1]},'Older reports also retain exact nodal values');
assert.throws(()=>createSurfaceSampler({...result,mesh:{cell_mm:0}}),/invalid surface fields/);
assert.throws(()=>createSurfaceSampler({...result,mesh:{cell_mm:1e-12}}),/invalid surface fields/,'Corrupt pitch must not create an enormous search grid');
assert.throws(()=>createSurfaceSampler({...result,surface_triangles:[[0,1,99]]}),/invalid surface fields/);

const quadPositions=[[0,0,0],[2,0,0],[2,0,3],[0,0,3]],quadValues=quadPositions.map(([x,,z])=>x*z);
const quadResult={mesh:{cell_mm:3},positions:quadPositions,surface_quads:[[0,1,2,3]],surface_triangles:[[0,1,2],[0,2,3]]};
const quadSampler=createSurfaceSampler(quadResult);
const quadValue=(p,n,values=quadValues,sampler=quadSampler)=>{
  const {indices,weights}=sampler(p,n);return indices.reduce((sum,i,k)=>sum+values[i]*weights[k],0);
};
assert.equal(quadValue([1,0,1.5],[0,-1,0]),1.5,'Hex faces preserve bilinear x*z rather than a triangle diagonal');
assert.equal(quadValue([.5,-.1,.75],[0,-1,0]),.375,'Bilinear weights use the face projection');
assert.equal(quadValue([3,0,1.5],[0,-1,0]),3,'Projection clips to a rectangular edge');
assert.deepEqual(quadSampler([0,0,0],[0,1,0]),{indices:[0],weights:[1]},'Quad reports preserve exact held nodes');
const triangleSampler=createSurfaceSampler({...quadResult,surface_quads:undefined});
assert.equal(quadValue([1,0,1.5],[0,-1,0],quadValues,triangleSampler),3,'Old triangular reports retain their interpolation');
const opposedQuads={...quadResult,positions:[...quadPositions,...quadPositions.map(([x,,z])=>[x,.2,z])],surface_quads:[[0,1,2,3],[4,7,6,5]]};
const gapSampler=createSurfaceSampler(opposedQuads),gapValues=[...quadValues,100,100,100,100];
assert.equal(quadValue([1,.15,1.5],[0,-1,0],gapValues,gapSampler),1.5,'Quad fields must not blend across a narrow gap');
assert.equal(quadValue([1,.05,1.5],[0,1,0],gapValues,gapSampler),100);
assert.throws(()=>quadSampler([1,-20,1.5],[0,-1,0]),/cannot map/);
for(const surface_quads of [[],[[0,1,2]],[[0,1,2,99]],[[0,2,1,3]],[[0,0,2,3]]])
  assert.throws(()=>createSurfaceSampler({...quadResult,surface_quads}),/invalid surface fields/);
assert.throws(()=>createSurfaceSampler({...quadResult,positions:[[0,0,0],[2,0,0],[3,0,3],[0,0,3]]}),/invalid surface fields/,'Skewed quad geometry is not a rectangular hex face');
assert.throws(()=>createSurfaceSampler({...quadResult,positions:[[0,0,0],[2,0,0],[2,.1,3],[0,0,3]]}),/invalid surface fields/,'Nonplanar quads must be rejected');
console.log('Surface interpolation checks passed: bilinear quads, fixed nodes, thin gaps, linear triangles, distance bounds and legacy reports.');
