import './style.css';
import { icon } from './icons.js';
import { PartViewer } from './viewer.js';
import { getMaterials, uploadModel, simulate } from './api.js';
import brandArtwork from '../../artifacts/MakerSimLogos.png';

const $=(id)=>document.getElementById(id);
const state={model:null,file:null,materials:[],busy:false,result:null};
const unitFactors={N:1,lbf:4.4482216152605,kg:9.80665,stone:14*4.4482216152605};
const escape=(text)=>String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

$('app').innerHTML=`
  <header class="topbar">
    <a class="brand" href="/" aria-label="MakerSim home"><svg class="brand-mark" viewBox="79 742 161 153" aria-hidden="true" focusable="false"><image href="${brandArtwork}" width="1536" height="1024" /></svg><svg class="brand-wordmark" viewBox="96 508 675 80" aria-hidden="true" focusable="false"><image href="${brandArtwork}" width="1536" height="1024" /></svg><span class="version">PLAYGROUND</span></a>
    <div class="top-actions"><span class="local-status"><i></i> Local workspace</span><button id="help" class="quiet-button">${icon('help')} How it works</button></div>
  </header>
  <main class="workspace">
    <div class="intro"><div><div class="eyebrow">A LITTLE CURIOSITY. A BETTER PRINT.</div><h1>Follow the force<span>.</span></h1><p>Hold it here. Pull it there. See what your part is doing.</p></div><div class="intro-note">${icon('layers')} Made for makers.<br><span>Built for exploring.</span></div></div>
    <nav class="flow" aria-label="Simulation workflow">
      ${[['upload','Upload'],['print','Set print'],['hold','Hold here'],['pull','Pull here'],['run','Simulate']].map(([id,label],i)=>`<div class="flow-step ${i===0?'active':''}" id="step-${id}"><span class="flow-dot">${i+1}</span>${label}</div>${i<4?icon('arrow','flow-arrow'):''}`).join('')}
    </nav>
    <div class="workbench">
      <aside class="setup" aria-label="Part and load setup">
        <section class="card part-card" id="part-card" hidden>
          <div class="card-heading"><span class="step-number">01</span><h2>Your part</h2><span class="card-caption">STL</span></div>
          <div id="file-summary" class="file-summary" hidden><div class="file-icon">${icon('file')}</div><div><strong id="file-name"></strong><span id="file-dimensions"></span></div><button id="replace-file" class="icon-button" title="Replace STL" aria-label="Replace STL">${icon('upload')}</button></div>
          <div class="unit-row"><label for="stl-unit">STL units</label><select id="stl-unit"><option value="mm">Millimetres</option><option value="inch">Inches</option></select></div>
        </section>
        <section class="card print-card">
          <div class="card-heading"><span class="step-number">02</span><h2>Your print</h2>${icon('layers','heading-icon')}</div>
          <label class="field-label" for="material">Filament</label>
          <select id="material" class="full-select"><option>Loading material library…</option></select>
          <div class="library-note"><i></i><span id="material-basis">MakerSim material library</span></div>
          <div class="print-numbers">
            <label>Walls<input id="walls" type="number" min="1" max="20" step="1" value="3" /></label>
            <label>Top layers<input id="top-layers" type="number" min="0" max="30" step="1" value="4" /></label>
            <label>Bottom layers<input id="bottom-layers" type="number" min="0" max="30" step="1" value="4" /></label>
          </div>
          <div class="slider-label"><label for="infill">Infill</label><output id="infill-value">20<span>%</span></output></div>
          <input id="infill" type="range" min="0" max="100" step="5" value="20" />
          <div class="orientation-row"><label for="orientation">Print orientation</label><select id="orientation"><option value="z">Layers stack along Z</option><option value="x">Layers stack along X</option><option value="y">Layers stack along Y</option></select></div>
          <details class="material-details"><summary>Material & print assumptions ${icon('info')}</summary><div id="material-source"></div><p>0.45 mm extrusion width · 0.20 mm layers.<br>Walls, solid layers and infill modify effective stiffness. Balanced raster directions are assumed.</p></details>
        </section>
        <section class="card contact-card">
          <div class="card-heading"><span class="step-number">03</span><h2>Hold & pull</h2></div>
          <div class="tool-buttons" role="group" aria-label="Select interaction tool"><button data-mode="hold" id="hold-tool" class="hold-tool" disabled>${icon('hand')}Hold here</button><button data-mode="pull" id="pull-tool" class="pull-tool" disabled>${icon('pull')}Pull here</button></div>
          <p class="tool-description" id="tool-description">Upload a part to choose where to hold and pull.</p>
          <div class="slider-label"><label for="contact-size">Contact size <span class="muted">· new clicks</span></label><output id="contact-value">12<span> mm</span></output></div>
          <input id="contact-size" type="range" min="2" max="30" value="12" step="1" />
          <div class="selection-row"><span class="selection-chip"><i class="hold-dot"></i><span id="hold-count">No holds yet</span></span><span class="selection-chip"><i class="pull-dot"></i><span id="pull-count">No pull yet</span></span><button id="undo-hold" class="icon-button" aria-label="Undo last hold" title="Undo last hold" disabled>${icon('undo')}</button><button id="clear-selection" class="icon-button" aria-label="Clear held and pull areas" title="Clear selected areas" disabled>${icon('close')}</button></div>
        </section>
        <section class="card force-card">
          <div class="card-heading"><span class="step-number">04</span><h2>Add a little weight</h2></div>
          <div class="force-input"><input id="force" type="number" min="0.01" max="100000" step="any" value="25" aria-label="Force amount"/><select id="force-unit" aria-label="Force unit"><option value="lbf">lb / hanging pounds</option><option value="kg">kg / hanging mass</option><option value="N">Newtons</option><option value="stone">stone / hanging mass</option></select></div>
          <p class="force-equivalent" id="force-equivalent">About 111 N of pulling force</p>
          <div class="direction-label">Force direction <span id="direction-status">Down · −Z</span></div>
          <div class="direction-buttons" role="group" aria-label="Force direction">${[['down','Down'],['up','Up'],['left','−X'],['right','+X'],['forward','−Y'],['back','+Y']].map(([name,label])=>`<button data-direction="${name}" class="${name==='down'?'selected':''}">${label}</button>`).join('')}</div>
          <small class="force-hint">Or click the part and drag the arrow in any direction.</small>
        </section>
        <div class="run-area"><button id="simulate" class="simulate-button" disabled>${icon('play')}<span>Simulate</span>${icon('arrow')}</button><p id="run-hint">Add a part, a hold and a pull to get started.</p></div>
      </aside>
      <section class="viewer-panel" aria-label="3D workspace">
        <div class="viewer-top"><div class="viewer-title">${icon('cube')}<span id="viewer-name">Your 3D workspace</span><span class="viewer-badge" id="viewer-badge">READY TO EXPLORE</span></div><button class="viewer-icon" id="fit-view" aria-label="Fit model in view" title="Fit view">${icon('expand')}</button></div>
        <div class="viewport" id="viewport">
          <input type="file" id="stl-file" accept=".stl" class="visually-hidden" />
          <div class="empty-view" id="empty-view"><div class="empty-cube">${icon('cube')}</div><span class="empty-eyebrow">EVERY GOOD PRINT STARTS WITH A QUESTION</span><h2>What happens if<br>I pull <em>here?</em></h2><p>Bring in a part. Give it a nudge.<br>Make the invisible a little more visible.</p><button id="viewer-upload" class="primary-light">${icon('upload')}Upload your STL</button><small class="upload-limit">Drop an STL here · up to 20 MB · one solid part</small><button id="load-example" class="example-button">Try a backpack bracket ${icon('arrow')}</button></div>
          <div class="canvas-tools" id="canvas-tools"><button id="orbit-tool" class="selected" aria-label="Orbit model" title="Orbit / zoom / pan">${icon('mouse')}</button><span></span><button id="grid-toggle" class="selected" aria-label="Toggle grid" title="Toggle grid">${icon('grid')}</button><button id="rotate-toggle" aria-label="Toggle slow auto rotation" title="Slow auto rotation">${icon('rotate')}</button></div>
          <div id="interaction-tip" class="interaction-tip" hidden>${icon('hand')}<span></span></div>
          <div class="axes" aria-label="Model axes"><span class="axis-z">Z</span><span class="axis-y">Y</span><span class="axis-x">X</span><svg viewBox="0 0 70 70" aria-hidden="true"><path d="M35 39V9" stroke="#81a5ff"/><path d="M35 39 10 55" stroke="#68d6b1"/><path d="M35 39 61 54" stroke="#f39a8c"/></svg></div>
          <div id="busy-overlay" class="busy-overlay" hidden><span class="spinner"></span><strong id="busy-label">Following the force…</strong><span id="busy-detail">Meshing your part and solving the load.</span></div>
          <div id="drag-overlay" class="drag-overlay" hidden>${icon('upload')}Drop your STL here</div>
          <div id="heatmap-legend" class="heatmap-legend" hidden><div><strong>Relative stress</strong><span>THIS RUN</span></div><div class="heat-gradient"></div><div class="heat-labels"><span>Lower</span><span>Concentrated</span></div></div>
          <div id="deformation-label" class="deformation-label" hidden>Movement exaggerated for visibility</div>
        </div>
        <div class="viewer-bottom"><span>${icon('mouse')}<span id="mouse-hint">Drag to orbit · scroll to zoom · right-drag to pan</span></span><span id="viewer-size">STL → a little insight</span></div>
        <div class="result-panel" id="result-panel" hidden>
          <div class="result-heading"><span class="result-icon">${icon('spark')}</span><div><h3>The force has a story.</h3><p>Warm areas carry more concentrated stress in this run.</p></div><span class="result-complete">${icon('check')} Solved</span></div>
          <div class="result-controls"><div class="result-tabs" role="group" aria-label="Model display"><button id="show-heatmap" class="selected">Stress heatmap</button><button id="show-original">Original part</button></div><label class="switch-label"><input id="deformation" type="checkbox"/><span class="switch"></span>Show movement</label></div>
          <div class="result-note">${icon('info')}<p>Look for concentrated colour around necks, holes and inside corners. Try a fillet, rib or thicker section in your next design.</p></div>
          <details class="result-details"><summary>About this preview</summary><p id="result-mesh"></p><ul id="result-notes"></ul><p>Colours rescale for each run. A stronger colour means higher relative stress, not a failure prediction.</p></details>
        </div>
      </section>
    </div>
    <footer><span><span class="footer-mark">${icon('cube')}</span> A better design guess starts here.</span><span>Approximate structural exploration · no failure predictions</span></footer>
  </main>
  <div id="notice" class="notice" role="status" aria-live="polite" hidden><span id="notice-text"></span><button id="close-notice" class="icon-button" aria-label="Dismiss message">${icon('close')}</button></div>
  <dialog id="help-dialog"><button id="close-help" class="dialog-close icon-button" aria-label="Close help">${icon('close')}</button><span class="eyebrow">WELCOME TO MAKERSIM</span><h2>A quick question for your part.</h2><p>See approximately where a force travels, then use that insight to improve your next print.</p><ol><li><strong>Upload</strong> a closed STL. Check its millimetre or inch units.</li><li><strong>Set print</strong> with your filament, walls, layers and infill.</li><li><strong>Hold here</strong> by clicking or painting mounted surfaces. Use Orbit to reach the other side.</li><li><strong>Pull here</strong> by clicking a surface and dragging an arrow. The pull spreads over a small contact patch.</li><li><strong>Add weight & simulate.</strong> Warm colours show more concentrated stress in this run.</li></ol><p class="help-note">This is a coarse linear-elastic preview with approximate printed properties. It helps with design guesses; it does not predict failure, layer separation or safe working loads.</p><button id="help-done" class="primary-light">Let's explore ${icon('arrow')}</button></dialog>
`;

