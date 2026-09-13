const $=id=>document.getElementById(id);
let scans=[], busy=false, viewer=null, selectionVersion=0;
let defaultGrid=96, defaultMaxFrames=0, photoOffset=0, photoVersion=0;
const initialScan=new URLSearchParams(location.search).get('scan');
function status(message,error=false){$('status').textContent=message;$('status').classList.toggle('error',error);}
try {viewer=new MeshViewer($('canvas'));} catch(e){status(e.message,true);}
async function api(url,options={}) {
  const response=await fetch(url,options), data=await response.json();
  if(!response.ok)throw new Error(data.error||`Request failed (${response.status})`);
  return data;
}
function controls() {
  for(const id of ['generate','import','refresh','scans'])$(id).disabled=busy;
  $('run').disabled=busy||!$('scans').value;
  $('done').disabled=busy||!scans.find(s=>s.id===$('scans').value)?.disposable;
  $('frame-limit').disabled=busy||!$('scans').value;
  $('use-all').disabled=busy||!$('scans').value;
  $('quality').disabled=busy||!$('scans').value;
  window.runnerControls?.();
}
async function refresh(preferred=$('scans').value||initialScan) {
  const data=await api('/api/scans');scans=data.scans;defaultMaxFrames=data.default_max_frames??0;defaultGrid=data.default_grid??96;$('root').textContent=data.root;
  $('scans').replaceChildren();
  if(!scans.length)$('scans').add(new Option('No scans yet',''));
  for(const scan of scans)$('scans').add(new Option(scan.id,scan.id));
  if(scans.some(s=>s.id===preferred))$('scans').value=preferred;
  controls();await select();
}
async function select() {
  const version=++selectionVersion;
  const scan=scans.find(s=>s.id===$('scans').value), result=scan?.result;
  window.loadFullResult?.(scan?.id);
  photoVersion++;photoOffset=0;$('photo-grid').replaceChildren();$('photo-page').textContent='';
  $('photos-prev').disabled=true;$('photos-next').disabled=true;
  $('photo-summary').textContent='Select a scan to inspect its photos.';
  $('photo-count').textContent=scan?`(${scan.photo_count??scan.frames})`:'';
  $('full-output').hidden=!scan?.full_workspace;
  $('full-output').textContent=scan?.full_workspace?`Full photogrammetry: ${scan.full_status}. Workspace: ${scan.full_workspace}`:'';
  if(scan?.full_workspace){
    const report=document.createElement('a');report.textContent=' Open run report';
    report.href=`/scans/${encodeURIComponent(scan.id)}/outputs/${['vggt','worldmirror2'].includes(scan.engine)?scan.engine:'full_photogrammetry'}/reports/run_report.md`;
    report.target='_blank';report.rel='noopener';$('full-output').append(report);
  }

  for(const [id,name] of [['combined-sheet','combined_contact_sheet.jpg'],['split-sheet','split_contact_sheet.jpg']]){
    const path=`outputs/inspection/${name}`;
    $(id).hidden=!scan?.inspection?.includes(path);
    if(scan)$(id).href=`/scans/${encodeURIComponent(scan.id)}/${path}`;
  }
  if($('photo-dialog').open)$('photo-dialog').close();
  if(scan){
    const upgraded=result?.quick.surface_version===2;
    const maximum=upgraded?result.quick.max_frames:defaultMaxFrames;
    const grid=upgraded?result.quick.grid:defaultGrid;
    for(const option of Array.from($('quality').options))if(option.dataset.custom)option.remove();
    if(!Array.from($('quality').options).some(o=>Number(o.value)===grid)){const option=new Option(`Custom (${grid})`,grid);option.dataset.custom='true';$('quality').add(option);}
    $('quality').value=grid;
    $('frame-limit').max=scan.frames;$('frame-limit').value=maximum===0?scan.frames:Math.min(maximum,scan.frames);
    $('use-all').textContent=`Use all ${scan.frames}`;
  }
  updateFrameHelp();
  viewer?.clear();$('empty').hidden=false;$('empty').querySelector('strong').textContent='Your first scan starts here';$('empty').querySelector('p').textContent='Generate or import images, then run the preview.';$('geometry-info').textContent='Silhouette reconstruction';
  for(const id of ['glb','obj','detailed-link'])$(id).hidden=true;
  for(const id of ['images','elapsed','triangles'])$(id).textContent='—';
  $('detailed').textContent='Not prepared yet';$('detailed-info').textContent='The same images will be organized for a later reconstruction.';
  $('scan-info').textContent=scan?`${scan.frames} images · ${scan.source}`:'Generate a test scan to get started.';
  $('state').textContent=result?'PREVIEW READY':scan?'READY TO PROCESS':'AWAITING SCAN';
  controls();if(!scan){history.replaceState(null,'','/');return;}
  history.replaceState(null,'',`/?scan=${encodeURIComponent(scan.id)}`);
  if($('photos-panel').open)loadPhotos();
  if(!result)return;
  const base=`/scans/${encodeURIComponent(scan.id)}/outputs`;
  $('images').textContent=`${result.quick.frames_used} / ${result.quick.frames_total}`;
  $('elapsed').textContent=`${result.quick.seconds.toFixed(2)} s`;
  $('triangles').textContent=result.quick.triangles.toLocaleString();
  $('geometry-info').textContent=result.quick.surface_version===2?`${result.quick.grid}³ grid · ${result.quick.voxel_size_mm} mm cells · smoothed surface`:'Legacy coarse preview · Run preview to upgrade';
  $('detailed').textContent='Images prepared';$('detailed-info').textContent=`${result.detailed.image_count} images staged. Heavy reconstruction has not run.`;
  if(result.quick.status==='unavailable'){
    $('state').textContent='PHOTOS READY';$('geometry-info').textContent='Awaiting verified camera views';$('elapsed').textContent='—';$('triangles').textContent='—';
    $('empty').querySelector('strong').textContent='Camera layout needs verification';
    $('empty').querySelector('p').textContent=result.quick.reason;
    $('detailed-info').textContent=`${result.detailed.image_count} combined images staged. Verify and split camera views before reconstruction.`;
    $('detailed-link').href=`${base}/detailed/README.md`;$('detailed-link').hidden=false;
    status(result.quick.reason);return;
  }
  for(const type of ['glb','obj']){$(type).href=`${base}/quick/preview.${type}`;$(type).hidden=false;}
  $('detailed-link').href=`${base}/detailed/README.md`;$('detailed-link').hidden=false;
  if(!viewer)throw new Error('WebGL unavailable; use the model download links.');
  const mesh=await api(`${base}/quick/mesh.json`);
  if(version!==selectionVersion)return;
  viewer.load(mesh);$('empty').hidden=true;
}
async function job(request) {
  if(busy)return;
  busy=true;controls();status('Starting '+request.action+'…');
  try {
    const {job_id}=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
    await watchJob(job_id,request);
  }catch(e){status(e.message,true);busy=false;controls();}
}
async function watchJob(job_id,request={}) {
  busy=true;controls();$('state').textContent='WORKING';
  try {
    let current;
    do {
      current=await api(`/api/jobs/${job_id}`);
      window.runnerProgress?.(current);
      if(['queued','running'].includes(current.status))await new Promise(resolve=>setTimeout(resolve,750));
    }while(['queued','running'].includes(current.status));
    if(current.status==='failed')throw new Error(current.error);
    await refresh(current.scan_id||$('scans').value);
    window.runnerFinished?.(current);
    status(current.result?.quick?.reason||current.message||(request.action==='discard'?'Generation deleted.':request.action==='process'?'Complete. Quick model exported; detailed reconstruction images prepared.':'New random scan ready. Previous generated scans deleted. Click Run preview.'));
  }catch(e){status(e.message,true);$('state').textContent='NEEDS ATTENTION';}
  finally{busy=false;controls();}
}
$('generate').onclick=()=>job({action:'generate'});
$('import').onclick=()=>{const path=$('import-path').value.trim().replace(/^"|"$/g,'');if(!path)return status('Enter the full scan folder path, including its scan_… folder.',true);job({action:'import',path});};
$('done').onclick=()=>job({action:'discard',scan_id:$('scans').value});
$('refresh').onclick=()=>refresh().catch(e=>status(e.message,true));
$('scans').onchange=()=>select().catch(e=>status(e.message,true));
$('reset').onclick=()=>viewer?.reset();
refresh().catch(e=>status(e.message,true));
// Show externally watched scan completions without resetting the orbit every poll.
setInterval(async()=>{
  if(busy)return;
  try {const data=await api('/api/scans');if(JSON.stringify(data.scans)!==JSON.stringify(scans))await refresh();}
  catch(e){status(`Client disconnected: ${e.message}. Check the terminal.`,true);}
},3000);

