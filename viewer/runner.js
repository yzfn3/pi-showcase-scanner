/* The browser drives the same capture and reconstruction modules as the CLI. */
let cloud=null, meshViewer=null, fullVersion=0, sparseReady=false,meshReady=false, phaseOneShown=null;
let requestedMesh=new URLSearchParams(location.search).get('mesh');
try{cloud=new MeshViewer($('colmap-canvas'));}catch(e){status(e.message,true);}
try{meshViewer=new MeshViewer($('mesh-canvas'));}catch(e){status(e.message,true);}
const artifactUrl=(sid,file)=>`/scans/${encodeURIComponent(sid)}/${file.split('/').map(encodeURIComponent).join('/')}`;
window.runnerControls=()=>{
  document.querySelectorAll('.runner-action,.runner-setting').forEach(el=>el.disabled=busy);
  $('empty-table').disabled=busy;
  $('background-capture').disabled=busy||!$('empty-table').checked;
  $('capture-object').disabled=busy||$('empty-table').checked;
  $('capture-only').disabled=busy||$('empty-table').checked;
  $('capture-help').textContent=busy?'A job is running. Capture becomes available when it finishes.':$('empty-table').checked?'Uncheck “Object removed; table is empty” after replacing the object to enable capture.':'Put the object in place and start the turntable. Photos only saves images locally without running reconstruction.';
  for(const id of ['prepare-selected','colmap-selected'])$(id).disabled=busy||!$('scans').value;
  $('build-mesh').disabled=busy||!sparseReady;
  $('cleanup-selected').disabled=busy||!meshReady;
  $('trellis-generate').disabled=busy||!sparseReady;
};
function runnerRequest(action){
  for(const el of document.querySelectorAll('.runner-setting'))if(!el.reportValidity())throw new Error('Check the camera settings.');
  const number=id=>Number($(id).value), focus=$('capture-focus').value;
  return {action,trellis_source:$('trellis-source').value,trellis_resolution:number('trellis-resolution'),trellis_seed:number('trellis-seed'),surface_resolution:number('surface-resolution'),surface_smoothing:number('surface-smoothing'),mask_method:$('mask-method').value,sam_region:$('sam-region').value.split(',').map(Number),cleanup:$('auto-cleanup').checked,cleanup_smoothing:number('cleanup-smoothing'),engine:$('reconstruction-engine').value,node:$('node-url').value.trim(),scan_id:$('scans').value,
    remote_scan_id:$('remote-scan-id').value.trim(),empty_table:$('empty-table').checked,
    run_colmap:false,dense:$('dense').checked,brightness_gamma:number('brightness-gamma'),vggt_frames:number('vggt-frames'),vggt_size:number('vggt-size'),mesher:$('mesher').value,
    matcher:$('matcher').value,mask_threshold:number('mask-threshold'),
    crop_objects:$('crop-objects').checked,crop_padding:number('crop-padding')/100,rotation:number('image-rotation'),dense_size:number('dense-size'),
    settings:{steps:number('capture-steps'),rotation_seconds:number('capture-rotation'),
      capture_width:number('capture-width'),capture_height:number('capture-height'),
      gain:$('capture-gain').value===''?null:number('capture-gain'),
      exposure_time:$('capture-shutter').value===''?null:number('capture-shutter'),
      focus_mode:focus,lens_position:focus==='manual'&&$('capture-lens').value!==''?number('capture-lens'):null,
      camera_timeout_ms:number('capture-timeout'),jpeg_quality:number('capture-jpeg'),
      sensor_mode:$('capture-mode').value,viewfinder_mode:$('capture-mode').value,
      autofocus_window:$('capture-window').value,autofocus_on_capture:focus==='auto',
      use_backgrounds:$('use-backgrounds').checked}};
}
for(const [id,action] of Object.entries({'node-connect':'connect','background-capture':'backgrounds',
  'capture-only':'capture_only','capture-object':'capture','receive-scan':'receive','prepare-selected':'full_process','colmap-selected':'colmap','build-mesh':'mesh','cleanup-selected':'cleanup','trellis-generate':'trellis'})){
  $(id).onclick=()=>{try{job(runnerRequest(action));}catch(e){status(e.message,true);}};
}
$('mesh-appearance').onchange=()=>{if(meshViewer){meshViewer.surfaceStyle=Number($('mesh-appearance').value);meshViewer.draw();}};
$('mesh-version').onchange=()=>loadFullResult($('scans').value);
$('empty-table').onchange=()=>controls();
$('run-colmap').onchange=()=>{if(!$('run-colmap').checked)$('dense').checked=false;};
$('dense').onchange=()=>{if($('dense').checked)$('run-colmap').checked=true;};
$('mesh-reset').onclick=()=>meshViewer?.reset();
$('colmap-reset').onclick=()=>cloud?.reset();
window.runnerProgress=current=>{
  if(current.phase===0){if(current.total){$('mask-progress').max=current.total;$('mask-progress').value=current.done;}else $('mask-progress').removeAttribute('value');$('mask-status').textContent=current.stage;}
  if(current.phase===3){$('cleanup-progress').value=current.done||0;$('cleanup-status').textContent=current.stage;}
  if(current.phase===2&&current.scan_id&&phaseOneShown!==current.id){phaseOneShown=current.id;refresh(current.scan_id).catch(e=>status(e.message,true));}
  $('job-stage').textContent=current.status==='failed'?'Run failed':current.status==='complete'?(current.message||'Complete'):current.stage||'Starting…';
  $('job-progress').textContent=current.remote_scan_id||'';
  const bar=$('stage-progress');
  if(current.status==='complete'){bar.max=1;bar.value=1;$('stage-percent').textContent='Run finished — inspect the phase results below.';}
  else if(current.total>0&&current.done!==null){bar.max=current.total;bar.value=current.done;$('stage-percent').textContent=`${current.done} / ${current.total} · ${Math.round(100*current.done/current.total)}% ${current.progress_unit||'of this operation'}`;}
  else if(current.status==='failed'){bar.value=0;$('stage-percent').textContent=current.error;}
  else{bar.removeAttribute('value');$('stage-percent').textContent='Working — this operation does not report a reliable percentage.';}
  if(current.phase===2){$('mesh-progress').value=current.phase_done;$('mesh-stage-count').textContent=`${current.phase_done} / ${current.phase_total} stages complete`;$('mesh-state').textContent=current.status==='running'?'RUNNING':'CHECK RESULT';if(current.status==='running')$('mesh-empty').textContent='Building the dense surface. The reconstructed mesh will appear here.';}
  $('job-log').textContent=[...(current.events||[]),current.engine_log||'',current.error||''].filter(Boolean).join('\n')||'Waiting for pipeline output…';
  const stage=(current.stage||'').toLowerCase();
  const match=stage.includes('colmap')||current.phase===2?4:stage.includes('mask')||stage.includes('crop')?3:stage.includes('split')||stage.includes('sharpness')?2:stage.includes('download')?1:0;
  document.querySelectorAll('.pipeline span').forEach((el,i)=>el.classList.toggle('active',current.status==='running'&&i===match));
};
window.runnerFinished=current=>{
  if(current.action==='capture_only'){$('photo-filter').value='all';$('photos-panel').open=true;$('photo-filter').onchange();}
  if(current.trellis)$('trellis-source').value=current.trellis.source_image;
  if(['trellis','mesh'].includes(current.action)){ $('mesh-version').value=current.action==='trellis'?'generated':'fused';loadFullResult(current.scan_id);}
  if(current.health)$('node-status').textContent=current.message;
  if(current.action==='backgrounds'){$('empty-table').checked=false;$('node-status').textContent=current.message;}
};
window.loadFullResult=async sid=>{
  const version=++fullVersion;cloud?.clear();meshViewer?.clear();sparseReady=false;meshReady=false;$('colmap-empty').hidden=false;
  $('mesh-empty').hidden=false;$('mesh-empty').textContent='Complete phase 1, then build the mesh.';$('mesh-state').textContent='NOT RUN';$('mesh-download').hidden=true;
  $('mesh-info').textContent='Actual reconstructed surface; no placeholder geometry.';$('mesh-progress').value=0;$('mesh-stage-count').textContent='0 / 4 stages complete';controls();
  $('colmap-empty').querySelector('strong').textContent='No reconstruction yet';
  $('colmap-empty').querySelector('p').textContent='Capture an object or prepare a selected scan.';
  $('colmap-state').textContent='READY';
  for(const id of ['registered-count','point-count','camera-count'])$(id).textContent='—';
  $('full-artifacts').replaceChildren();$('camera-models').replaceChildren();$('mask-sheet').hidden=true;$('crop-sheet').hidden=true;
  $('full-warnings').textContent='';$('colmap-next').textContent='VGGT predicts camera poses and depth directly; no COLMAP optimization.';
  $('sam-mask-sheet').hidden=true;$('mask-progress').value=0;$('cleanup-progress').value=0;$('cleanup-status').textContent='Original mesh is always preserved.';
  if(!sid)return;
  try{
    const data=await api(`/api/scans/${encodeURIComponent(sid)}/full`);
    if(version!==fullVersion)return;
    const r=data.reconstruction, report=data.report, base=data.workspace||'outputs/full_photogrammetry';
    const selectedVersion=requestedMesh||$('mesh-version').value;requestedMesh=null;
    $('mesh-version').querySelectorAll('[data-generation]').forEach(option=>option.remove());
    for(const generation of data.generations||[]){
      const option=document.createElement('option');option.dataset.generation='true';
      option.value='generated:'+generation.view.mesh_file;option.textContent=`TRELLIS ${generation.resolution} / seed ${generation.seed} / ${generation.source_image}`;$('mesh-version').append(option);
    }
    if([...$('mesh-version').options].some(o=>o.value===selectedVersion))$('mesh-version').value=selectedVersion;
    $('reconstruction-title').textContent=r?.engine==='worldmirror2'?'WorldMirror 2.0 geometry':r?.engine==='vggt'?'VGGT geometry':'Reconstruction';
    sparseReady=!!(r?.sparse_model||r?.view);meshReady=r?.mesh_status==='complete';controls();
    if(r?.selected_images){const old=$('trellis-source').value;$('trellis-source').replaceChildren(...r.selected_images.map(name=>{const o=document.createElement('option');o.value=name;o.textContent=name;return o;}));$('trellis-source').value=r.selected_images.includes(old)?old:(r.trellis?.source_image||r.selected_images[Math.floor(r.selected_images.length/2)]);}
    $('trellis-status').textContent=r?.trellis?r.trellis.warning+' Source: '+r.trellis.source_image:'Uses the local TRELLIS.2 model; photos stay on this computer.';
    if(r?.mask_method==='sam2'){$('mask-progress').max=1;$('mask-progress').value=1;$('mask-status').textContent='SAM 2 object masks complete';}
    if(r?.cleanup){$('cleanup-progress').value=4;$('cleanup-status').textContent=`${r.cleanup.before_faces.toLocaleString()} original faces → ${r.cleanup.after_faces.toLocaleString()} cleaned faces. ${r.cleanup.warnings.join(' ')}`;}
    if(!r&&!report)return;
    const state=r?.status||report?.colmap_status||'prepared_only';
    $('colmap-state').textContent=state.replaceAll('_',' ').toUpperCase();
    $('registered-count').textContent=r?.registered_images===undefined?'—':`${r.registered_images} / ${r.input_images}`;
    $('point-count').textContent=r?.points3D?.toLocaleString()||'—';
    const cameras=Object.entries(r?.camera_models||{});
    $('camera-count').textContent=['vggt','worldmirror2'].includes(r?.engine)?r.processed_images:(cameras.length||'Not audited');
    $('colmap-next').textContent=r?.next_action||report?.next_action||'';
    $('full-warnings').textContent=[...new Set([...(report?.warnings||[]),...(r?.warnings||[]),...(report?.errors||[]),...(r?.errors||[]),...(r?.mesh_errors||[])])].join('\n\n')||'No warnings recorded.';
    if(cameras.length){
      const table=document.createElement('table');
      for(const row of [['Physical camera','COLMAP ID','Images sharing intrinsics'],...cameras.map(([name,c])=>[name,c.camera_id,c.images])]){
        const tr=document.createElement('tr');row.forEach(value=>{const td=document.createElement('td');td.textContent=value;tr.append(td);});table.append(tr);
      }
      $('camera-models').append(table);
    }
    for(const item of data.artifacts){
      const a=document.createElement('a');a.textContent=item.label;a.href=artifactUrl(sid,item.file);a.target='_blank';a.rel='noopener';$('full-artifacts').append(a);
      if(item.file.endsWith('/mask_contact_sheet.jpg')){$('mask-sheet').src=a.href;$('mask-sheet').hidden=false;$('sam-mask-sheet').src=a.href;$('sam-mask-sheet').hidden=false;}
      if(item.file.endsWith('/cropped_contact_sheet.jpg')){$('crop-sheet').src=a.href;$('crop-sheet').hidden=false;}
    }
    if(r?.view){
      const points=await api(artifactUrl(sid,base+'/' +r.view.file));
      if(version!==fullVersion)return;
      if(!cloud)throw new Error('WebGL unavailable. Download the reconstruction files.');
      if(!points.positions.length)throw new Error('Sparse model contains no displayable points.');
      cloud.load(points,true);$('colmap-empty').hidden=true;
    }else{
      $('colmap-empty').querySelector('strong').textContent=state.replaceAll('_',' ');
      $('colmap-empty').querySelector('p').textContent=r?.errors?.join(' ')||'No browser point cloud is available. Run COLMAP to produce one.';
    }
    $('mesh-state').textContent=(r?.mesh_status||'not run').toUpperCase();
    if(r?.mesh_errors?.length)$('mesh-empty').textContent=r.mesh_errors.join(' ');
    if((r?.mesh_status==='complete'&&r.mesh_view)||r?.generated_mesh_view){
      const generation=(data.generations||[]).find(g=>'generated:'+g.view.mesh_file===$('mesh-version').value);
      const meshChoice=generation?generation.view:$('mesh-version').value==='generated'&&r.generated_mesh_view?r.generated_mesh_view:$('mesh-version').value==='fused'&&r.fused_mesh_view?r.fused_mesh_view:$('mesh-version').value==='clean'&&r.cleaned_mesh_view?r.cleaned_mesh_view:r.mesh_view||r.generated_mesh_view;
      $('mesh-empty').textContent='Loading reconstructed mesh…';
      const response=await fetch(artifactUrl(sid,base+'/' +meshChoice.file));
      if(!response.ok)throw new Error('Mesh preview could not be downloaded');
      const reader=response.body.getReader(), chunks=[];let received=0;const total=Number(response.headers.get('Content-Length'));
      while(true){const {done,value}=await reader.read();if(done)break;chunks.push(value);received+=value.length;
        if(version!==fullVersion){await reader.cancel();return;}
        $('mesh-info').textContent=`Loading mesh: ${total?Math.round(100*received/total)+'%':Math.round(received/1024)+' KB'}`;
      }
      const bytes=new Uint8Array(received);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
      const mesh=JSON.parse(new TextDecoder().decode(bytes));
      if(version!==fullVersion)return;
      if(!meshViewer)throw new Error('WebGL unavailable; download the mesh PLY.');
      meshViewer.load(mesh,false,mesh.texture?artifactUrl(sid,base+'/'+meshChoice.file.slice(0,meshChoice.file.lastIndexOf('/')+1)+mesh.texture):null);$('mesh-empty').hidden=true;$('mesh-progress').value=4;$('mesh-stage-count').textContent='4 / 4 stages complete';
      const generated=meshChoice.mesh_file.endsWith('.glb');
      $('mesh-state').textContent=generated?'AI ALTERNATIVE':'COMPLETE';
      $('mesh-info').textContent=`${generated?'AI-generated from one photo; may invent details. ':''}${meshChoice.triangles_total.toLocaleString()} faces in full mesh · ${meshChoice.triangles_displayed.toLocaleString()} triangles displayed`;
      $('mesh-download').textContent=meshChoice.mesh_file.endsWith('.glb')?'Download textured GLB':'Download full mesh PLY';
      $('mesh-download').href=artifactUrl(sid,base+'/' +meshChoice.mesh_file);$('mesh-download').hidden=false;
    }
  }catch(e){if(version===fullVersion){$('colmap-empty').querySelector('p').textContent=e.message;$('mesh-empty').hidden=false;$('mesh-empty').textContent=e.message;status(e.message,true);}}
};
api('/api/runner').then(data=>{
  $('engine-status').textContent=data.vggt?(data.worldmirror2?'VGGT and WorldMirror 2.0 available. COLMAP is retained but unused.':'VGGT available. For WorldMirror 2.0 run scripts/setup_worldmirror.ps1.'):'VGGT is not installed. Run scripts/setup_vggt.ps1.';
  controls();window.loadFullResult($('scans').value);
  const active=data.jobs.find(j=>['queued','running'].includes(j.status));
  if(active&&!busy)watchJob(active.id);
}).catch(e=>status(e.message,true));
