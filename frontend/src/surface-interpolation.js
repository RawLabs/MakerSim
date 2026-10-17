import * as THREE from './vendor/three.module.js';

// Project onto one connected boundary face. Mixing nearby surface nodes
// can move a clamped node and blend unrelated sides of a narrow air gap.
export function createSurfaceSampler(result) {
  const pitch=result.mesh.cell_mm,positions=result.positions;
  const quads=result.surface_quads!=null,faces=quads?result.surface_quads:result.surface_triangles;
  const invalid=()=>new Error('This report has invalid surface fields. Run the simulation again.');
  if(!(Number.isFinite(pitch)&&pitch>0)||!Array.isArray(positions)||!positions.length
    ||positions.some(p=>!Array.isArray(p)||p.length!==3||!p.every(Number.isFinite)))throw invalid();
  if(faces!=null&&(!Array.isArray(faces)||!faces.length||faces.some(ids=>!Array.isArray(ids)||ids.length!==(quads?4:3)
    ||ids.some(i=>!Number.isInteger(i)||i<0||i>=positions.length))))throw invalid();
  const key=(x,y,z)=>`${x},${y},${z}`;
  const exactNodes=new Map(positions.map((p,i)=>[key(...p),i]));
  const buckets=new Map(),neighborhoods=new Map();
  const point=new THREE.Vector3(),nearest=new THREE.Vector3(),barycentric=new THREE.Vector3(),delta=new THREE.Vector3();
  const surfaces=faces?.map(ids=>{
    if(quads) {
      const [origin,b,c,d]=ids.map(i=>new THREE.Vector3(...positions[i]));
      const u=b.sub(origin),v=d.sub(origin),uu=u.lengthSq(),vv=v.lengthSq();
      const size=Math.sqrt(Math.max(uu,vv));
      if(!(Number.isFinite(uu)&&Number.isFinite(vv)&&uu>0&&vv>0)
        ||Math.abs(u.dot(v))>1e-8*Math.sqrt(uu*vv)
        ||c.distanceTo(origin.clone().add(u).add(v))>1e-8*size)throw invalid();
      return {ids,origin,u,v,uu,vv,normal:new THREE.Vector3().crossVectors(u,v).normalize()};
    }
    const triangle=new THREE.Triangle(...ids.map(i=>new THREE.Vector3(...positions[i])));
    if(!(triangle.getArea()>0))throw invalid();
    return {ids,triangle,normal:triangle.getNormal(new THREE.Vector3())};
  });
  if(surfaces) {
    surfaces.forEach(({ids},index)=>{
      const corners=ids.map(i=>positions[i]);
      const low=[0,1,2].map(k=>Math.floor(Math.min(...corners.map(p=>p[k]))/pitch));
      const high=[0,1,2].map(k=>Math.floor(Math.max(...corners.map(p=>p[k]))/pitch));
      // A solver boundary face occupies at most a few adjacent buckets. Reject
      // corrupt report dimensions before an unbounded grid allocation.
      if(high.reduce((count,v,k)=>count*(v-low[k]+1),1)>64||!low.every(Number.isSafeInteger)||!high.every(Number.isSafeInteger))throw invalid();
      for(let x=low[0];x<=high[0];x++)for(let y=low[1];y<=high[1];y++)for(let z=low[2];z<=high[2];z++) {
        const cell=key(x,y,z);if(!buckets.has(cell))buckets.set(cell,[]);buckets.get(cell).push(index);
      }
    });
  } else {
    positions.forEach((p,i)=>{const cell=key(...p.map(x=>Math.floor(x/pitch)));if(!buckets.has(cell))buckets.set(cell,[]);buckets.get(cell).push(i);});
  }
  function candidates(cell,radius) {
    const cacheKey=`${key(...cell)}:${radius}`;
    if(!neighborhoods.has(cacheKey)) {
      const ids=new Set();
      for(let x=-radius;x<=radius;x++)for(let y=-radius;y<=radius;y++)for(let z=-radius;z<=radius;z++) {
        const list=buckets.get(key(cell[0]+x,cell[1]+y,cell[2]+z));if(list)for(const i of list)ids.add(i);
      }
      neighborhoods.set(cacheKey,[...ids].sort((a,b)=>a-b));
    }
    return neighborhoods.get(cacheKey);
  }
  return (p,normal)=>{
    const exact=exactNodes.get(key(...p));
    if(exact!==undefined)return {indices:[exact],weights:[1]};
    const normalLength=normal?Math.hypot(...normal):0;
    const cell=p.map(x=>Math.floor(x/pitch));point.fromArray(p);
    if(surfaces) {
      let best=Infinity,answer=null;
      for(let radius=1;radius<=3;radius++) {
        for(const i of candidates(cell,radius)) {
          const face=surfaces[i],{ids,triangle,normal:outward}=face;
          if(normalLength>1e-8&&outward.x*normal[0]+outward.y*normal[1]+outward.z*normal[2]<=.2*normalLength)continue;
          let u,v;
          if(quads) {
            delta.subVectors(point,face.origin);
            u=Math.max(0,Math.min(1,delta.dot(face.u)/face.uu));
            v=Math.max(0,Math.min(1,delta.dot(face.v)/face.vv));
            nearest.copy(face.origin).addScaledVector(face.u,u).addScaledVector(face.v,v);
          } else triangle.closestPointToPoint(point,nearest);
          const distance=nearest.distanceToSquared(point);
          if(distance>=best)continue;
          best=distance;
          if(quads)answer={indices:ids,weights:[(1-u)*(1-v),u*(1-v),u*v,(1-u)*v]};
          else {triangle.getBarycoord(nearest,barycentric);answer={indices:ids,weights:barycentric.toArray()};}
        }
        // Every unvisited face lies outside these bucket boundaries.
        const unseen=Math.min(...p.flatMap((v,k)=>[v-(cell[k]-radius)*pitch,(cell[k]+radius+1)*pitch-v]));
        if(best<=unseen*unseen)break;
      }
      if(!answer||best>(1.8*pitch)**2)throw new Error('The quick mesh cannot map part of this surface. Try Fine detail or a thicker feature.');
      return answer;
    }
    // Older saved reports have no boundary connectivity. Preserve their display
    // while reproducing exact nodal values, including zero clamped movement.
    let near=[];
    for(let radius=1;radius<=3;radius++) {
      near=[];
      for(const i of candidates(cell,radius)) {
        const q=positions[i],d=(q[0]-p[0])**2+(q[1]-p[1])**2+(q[2]-p[2])**2;
        if(near.length===4&&d>=near[3].d)continue;
        let insert=near.findIndex(item=>d<item.d);if(insert<0)insert=near.length;
        near.splice(insert,0,{i,d});if(near.length>4)near.pop();
      }
      const unseen=Math.min(...p.flatMap((v,k)=>[v-(cell[k]-radius)*pitch,(cell[k]+radius+1)*pitch-v]));
      if(near.length===Math.min(4,positions.length)&&near.at(-1).d<=unseen*unseen)break;
      if(radius===3)near=positions.map((q,i)=>({i,d:q.reduce((sum,v,k)=>sum+(v-p[k])**2,0)})).sort((a,b)=>a.d-b.d||a.i-b.i).slice(0,4);
    }
    if(near[0].d<=(pitch*1e-7)**2)return {indices:[near[0].i],weights:[1]};
    const weights=near.map(({d})=>1/Math.max(d,(pitch*.15)**2)),total=weights.reduce((a,b)=>a+b,0);
    return {indices:near.map(({i})=>i),weights:weights.map(w=>w/total)};
  };
}