let viewer;
try {
  viewer=new PartViewer($('viewport'),{
    edit:()=>invalidate(),
    selection:()=>updateSelections(),
    direction:()=>{invalidate();updateDirection(null);},
  });
} catch(error) {
  showNotice('The 3D viewer needs WebGL. Try a browser with hardware acceleration enabled.');
  $('viewer-upload').disabled=true;$('load-example').disabled=true;
}

function showNotice(message) {$('notice-text').textContent=message;$('notice').hidden=false;}
$('close-notice').onclick=()=>$('notice').hidden=true;

function setBusy(busy,label='Following the force…') {
  state.busy=busy;viewer?.setBusy(busy);$('busy-overlay').hidden=!busy;$('busy-label').textContent=label;
  $('busy-detail').textContent=label.startsWith('Bringing')?'Reading and centering the STL.':'Meshing your part and solving the load.';
  document.querySelectorAll('.setup input,.setup select,.setup button').forEach(el=>el.disabled=busy);
  document.querySelectorAll('.canvas-tools button,#fit-view,#viewer-upload,#load-example,#stl-file,.result-controls button,.result-controls input').forEach(el=>el.disabled=busy);
  if(!busy){$('material').disabled=!state.materials.length;updateSelections();}
}

function invalidate() {
  if(!state.result)return;
  state.result=null;viewer.clearResult();$('result-panel').hidden=true;$('heatmap-legend').hidden=true;
  $('deformation-label').hidden=true;$('deformation').checked=false;$('viewer-badge').textContent='READY TO EXPLORE';
  $('step-run').classList.remove('done');$('step-run').classList.add('active');
}

