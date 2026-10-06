import './hero.css';
import * as THREE from '../../frontend/src/vendor/three.module.js';
import { OrbitControls } from '../../frontend/src/vendor/OrbitControls.js';
import { positions, colors } from './part-data.js';

const mount=document.querySelector('#hero-viewer');
const reset=document.querySelector('#reset-preview');
const instruction=document.querySelector('#preview-instruction');

function initialize() {
  const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
  renderer.outputColorSpace=THREE.SRGBColorSpace;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure=1.15;
  const canvas=renderer.domElement;
  canvas.setAttribute('role','img');
  canvas.setAttribute('aria-label','3D bracket under a downward pull. Blue-to-red colours show relative stress, with warmer colours at the bend. Drag or use arrow keys to rotate; R resets the view.');
  canvas.tabIndex=0;
  mount.append(canvas);
  const scene=new THREE.Scene();
  const camera=new THREE.PerspectiveCamera(38,1,.1,1000);
  camera.up.set(0,0,1);
  const controls=new OrbitControls(camera,canvas);
  controls.enableDamping=true;
  controls.dampingFactor=.08;
  controls.enableZoom=false;
  controls.enablePan=false;
  controls.rotateSpeed=.65;
  controls.minPolarAngle=.3;
  controls.maxPolarAngle=Math.PI*.65;
  controls.target.set(0,0,-6);
  scene.add(new THREE.HemisphereLight('#e4efff','#202e47',2.5));
  const key=new THREE.DirectionalLight('#ffffff',3);
  key.position.set(-80,-120,180);
  scene.add(key);
  const rim=new THREE.DirectionalLight('#7da7ff',1.6);
  rim.position.set(90,60,90);
  scene.add(rim);
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));
  geometry.setAttribute('color',new THREE.BufferAttribute(colors,3));
  geometry.computeVertexNormals();
  scene.add(new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({vertexColors:true,roughness:.55,metalness:.08})));
  const grid=new THREE.GridHelper(138,18,'#34517b','#213753');
  grid.rotation.x=Math.PI/2;
  grid.position.z=-28.5;
  grid.material.transparent=true;
  grid.material.opacity=.34;
  scene.add(grid);
  for(const z of [-7,14]) {
    const ball=new THREE.Mesh(new THREE.SphereGeometry(1.05,14,10),new THREE.MeshBasicMaterial({color:'#5de3b3'}));
    ball.position.set(-25.5,-8.35,z);
    const ring=new THREE.Mesh(new THREE.RingGeometry(3.3,3.75,40),new THREE.MeshBasicMaterial({color:'#5de3b3',side:THREE.DoubleSide}));
    ring.rotation.x=Math.PI/2;
    ring.position.copy(ball.position);
    scene.add(ball,ring);
  }
  const pull=new THREE.Vector3(32.5,0,-16.8);
  const arrow=new THREE.ArrowHelper(new THREE.Vector3(0,0,-1),pull,23,'#ffb36a',5,3);
  const pullRing=new THREE.Mesh(new THREE.RingGeometry(5.3,6,48),new THREE.MeshBasicMaterial({color:'#ffb36a',side:THREE.DoubleSide}));
  pullRing.position.copy(pull);
  scene.add(arrow,pullRing);
  function fit() {
    const horizontal=2*Math.atan(Math.tan(THREE.MathUtils.degToRad(19))*camera.aspect);
    const angle=Math.min(horizontal,THREE.MathUtils.degToRad(38));
    camera.position.copy(new THREE.Vector3(.8,-1.65,1.05).normalize().multiplyScalar(48/Math.sin(angle/2)*1.07)).add(controls.target);
    controls.update();
  }
  function resize() {
    const {width,height}=mount.getBoundingClientRect();
    if(!width||!height)return;
    renderer.setSize(width,height,false);
    camera.aspect=width/height;
    camera.updateProjectionMatrix();
    fit();
    renderer.render(scene,camera);
  }
  reset.addEventListener('click',fit);
  canvas.addEventListener('keydown',event=>{
    if(event.key.toLowerCase()==='r')return fit();
    if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
    event.preventDefault();
    const offset=camera.position.clone().sub(controls.target);
    const spherical=new THREE.Spherical().setFromVector3(offset.applyAxisAngle(new THREE.Vector3(1,0,0),-Math.PI/2));
    spherical.theta+=event.key==='ArrowLeft'?-.12:event.key==='ArrowRight'?.12:0;
    spherical.phi=Math.max(.3,Math.min(Math.PI*.65,spherical.phi+(event.key==='ArrowUp'?-.1:event.key==='ArrowDown'?.1:0)));
    offset.setFromSpherical(spherical).applyAxisAngle(new THREE.Vector3(1,0,0),Math.PI/2);
    camera.position.copy(controls.target).add(offset);
    controls.update();
  });
  new ResizeObserver(resize).observe(mount);
  resize();
  mount.dataset.ready='true';
  reset.hidden=false;
  instruction.textContent='Drag to turn the part';
  let frame=0,visible=true;
  function requestRender() {
    if(!frame&&visible&&!document.hidden&&mount.dataset.ready==='true')frame=requestAnimationFrame(render);
  }
  function render() {
    frame=0;
    if(!visible||document.hidden||mount.dataset.ready!=='true')return;
    const moving=controls.update();
    renderer.render(scene,camera);
    if(moving)requestRender();
  }
  function update() {
    if(frame)cancelAnimationFrame(frame);
    frame=0;
    requestRender();
  }
  controls.addEventListener('change',requestRender);
  new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;update();}).observe(mount);
  document.addEventListener('visibilitychange',update);
  canvas.addEventListener('webglcontextlost',()=>{mount.dataset.ready='false';reset.hidden=true;instruction.textContent='Hold points + downward pull';update();});
  canvas.addEventListener('webglcontextrestored',()=>{mount.dataset.ready='true';reset.hidden=false;instruction.textContent='Drag to turn the part';update();});
  render();
}
try{initialize();}catch{ /* The static stress rendering remains available without WebGL. */ }
