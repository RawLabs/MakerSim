import * as THREE from './vendor/three.module.js';
import { OrbitControls } from './vendor/OrbitControls.js';
import { STLLoader } from './vendor/STLLoader.js';

const BLUE = new THREE.Color('#9FB3C8');
const HOLD = new THREE.Color('#5DE3B3');
const PULL = new THREE.Color('#FFB36A');
const HEAT = ['#3769e8','#2aa8f1','#33c7b4','#ead861','#f69b47','#ef5c52'].map(c=>new THREE.Color(c));
const v3 = (a) => new THREE.Vector3(...a);

function refineGeometry(geometry, target) {
  let positions = geometry.attributes.position.array;
  // Subdivide long triangles for a readable field on sparsely triangulated STLs.
  for (let pass=0;pass<7;pass++) {
    if (positions.length/9 > 100000) break;
    const next=[]; let changed=false;
    for(let i=0;i<positions.length;i+=9) {
      const a=Array.from(positions.slice(i,i+3)),b=Array.from(positions.slice(i+3,i+6)),c=Array.from(positions.slice(i+6,i+9));
      const points=[a,b,c];
      const lengths=[[0,1],[1,2],[2,0]].map(([u,w])=>points[u].reduce((s,x,k)=>s+(x-points[w][k])**2,0));
      const longest=lengths.indexOf(Math.max(...lengths));
      if(lengths[longest] > target*target) {
        const u=points[longest],w=points[(longest+1)%3],other=points[(longest+2)%3];
        const mid=u.map((x,k)=>(x+w[k])/2);
        next.push(...u,...mid,...other,...mid,...w,...other); changed=true;
      } else next.push(...a,...b,...c);
    }
    if(!changed) break;
    positions=new Float32Array(next);
  }
  geometry.dispose();
  const refined=new THREE.BufferGeometry();
  refined.setAttribute('position',new THREE.BufferAttribute(new Float32Array(positions),3));
  refined.computeVertexNormals();
  refined.computeBoundingBox();refined.computeBoundingSphere();
  return refined;
}