function settingsValid() {return ['walls','top-layers','bottom-layers','force'].every(id=>$(id).checkValidity()&&$(id).value.trim()!=='') && !!state.materials.find(m=>m.id===$('material').value)?.supported;}

function updateSelections() {
  if(!viewer)return;
  const count=viewer.fixtures.length,load=!!viewer.load,model=!!state.model;
  $('hold-count').textContent=count?`${count} hold${count===1?'':'s'}`:'No holds yet';
  $('pull-count').textContent=load?'Pull placed':'No pull yet';
  $('undo-hold').disabled=state.busy||!count;$('clear-selection').disabled=state.busy||(!count&&!load);
  $('hold-tool').disabled=$('pull-tool').disabled=state.busy||!model;
  $('simulate').disabled=state.busy||!model||!count||!load||!settingsValid()||!state.model.watertight;
  $('run-hint').textContent=state.busy?'A little patience. The force is finding its way.':!model?'Add a part, a hold and a pull to get started.':!state.model.watertight?'Repair the open STL surface before simulating.':!count?'Choose Hold here and paint a mounted area.':!load?'Choose Pull here and place a force on the part.':!settingsValid()?'Enter valid print settings and a positive force.':'Ready when you are. See where the load goes.';
  const done=[model,model,count>0,load,!!state.result];
  ['upload','print','hold','pull','run'].forEach((name,i)=>{
    $(`step-${name}`).classList.toggle('done',!!done[i]);
    $(`step-${name}`).classList.toggle('active',!done[i]&&(i===0||done[i-1]));
  });
}

