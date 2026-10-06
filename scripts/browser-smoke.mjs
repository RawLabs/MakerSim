import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile, mkdir } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { dirname, resolve, extname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const moduleName=process.env.MAKERSIM_PLAYWRIGHT_MODULE||'playwright';
const { chromium }=await import(moduleName.startsWith('/')?pathToFileURL(moduleName).href:moduleName);
const transport=spawn(resolve(root,'.venv/bin/python'),['-m','scripts.qa_bridge'],{cwd:root,stdio:['pipe','pipe','pipe']});
let serial=0;const pending=new Map();
createInterface({input:transport.stdout}).on('line',line=>{
  const message=JSON.parse(line),pair=pending.get(message.id);pending.delete(message.id);
  if(message.error)pair.reject(new Error(message.error));else pair.resolve(message);
});
transport.stderr.on('data',chunk=>process.stderr.write(chunk));
const api=(request)=>new Promise((resolve,reject)=>{
  const id=++serial;pending.set(id,{resolve,reject});
  transport.stdin.write(JSON.stringify({id,method:request.method(),path:new URL(request.url()).pathname,headers:request.headers(),body:request.postDataBuffer()?.toString('base64')||''})+'\n');
});
const mime={'.html':'text/html','.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png'};
const browser=await chromium.launch({executablePath:process.env.MAKERSIM_CHROMIUM||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
const context=await browser.newContext({viewport:{width:1440,height:1060},deviceScaleFactor:1});
const page=await context.newPage();const errors=[];
page.on('pageerror',error=>errors.push(error.message));
await context.route('**/*',async route=>{
  const request=route.request(),pathname=new URL(request.url()).pathname;
  if(pathname.startsWith('/api/')) {
    const response=await api(request);
    return route.fulfill({status:response.status,contentType:response.content_type,body:Buffer.from(response.body,'base64')});
  }
  const base=pathname.startsWith('/src/')?'frontend':'frontend/dist';
  const path=resolve(root,base,pathname==='/'?'index.html':'.'+decodeURIComponent(pathname));
  try {await route.fulfill({status:200,contentType:mime[extname(path)]||'application/octet-stream',body:await readFile(path)});}
  catch {await route.fulfill({status:404,body:'Not found'});}
});
const shots=resolve(root,'artifacts');await mkdir(shots,{recursive:true});
// File-backed multipart bodies omit their file bytes from Playwright's route
// request data. Use bytes so the in-process API transport gets the whole STL.
const bracketUpload={name:'backpack-bracket.stl',mimeType:'model/stl',buffer:await readFile(resolve(root,'backend/data/backpack-bracket.stl'))};
try {
  await page.goto('https://makersim.local/');
  await page.waitForFunction(()=>document.querySelector('#material').value==='generic-pla');
  assert.equal(await page.locator('#simulate').isDisabled(),true);
  assert.equal(await page.locator('#part-card').isVisible(),false);
  assert.equal(await page.locator('#viewer-upload').isVisible(),true);
  await page.screenshot({path:resolve(shots,'workspace-empty.png'),fullPage:true});
  const initialChooser=page.waitForEvent('filechooser');
  await page.locator('#viewer-upload').click();
  await (await initialChooser).setFiles(bracketUpload);
  await page.locator('#file-summary').waitFor({state:'visible'});
  assert.equal(await page.locator('#file-name').innerText(),'backpack-bracket.stl');
  assert.equal(await page.locator('#part-card').isVisible(),true);
  assert.equal(await page.locator('#viewer-upload').isVisible(),false);
  await page.reload();
  await page.waitForFunction(()=>document.querySelector('#material').value==='generic-pla');
  await page.locator('#load-example').click();
  await page.waitForFunction(()=>!document.querySelector('#simulate').disabled);
  assert.equal(await page.locator('#file-name').innerText(),'backpack-bracket.stl');
  assert.equal(await page.locator('#part-card').isVisible(),true);
  assert.equal(await page.locator('#viewer-upload').isVisible(),false);
  await page.locator('#simulate').click();
  await page.locator('#result-panel').waitFor({state:'visible',timeout:60000});
  assert.equal(await page.locator('#heatmap-legend').isVisible(),true);
  await page.screenshot({path:resolve(shots,'workspace-heatmap.png'),fullPage:true});
  await page.locator('#show-original').click();
  assert.equal(await page.locator('#heatmap-legend').isVisible(),false);
  await page.locator('#show-heatmap').click();
  await page.locator('.switch-label').click();
  assert.equal(await page.locator('#deformation').isChecked(),true);
  assert.equal(await page.locator('#deformation-label').isVisible(),true);
  await page.locator('.switch-label').click();
  assert.equal(await page.locator('#deformation').isChecked(),false);
  await page.locator('#force').fill('50');
  assert.equal(await page.locator('#result-panel').isVisible(),false);
  await page.locator('#clear-selection').click();
  assert.equal(await page.locator('#simulate').isDisabled(),true);

  // Exercise the actual painted-area and dragged-arrow UI. Project known
  // bracket coordinates using the documented automatic camera fit.
  const project=async point=>page.evaluate(async point=>{
    const THREE=await import('/src/vendor/three.module.js');
    const rect=document.querySelector('.viewport canvas').getBoundingClientRect();
    const radius=Math.sqrt(79**2+16**2+50**2)/2;
    const camera=new THREE.PerspectiveCamera(38,rect.width/rect.height,.01,10000);
    camera.up.set(0,0,1);
    const horizontal=2*Math.atan(Math.tan(THREE.MathUtils.degToRad(19))*camera.aspect);
    const angle=Math.min(horizontal,THREE.MathUtils.degToRad(38));
    camera.position.copy(new THREE.Vector3(.8,-1.65,1.05).normalize().multiplyScalar(radius/Math.sin(angle/2)*1.22));
    camera.lookAt(0,0,0);camera.updateMatrixWorld();
    const screen=new THREE.Vector3(...point).project(camera);
    return {x:rect.left+(screen.x+1)/2*rect.width,y:rect.top+(1-screen.y)/2*rect.height};
  },point);
  await page.locator('#hold-tool').click();
  const held=await project([-25.5,-8,0]);
  await page.mouse.move(held.x,held.y);await page.mouse.down();await page.mouse.move(held.x,held.y-14,{steps:6});await page.mouse.up();
  assert.match(await page.locator('#hold-count').innerText(),/hold/);
  assert.notEqual(await page.locator('#hold-count').innerText(),'No holds yet');
  await page.locator('#pull-tool').click();
  const pulled=await project([32.5,-6,-17]);
  await page.mouse.move(pulled.x,pulled.y);await page.mouse.down();await page.mouse.move(pulled.x+22,pulled.y+50,{steps:10});await page.mouse.up();
  assert.equal(await page.locator('#pull-count').innerText(),'Pull placed');
  assert.match(await page.locator('#direction-status').innerText(),/Custom/);
  await page.locator('[data-direction=down]').click();
  assert.equal(await page.locator('#simulate').isDisabled(),false);
  await page.locator('#simulate').click();
  await page.locator('#result-panel').waitFor({state:'visible',timeout:60000});
  await page.locator('#undo-hold').click();
  assert.equal(await page.locator('#result-panel').isVisible(),false);
  await page.locator('#force-unit').selectOption('kg');
  await page.locator('#force').fill('1');
  assert.match(await page.locator('#force-equivalent').innerText(),/9.8 N/);

  // Upload path, inch rescaling, help and narrow viewport layout.
  const chooser=page.waitForEvent('filechooser');
  await page.locator('#replace-file').click();
  await (await chooser).setFiles(bracketUpload);
  await page.waitForFunction(()=>document.querySelector('#busy-overlay').hidden);
  await page.waitForFunction(()=>document.querySelector('#hold-count').textContent==='No holds yet');
  assert.equal(await page.locator('#pull-count').innerText(),'No pull yet');
  await page.locator('#stl-unit').selectOption('inch');
  await page.waitForFunction(()=>document.querySelector('#file-dimensions').textContent.includes('2006.6'));
  await page.locator('#stl-unit').selectOption('mm');
  await page.waitForFunction(()=>document.querySelector('#file-dimensions').textContent.includes('79.0'));
  await page.locator('#help').click();assert.equal(await page.locator('#help-dialog').isVisible(),true);
  await page.keyboard.press('Escape');assert.equal(await page.locator('#help-dialog').isVisible(),false);
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:resolve(shots,'workspace-mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  assert.deepEqual(errors,[]);
  console.log('Browser workflow passed: sample + real solver, paint, drag, rerun, unit conversion, upload, reset, help, mobile.');
} catch(error) {
  console.error('Browser state:',await page.evaluate(()=>({
    notice:document.querySelector('#notice-text')?.textContent,
    busy:!document.querySelector('#busy-overlay')?.hidden,
    file:document.querySelector('#file-name')?.textContent,
  })),errors);
  await page.screenshot({path:resolve(shots,'workspace-error.png'),fullPage:true});
  throw error;
} finally {await browser.close();transport.stdin.end();transport.kill();}