export class PartViewer {
  constructor(container, callbacks) {
    this.container=container; this.callbacks=callbacks;
    this.mode='orbit';this.radius=6;this.fixtures=[];this.load=null;
    this.direction=[0,0,-1];this.result=null;this.heatmap=true;this.deform=false;
    this.busy=false;this.span=100;this.pointerDown=false;this.dragStart=null;
    this.scene=new THREE.Scene();
    this.scene.background=new THREE.Color('#0F172A');
    this.camera=new THREE.PerspectiveCamera(38,1,.01,10000);
    this.camera.up.set(0,0,1); this.camera.position.set(100,-160,120);
    this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:false});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));
    this.renderer.outputColorSpace=THREE.SRGBColorSpace;
    this.renderer.toneMapping=THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure=1.2;
    this.renderer.domElement.setAttribute('aria-label','3D part viewer. Use the Hold here or Pull here tool to select areas.');
    this.renderer.domElement.setAttribute('role','img');
    container.prepend(this.renderer.domElement);
    this.controls=new OrbitControls(this.camera,this.renderer.domElement);
    this.controls.enableDamping=true;this.controls.dampingFactor=.075;
    this.controls.autoRotateSpeed=.6;
    this.scene.add(new THREE.HemisphereLight('#dfebff','#293247',2.8));
    const key=new THREE.DirectionalLight('#ffffff',3.3);key.position.set(-80,-120,180);this.scene.add(key);
    const rim=new THREE.DirectionalLight('#729aff',1.5);rim.position.set(100,100,70);this.scene.add(rim);
    this.markers=new THREE.Group();this.scene.add(this.markers);
    this.raycaster=new THREE.Raycaster();this.pointer=new THREE.Vector2();
    this.brush=new THREE.Mesh(new THREE.RingGeometry(.88,1,64),new THREE.MeshBasicMaterial({color:HOLD,transparent:true,opacity:.8,side:THREE.DoubleSide,depthTest:false}));
    this.brush.visible=false;this.brush.renderOrder=20;this.scene.add(this.brush);
    this.makeGrid(100,-30);
    this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(container);
    const canvas=this.renderer.domElement;
    canvas.addEventListener('pointerdown',e=>this.onDown(e),{capture:true});
    canvas.addEventListener('pointermove',e=>this.onMove(e));
    canvas.addEventListener('pointerup',e=>this.onUp(e));
    canvas.addEventListener('pointercancel',()=>this.endDrag());
    canvas.addEventListener('pointerleave',()=>{if(!this.pointerDown)this.brush.visible=false;});
    canvas.addEventListener('contextmenu',e=>e.preventDefault());
    this.resize();this.animate();
  }
  resize() {
    const {width,height}=this.container.getBoundingClientRect();
    this.renderer.setSize(width,height,false);this.camera.aspect=width/height;this.camera.updateProjectionMatrix();
  }
  makeGrid(size,z) {
    if(this.grid){this.scene.remove(this.grid);this.grid.geometry.dispose();this.grid.material.dispose();}
    this.grid=new THREE.GridHelper(size*2.8,28,'#344561','#22314a');
    this.grid.rotation.x=Math.PI/2;this.grid.position.z=z;
    this.grid.material.transparent=true;this.grid.material.opacity=.55;
    this.scene.add(this.grid);
  }
  animate() {
    this.animation=requestAnimationFrame(()=>this.animate());
    this.controls.update();this.renderer.render(this.scene,this.camera);
  }
  async setFile(buffer,unit='mm') {
    let geometry=new STLLoader().parse(buffer);
    const raw=geometry.attributes.position.array;
    for(const value of raw)if(!Number.isFinite(value)){geometry.dispose();throw new Error('The STL contains invalid coordinates.');}
    if(!raw.length){geometry.dispose();throw new Error('The STL contains no triangles.');}
    if(unit==='inch')geometry.scale(25.4,25.4,25.4);
    geometry.computeBoundingBox();const center=geometry.boundingBox.getCenter(new THREE.Vector3());
    geometry.translate(-center.x,-center.y,-center.z);
    geometry.computeBoundingBox();const dimensions=geometry.boundingBox.getSize(new THREE.Vector3());
    const span=Math.max(...dimensions.toArray());
    geometry=refineGeometry(geometry,span/24);
    if(this.part){this.scene.remove(this.part);this.part.geometry.dispose();this.part.material.dispose();}
    this.part=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color:BLUE,roughness:.65,metalness:.08,side:THREE.DoubleSide}));
    this.scene.add(this.part);this.span=span;
    this.original=new Float32Array(geometry.attributes.position.array);
    this.colors=new Float32Array(this.original.length);
    geometry.setAttribute('color',new THREE.BufferAttribute(this.colors,3));
    this.fixtures=[];this.load=null;this.result=null;this.displacements=null;this.stress=null;
    this.makeGrid(span,geometry.boundingBox.min.z-span*.045);this.fit();this.redrawMarkers();this.recolor();
    return dimensions.toArray();
  }
  fit() {
    const radius=this.part?.geometry.boundingSphere?.radius || 60;
    const horizontal=2*Math.atan(Math.tan(THREE.MathUtils.degToRad(this.camera.fov/2))*this.camera.aspect);
    const angle=Math.min(horizontal,THREE.MathUtils.degToRad(this.camera.fov));
    const distance=radius/Math.sin(angle/2)*1.22;
    this.camera.position.copy(new THREE.Vector3(.8,-1.65,1.05).normalize().multiplyScalar(distance));
    this.controls.target.set(0,0,0);this.camera.near=Math.max(.001,radius/500);this.camera.far=radius*100;
    this.camera.updateProjectionMatrix();this.controls.update();
  }
  setMode(mode) {
    this.mode=mode;this.controls.autoRotate=false;this.controls.enabled=mode==='orbit';
    this.renderer.domElement.style.cursor=mode==='orbit'?'grab':'crosshair';this.brush.visible=false;
    if(this.result&&mode!=='orbit')this.setDeformation(false);
  }
  setRadius(radius) {this.radius=radius;this.brush.scale.setScalar(radius);}
  setBusy(busy) {this.busy=busy;this.controls.enabled=!busy&&this.mode==='orbit';}
  hit(event) {
    const rect=this.renderer.domElement.getBoundingClientRect();
    this.pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);
    this.raycaster.setFromCamera(this.pointer,this.camera);
    return this.part?this.raycaster.intersectObject(this.part,false)[0]:null;
  }
  patch(hit) {return {point:hit.point.toArray(),normal:hit.face.normal.clone().transformDirection(this.part.matrixWorld).toArray(),radius:this.radius};}
  onDown(e) {
    if(e.button!==0||this.busy||!this.part||this.mode==='orbit')return;
    e.preventDefault();e.stopPropagation();
    const hit=this.hit(e);if(!hit)return;
    this.pointerDown=true;this.renderer.domElement.setPointerCapture(e.pointerId);
    this.callbacks.edit();
    if(this.mode==='hold')this.paint(hit);
    if(this.mode==='pull') {
      this.load=this.patch(hit);this.dragStart={x:e.clientX,y:e.clientY};
      const origin=v3(this.load.point);
      const normal=this.camera.getWorldDirection(new THREE.Vector3());
      this.dragPlane=new THREE.Plane().setFromNormalAndCoplanarPoint(normal,origin);
      this.redrawMarkers();this.callbacks.selection();
    }
  }
  paint(hit) {
    if(this.fixtures.length>=160)return;
    const patch=this.patch(hit);
    if(this.fixtures.some(p=>v3(p.point).distanceTo(hit.point)<this.radius*.55&&v3(p.normal).dot(v3(patch.normal))>.8))return;
    this.fixtures.push(patch);this.recolor();this.redrawMarkers();this.callbacks.selection();
  }
  onMove(e) {
    if(this.busy||this.mode==='orbit'||!this.part)return;
    const hit=this.hit(e);
    if(hit) {
      this.brush.visible=true;this.brush.position.copy(hit.point);
      const normal=hit.face.normal.clone().transformDirection(this.part.matrixWorld);
      this.brush.position.addScaledVector(normal,this.span*.002);
      this.brush.quaternion.setFromUnitVectors(new THREE.Vector3(0,0,1),normal);
      this.brush.scale.setScalar(this.radius);this.brush.material.color.copy(this.mode==='hold'?HOLD:PULL);
    } else this.brush.visible=false;
    if(!this.pointerDown)return;
    if(this.mode==='hold'&&hit)this.paint(hit);
    if(this.mode==='pull'&&this.dragPlane&&Math.hypot(e.clientX-this.dragStart.x,e.clientY-this.dragStart.y)>5) {
      const end=new THREE.Vector3();
      if(this.raycaster.ray.intersectPlane(this.dragPlane,end)) {
        const dir=end.sub(v3(this.load.point));
        if(dir.length()>this.span*.005){this.direction=dir.normalize().toArray();this.redrawMarkers();this.callbacks.direction(this.direction);}
      }
    }
  }
  onUp() {if(this.pointerDown){this.endDrag();this.callbacks.selection();}}
  endDrag() {this.pointerDown=false;this.dragPlane=null;this.dragStart=null;}
  setDirection(direction) {this.direction=v3(direction).normalize().toArray();this.redrawMarkers();}
  clearSelections() {this.fixtures=[];this.load=null;this.clearResult();this.redrawMarkers();this.callbacks.selection();}
  undoHold() {this.fixtures.pop();this.clearResult();this.redrawMarkers();this.callbacks.selection();}
  redrawMarkers() {
    for(const child of [...this.markers.children]) {
      this.markers.remove(child);child.traverse(o=>{o.geometry?.dispose();if(o.material)Array.isArray(o.material)?o.material.forEach(m=>m.dispose()):o.material.dispose();});
    }
    for(const patch of this.fixtures) {
      const marker=new THREE.Mesh(new THREE.SphereGeometry(this.span*.012,10,8),new THREE.MeshBasicMaterial({color:HOLD,depthTest:false}));
      marker.position.copy(v3(patch.point));marker.renderOrder=12;this.markers.add(marker);
    }
    if(this.load) {
      const origin=v3(this.load.point),normal=v3(this.load.normal);
      const ring=new THREE.Mesh(new THREE.RingGeometry(this.load.radius*.88,this.load.radius,48),new THREE.MeshBasicMaterial({color:PULL,side:THREE.DoubleSide,depthTest:false}));
      ring.position.copy(origin).addScaledVector(normal,this.span*.003);
      ring.quaternion.setFromUnitVectors(new THREE.Vector3(0,0,1),normal);ring.renderOrder=15;this.markers.add(ring);
      const arrow=new THREE.ArrowHelper(v3(this.direction),origin,this.span*.4,PULL,this.span*.075,this.span*.047);
      arrow.line.material.depthTest=false;arrow.cone.material.depthTest=false;
      arrow.line.renderOrder=18;arrow.cone.renderOrder=18;this.markers.add(arrow);
      const center=new THREE.Mesh(new THREE.SphereGeometry(this.span*.018,12,10),new THREE.MeshBasicMaterial({color:PULL,depthTest:false}));
      center.position.copy(origin);center.renderOrder=19;this.markers.add(center);
    }
  }
  recolor() {
    if(!this.part)return;
    const position=new THREE.Vector3();
    for(let i=0;i<this.original.length;i+=3) {
      position.fromArray(this.original,i);
      let color=BLUE;
      if(this.result&&this.heatmap) {
        const value=Math.min(1,Math.max(0,this.stress[i/3]/this.result.heatmap_scale_mpa));
        const index=value*(HEAT.length-1),lo=Math.floor(index);
        color=HEAT[lo].clone().lerp(HEAT[Math.min(lo+1,HEAT.length-1)],index-lo);
      } else {
        const inPatch=(patch)=>{
          const delta=position.clone().sub(v3(patch.point)),normal=v3(patch.normal).normalize();
          const depth=delta.dot(normal);
          return Math.abs(depth)<this.span*.008&&delta.addScaledVector(normal,-depth).length()<=patch.radius;
        };
        if(this.fixtures.some(inPatch))color=HOLD;
        if(this.load&&inPatch(this.load))color=PULL;
      }
      color.toArray(this.colors,i);
    }
    this.part.material.color.set('#ffffff');this.part.material.vertexColors=true;
    this.part.geometry.attributes.color.needsUpdate=true;
  }
  setResult(result) {
    this.result=result;
    const pitch=result.mesh.cell_mm;
    const buckets=new Map();
    const key=(x,y,z)=>`${x},${y},${z}`;
    result.positions.forEach((p,i)=>{const k=key(...p.map(x=>Math.floor(x/pitch)));if(!buckets.has(k))buckets.set(k,[]);buckets.get(k).push(i);});
    this.stress=new Float32Array(this.original.length/3);this.displacements=new Float32Array(this.original.length);
    for(let offset=0;offset<this.original.length;offset+=3) {
      const p=Array.from(this.original.slice(offset,offset+3));const cell=p.map(x=>Math.floor(x/pitch));
      let candidates=[];
      for(let radius=1;radius<=3&&!candidates.length;radius++) {
        for(let x=-radius;x<=radius;x++)for(let y=-radius;y<=radius;y++)for(let z=-radius;z<=radius;z++) {
          const list=buckets.get(key(cell[0]+x,cell[1]+y,cell[2]+z));if(list)candidates.push(...list);
        }
      }
      if(!candidates.length)candidates=Array.from({length:result.positions.length},(_,i)=>i);
      const near=candidates.map(i=>({i,d:result.positions[i].reduce((s,x,k)=>s+(x-p[k])**2,0)})).sort((a,b)=>a.d-b.d).slice(0,4);
      let total=0;
      for(const {i,d} of near) {
        const weight=1/Math.max(d,(pitch*.15)**2);total+=weight;
        this.stress[offset/3]+=result.stress[i]*weight;
        for(let k=0;k<3;k++)this.displacements[offset+k]+=result.displacement[i][k]*weight;
      }
      this.stress[offset/3]/=total;for(let k=0;k<3;k++)this.displacements[offset+k]/=total;
    }
    this.heatmap=true;this.deform=false;this.recolor();
  }
  setDeformation(enabled) {
    this.deform=enabled&&!!this.result;
    if(!this.part)return;
    const factor=this.deform?this.deformationFactor():0;
    const array=this.part.geometry.attributes.position.array;
    for(let i=0;i<array.length;i++)array[i]=this.original[i]+(this.displacements?.[i]||0)*factor;
    this.part.geometry.attributes.position.needsUpdate=true;this.part.geometry.computeVertexNormals();
    this.part.geometry.computeBoundingSphere();
    this.markers.visible=!this.deform;
  }
  deformationFactor() {return Math.min(10000,this.span*.065/Math.max(this.result?.max_displacement_mm||0,1e-9));}
  clearResult() {this.setDeformation(false);this.result=null;this.stress=null;this.displacements=null;this.recolor();}
  setExampleSelections() {
    // The bundled profile is centred at [25.5,0,25] mm.
    this.fixtures=[{point:[-25.5,-8,-7],normal:[0,-1,0],radius:7},{point:[-25.5,-8,14],normal:[0,-1,0],radius:7}];
    this.load={point:[32.5,0,-17],normal:[0,0,1],radius:6};this.direction=[0,0,-1];
    this.redrawMarkers();this.recolor();this.callbacks.selection();
  }
  dispose() {
    cancelAnimationFrame(this.animation);this.resizeObserver.disconnect();this.controls.dispose();
    this.scene.traverse(o=>{o.geometry?.dispose();o.material?.dispose?.();});this.renderer.dispose();
  }
}