function setMode(mode) {
  if(!viewer||state.busy||(!state.model&&mode!=='orbit'))return;
  viewer.setDeformation(false);viewer.setMode(mode);$('deformation').checked=false;$('deformation-label').hidden=true;
  document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('selected',b.dataset.mode===mode);b.setAttribute('aria-pressed',String(b.dataset.mode===mode));});
  $('orbit-tool').classList.toggle('selected',mode==='orbit');
  const message={orbit:'Orbit to find the right spot. Then choose a hold or pull.',hold:'Click or paint the surfaces that are held still.',pull:'Click a surface, then drag to point the force arrow.'}[mode];
  $('tool-description').textContent=message;$('interaction-tip').hidden=mode==='orbit';
  $('interaction-tip').innerHTML=icon(mode==='hold'?'hand':'pull')+`<span>${message}</span>`;
  $('mouse-hint').textContent=mode==='orbit'?'Drag to orbit · scroll to zoom · right-drag to pan':mode==='hold'?'Click or drag to paint · choose Orbit to rotate':'Click and drag an arrow · direction presets below';
  $('rotate-toggle').classList.remove('selected');
}

async function loadFile(file,example=false) {
  if(!viewer||state.busy)return;
  if(!file||!file.name.toLowerCase().endsWith('.stl')){showNotice('Choose an STL file to bring your part in.');return;}
  if(file.size>20*1024*1024){showNotice('This STL is over 20 MB. Export a lower-detail version.');return;}
  setBusy(true,'Bringing your part in…');$('notice').hidden=true;
  try {
    const metadata=await uploadModel(file,$('stl-unit').value);
    await viewer.setFile(await file.arrayBuffer(),$('stl-unit').value);
    state.model=metadata;state.file=file;state.result=null;
    $('empty-view').hidden=true;$('file-summary').hidden=false;$('part-card').hidden=false;
    $('file-name').textContent=file.name;$('viewer-name').textContent=file.name;
    const dimensions=metadata.dimensions_mm.map(n=>n.toFixed(1)).join(' × ');
    $('file-dimensions').textContent=`${dimensions} mm`;$('viewer-size').textContent=`${metadata.triangles.toLocaleString()} triangles · mm`;
    $('contact-size').max=String(Math.max(4,Math.round(Math.max(...metadata.dimensions_mm)/2)));
    $('contact-size').min=String(Math.max(1,Math.round(Math.max(...metadata.dimensions_mm)/100)));
    $('contact-size').value=String(Math.max(2,Math.round(Math.max(...metadata.dimensions_mm)*.15)));
    updateRadius();$('result-panel').hidden=true;$('heatmap-legend').hidden=true;$('deformation-label').hidden=true;
    $('deformation').checked=false;$('viewer-badge').textContent='READY TO EXPLORE';setMode('orbit');
    if(example){viewer.setExampleSelections();$('force').value='25';$('force-unit').value='lbf';updateForce();updateDirection('down');}
    if(metadata.notes.length)showNotice(metadata.notes.join(' '));
  } catch(error){showNotice(error.message||'The STL could not be loaded.');}
  finally {setBusy(false);setMode('orbit');$('stl-file').value='';}
}