function updateFrameHelp(){
  const scan=scans.find(s=>s.id===$('scans').value), count=Number($('frame-limit').value);
  if(!scan){$('frame-help').textContent='All images are used by default. Reduce the count for a faster run.';return;}
  $('frame-help').textContent=`Next run: ${Number.isInteger(count)&&count>0?Math.min(count,scan.frames):'—'} / ${scan.frames} images. Click Run preview to apply. Detailed preparation always includes all ${scan.frames}.`;
}
$('frame-limit').oninput=updateFrameHelp;
$('use-all').onclick=()=>{const scan=scans.find(s=>s.id===$('scans').value);if(scan){$('frame-limit').value=scan.frames;updateFrameHelp();}};
$('run').onclick=()=>{
  if(!$('frame-limit').reportValidity())return;
  job({action:'process',scan_id:$('scans').value,max_frames:Number($('frame-limit').value),grid:Number($('quality').value)});
};
function photoUrl(sid,file,size){return `/api/scans/${encodeURIComponent(sid)}/thumbnail?file=${encodeURIComponent(file)}&size=${size}`;}
async function loadPhotos(){
  const sid=$('scans').value, version=++photoVersion;
  if(!sid)return;
  $('photo-grid').replaceChildren();$('photo-summary').textContent='Loading photo previews…';
  $('photos-prev').disabled=true;$('photos-next').disabled=true;
  try{
    const data=await api(`/api/scans/${encodeURIComponent(sid)}/photos?filter=${$('photo-filter').value}&offset=${photoOffset}&limit=48`);
    if(version!==photoVersion||sid!==$('scans').value)return;
    const usage=data.frames_used===null?'No quick preview has run yet.':`${data.frames_used} / ${data.frames_total} photos used in the last quick preview.`;
    $('photo-summary').textContent=$('photo-filter').value==='model_inputs'?`${data.total} exact masked and resized images submitted to the reconstruction model.`:$('photo-filter').value==='reconstruction'?`${data.total} prepared reconstruction images, after orientation and crop. The image limit controls how many are submitted.`:usage+(data.selection_inferred?' Older result: used-photo selection inferred from its saved count.':'')+(data.total===0?' No photos match this filter.':'');
    for(const item of data.items){
      const card=document.createElement('button');card.type='button';card.className='photo-card';
      const img=document.createElement('img');img.src=photoUrl(sid,item.file,240);img.alt=item.file;img.loading='lazy';img.decoding='async';
      const name=document.createElement('span');name.className='photo-name';name.textContent=item.file.split('/').pop();
      const badge=document.createElement('span');badge.className='photo-badge'+(item.used?' used':'');
      badge.textContent=item.model_input?'Used by 3D model':item.reconstruction?'Prepared image':item.background?'Background':item.used===null?'Not processed yet':item.used?'Used in quick preview':'Not used in quick preview';
      const meta=document.createElement('span');meta.className='photo-meta';meta.textContent=item.camera_id+(item.angle_deg===undefined?'':` · ${Number(item.angle_deg.toFixed(1))}°`);
      card.append(img,name,badge,meta);
      card.onclick=()=>{ $('photo-large').src=photoUrl(sid,item.file,640);$('photo-large').alt=item.file;$('photo-caption').textContent=`${item.file} · ${badge.textContent}`;$('photo-dialog').showModal(); };
      $('photo-grid').append(card);
    }
    $('photo-page').textContent=data.total?`${data.offset+1}–${data.offset+data.items.length} of ${data.total}`:'0 photos';
    $('photos-prev').disabled=data.offset===0;
    $('photos-next').disabled=data.offset+data.items.length>=data.total;
  }catch(e){if(version===photoVersion)$('photo-summary').textContent=`Could not load photos: ${e.message}`;}
}
$('photos-panel').ontoggle=()=>{if($('photos-panel').open)loadPhotos();};
$('photo-filter').onchange=()=>{photoOffset=0;loadPhotos();};
$('photos-prev').onclick=()=>{photoOffset=Math.max(0,photoOffset-48);loadPhotos();};
$('photos-next').onclick=()=>{photoOffset+=48;loadPhotos();};
$('photo-close').onclick=()=>$('photo-dialog').close();