$('stl-file').addEventListener('change',e=>loadFile(e.target.files[0]));
['replace-file','viewer-upload'].forEach(id=>$(id).onclick=()=>$('stl-file').click());
$('stl-unit').onchange=()=>{if(state.file)loadFile(state.file);};
$('load-example').onclick=async()=>{
  if(state.busy)return;
  try {const response=await fetch('/api/example');if(!response.ok)throw new Error('The example could not be loaded.');$('stl-unit').value='mm';await loadFile(new File([await response.blob()],'backpack-bracket.stl'),true);}
  catch(error){showNotice(error.message);}
};
let dragDepth=0;
for(const zone of [$('part-card'),$('viewport')]) {
  zone.addEventListener('dragenter',e=>{e.preventDefault();dragDepth++;if(!state.busy)$('drag-overlay').hidden=false;});
  zone.addEventListener('dragover',e=>{e.preventDefault();e.dataTransfer.dropEffect='copy';});
  zone.addEventListener('dragleave',()=>{dragDepth=Math.max(0,dragDepth-1);if(!dragDepth)$('drag-overlay').hidden=true;});
  zone.addEventListener('drop',e=>{e.preventDefault();dragDepth=0;$('drag-overlay').hidden=true;loadFile(e.dataTransfer.files[0]);});
}
// Keep dropped files from navigating away from the workspace.
window.addEventListener('dragover',e=>e.preventDefault());window.addEventListener('drop',e=>e.preventDefault());

document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>setMode(button.dataset.mode));
$('orbit-tool').onclick=()=>setMode('orbit');
$('fit-view').onclick=()=>viewer?.fit();
$('grid-toggle').onclick=()=>{if(viewer){viewer.grid.visible=!viewer.grid.visible;$('grid-toggle').classList.toggle('selected',viewer.grid.visible);}};
$('rotate-toggle').onclick=()=>{if(viewer){setMode('orbit');viewer.controls.autoRotate=!viewer.controls.autoRotate;$('rotate-toggle').classList.toggle('selected',viewer.controls.autoRotate);}};
$('undo-hold').onclick=()=>{invalidate();viewer.undoHold();};
$('clear-selection').onclick=()=>{invalidate();viewer.clearSelections();};
function updateRadius(){const diameter=Number($('contact-size').value);$('contact-value').innerHTML=`${diameter}<span> mm</span>`;viewer?.setRadius(diameter/2);}
$('contact-size').oninput=updateRadius;
function updateForce(){const force=Number($('force').value)*unitFactors[$('force-unit').value];$('force-equivalent').textContent=force>0&&Number.isFinite(force)?`About ${force<10?force.toFixed(1):Math.round(force).toLocaleString()} N of pulling force`:'Enter a positive amount of force';}
['force','force-unit'].forEach(id=>$(id).addEventListener('input',()=>{invalidate();updateForce();updateSelections();}));
['walls','top-layers','bottom-layers','infill','orientation','material'].forEach(id=>$(id).addEventListener('input',()=>{invalidate();$('infill-value').innerHTML=`${$('infill').value}<span>%</span>`;updateMaterial();updateSelections();}));

function updateMaterial() {
  const material=state.materials.find(m=>m.id===$('material').value);if(!material)return;
  $('material-basis').textContent=material.basis==='representative_baseline'?'Representative FDM baseline':'MakerSim archived material data';
  const provenance=material.provenance;
  $('material-source').innerHTML=`<p>${escape(provenance?.source||'Mechanical stiffness is not available for this entry.')}${provenance?.url?` · <a href="${escape(provenance.url)}" target="_blank" rel="noreferrer">Source</a>`:''}</p>${provenance?`<p>${provenance.E_z==='assumed_ratio'?'Z stiffness uses an assumed layer-direction ratio.':'XY and Z stiffness are stored separately.'} Archived values have not been reverified against current datasheets.</p>`:''}`;
}

const directions={down:[0,0,-1],up:[0,0,1],left:[-1,0,0],right:[1,0,0],forward:[0,-1,0],back:[0,1,0]};
const directionLabels={down:'Down · −Z',up:'Up · +Z',left:'Left · −X',right:'Right · +X',forward:'Forward · −Y',back:'Back · +Y'};
function updateDirection(name){$('direction-status').textContent=name?directionLabels[name]:'Custom · dragged arrow';document.querySelectorAll('[data-direction]').forEach(b=>{b.classList.toggle('selected',b.dataset.direction===name);b.setAttribute('aria-pressed',String(b.dataset.direction===name));});}
document.querySelectorAll('[data-direction]').forEach(button=>button.onclick=()=>{if(state.busy)return;invalidate();viewer?.setDirection(directions[button.dataset.direction]);updateDirection(button.dataset.direction);});

$('simulate').onclick=async()=>{
  if($('simulate').disabled)return;
  setBusy(true);$('notice').hidden=true;
  try {
    const result=await simulate({
      model_id:state.model.model_id,material_id:$('material').value,
      print_settings:{walls:Number($('walls').value),top_layers:Number($('top-layers').value),bottom_layers:Number($('bottom-layers').value),infill:Number($('infill').value),orientation:$('orientation').value},
      fixtures:viewer.fixtures,load:viewer.load,direction:viewer.direction,
      magnitude:Number($('force').value),unit:$('force-unit').value,
    });
    viewer.setResult(result);state.result=result;
    $('result-panel').hidden=false;$('heatmap-legend').hidden=false;$('viewer-badge').textContent='LOAD PATH PREVIEW';
    $('show-heatmap').classList.add('selected');$('show-original').classList.remove('selected');
    $('result-mesh').textContent=`${result.mesh.elements.toLocaleString()} linear tetrahedra · about ${result.mesh.cell_mm.toFixed(2)} mm per mesh cell. All loads are distributed across a contact patch.`;
    $('result-notes').innerHTML=result.notes.map(note=>`<li>${escape(note)}</li>`).join('');
    setBusy(false);setMode('orbit');
  } catch(error){showNotice(error.message||'The simulation could not finish.');setBusy(false);}
};

function setHeatmap(heatmap){if(!state.result)return;viewer.heatmap=heatmap;viewer.recolor();$('show-heatmap').classList.toggle('selected',heatmap);$('show-original').classList.toggle('selected',!heatmap);$('heatmap-legend').hidden=!heatmap;}
$('show-heatmap').onclick=()=>setHeatmap(true);$('show-original').onclick=()=>setHeatmap(false);
$('deformation').onchange=()=>{viewer.setDeformation($('deformation').checked);$('deformation-label').hidden=!$('deformation').checked;$('deformation-label').textContent=`Movement exaggerated ${viewer.deformationFactor().toFixed(0)}× · visual only`;};
$('help').onclick=()=>$('help-dialog').showModal();['close-help','help-done'].forEach(id=>$(id).onclick=()=>$('help-dialog').close());
$('help-dialog').addEventListener('click',e=>{if(e.target===$('help-dialog'))$('help-dialog').close();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&viewer)setMode('orbit');});

getMaterials().then(({materials})=>{
  state.materials=materials;
  const generic=materials.filter(m=>m.brand==='Generic').sort((a,b)=>a.name.localeCompare(b.name));
  const branded=materials.filter(m=>m.brand!=='Generic').sort((a,b)=>(a.brand+a.name).localeCompare(b.brand+b.name));
  const options=(list)=>list.map(m=>`<option value="${escape(m.id)}" ${m.supported?'':'disabled'}>${escape((m.brand==='Generic'?'':m.brand+' · ')+m.name)}${m.supported?'':' · not supported in v1'}</option>`).join('');
  $('material').innerHTML=`<optgroup label="FDM baselines">${options(generic)}</optgroup><optgroup label="MakerSim material archive">${options(branded)}</optgroup>`;
  $('material').value='generic-pla';updateMaterial();updateSelections();
}).catch(()=>{showNotice('The local solver is unavailable. Start the MakerSim Python service, then reload this page.');$('material').innerHTML='<option>Solver service unavailable</option>';$('material').disabled=true;});
updateForce();updateRadius();
window.addEventListener('pagehide',()=>viewer?.dispose());
