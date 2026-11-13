// SPDX-License-Identifier: MIT
const $=id=>document.getElementById(id);
const label=camera=>camera.name||camera.model||camera.id;
export function initCameraViews({call,notice,CameraFeed,getStatus}){
  const dashboard=new Map(),previews=new Map();let cameras=[],primary=null,signature='',active=false,online=true,loading=false;
  const root=$('admin-cameras');
  root.innerHTML=`<div class="admin-intro"><div><p class="eyebrow">CSI CAMERA INPUTS</p><h2>Every camera, independently.</h2><p>View detected CSI sensors, control each capture stream, and choose the primary camera used by existing clients.</p></div><button id="cameras-refresh" class="outline">Refresh cameras</button></div><div id="cameras-notice" class="notice" role="status" hidden></div><div id="camera-admin-list" class="camera-admin-grid"></div>`;
  function message(text,type=''){$('cameras-notice').textContent=text;$('cameras-notice').className='notice '+type;$('cameras-notice').hidden=!text;}
  function createCard(camera,container,isAdmin){
    const card=document.createElement('article');card.className='panel csi-camera-card';
    const heading=document.createElement('div');heading.className='panel-heading';const title=document.createElement('h3');title.textContent=label(camera);const badge=document.createElement('span');badge.className='tag';heading.append(title,badge);
    const stage=document.createElement('div');stage.className='csi-camera-stage';const image=document.createElement('img');image.alt=`Live view from ${label(camera)}`;image.hidden=true;const empty=document.createElement('p');empty.className='caption';stage.append(image,empty);
    const body=document.createElement('div');body.className='panel-body';const identity=document.createElement('p');identity.className='caption camera-hardware-id';identity.textContent=camera.hardware_id||camera.id;body.append(identity);
    const actions=document.createElement('div');actions.className='admin-actions';const toggle=document.createElement('button');toggle.type='button';toggle.addEventListener('click',async()=>{toggle.disabled=true;try{const current=cameras.find(c=>c.id===camera.id);await call('camera',{camera_id:camera.id,enabled:!current?.enabled});await refresh();}catch(error){message(error.message,'error');notice(error.message,'error');}finally{toggle.disabled=false;}});actions.append(toggle);
    const primaryButton=document.createElement('button');primaryButton.type='button';primaryButton.textContent='Use as primary';primaryButton.addEventListener('click',()=>choose(camera.id));actions.append(primaryButton);
    const snapshot=document.createElement('a');snapshot.className='text-link';snapshot.href=camera.snapshot_url;snapshot.download=`${camera.id}.jpg`;snapshot.textContent='Save snapshot ↗';actions.append(snapshot);body.append(actions);
    const detail=document.createElement('p');detail.className='caption';body.append(detail);
    const diagnostics=document.createElement('details');diagnostics.className='capture-diagnostics';const summary=document.createElement('summary');summary.textContent='Capture diagnostics';const values=document.createElement('pre');diagnostics.append(summary,values);if(isAdmin)body.append(diagnostics);
    card.append(heading,stage,body);container.append(card);
    const feed=new CameraFeed(image,state=>{const live=state==='live';image.hidden=!live;empty.hidden=live;if(state!=='off')badge.textContent=live?'LIVE':'RECONNECTING';empty.textContent=state==='off'?'Camera capture is off':'Waiting for camera frames…';},camera.video_url);
    image.addEventListener('error',()=>{if(feed.enabled)feed.controller?.abort();});
    return {camera,card,feed,badge,toggle,primaryButton,snapshot,detail,values,image,empty,isAdmin};
  }
  async function choose(id){try{await call('camera_select',{camera_id:id});await refresh();notice('Primary camera changed. Existing client video URLs now use this camera.');}catch(error){message(error.message,'error');}}
  function rebuild(){
    for(const map of [dashboard,previews]){for(const view of map.values())view.feed.setEnabled(false);map.clear();}
    $('other-camera-feeds').replaceChildren();$('camera-admin-list').replaceChildren();
    for(const camera of cameras){if(camera.id!==primary)dashboard.set(camera.id,createCard(camera,$('other-camera-feeds'),false));previews.set(camera.id,createCard(camera,$('camera-admin-list'),true));}
    $('other-camera-feeds').hidden=!dashboard.size;
    if(!cameras.length){const empty=document.createElement('p');empty.className='caption';empty.textContent='No CSI camera sensors were detected. Check ribbon connections and the Pi camera configuration.';$('camera-admin-list').append(empty);}
    const select=$('primary-camera');select.replaceChildren();
    for(const camera of cameras){const option=document.createElement('option');option.value=camera.id;option.textContent=`${label(camera)}${camera.id===primary?' · primary':''}`;select.append(option);}select.value=primary||'';select.disabled=!cameras.length;
    $('primary-camera-control').hidden=!cameras.length;
  }
  function sync(){
    const instance=getStatus()?.server_instance_id;
    for(const [map,visible] of [[dashboard,!$('dashboard').hidden],[previews,active]])for(const [id,view] of map){
      const camera=cameras.find(c=>c.id===id);if(!camera)continue;
      view.toggle.textContent=camera.enabled?'Stop capture':'Start capture';view.primaryButton.hidden=id===primary;
      view.detail.textContent=camera.error||`${camera.width||640} × ${camera.height||480}${camera.available?' · live frames':camera.timestamp?' · last capture':''}${id===primary?' · primary camera':''}`;
      if(camera.available&&Number.isFinite(camera.brightness)&&camera.brightness<5)view.detail.textContent+=' · Very dark image: check lens and lighting';
      view.values.textContent=JSON.stringify({brightness:camera.brightness??null,sensor_rotation:camera.sensor_rotation??null,...camera.capture_metadata},null,2);
      view.snapshot.style.pointerEvents=camera.available?'auto':'none';view.snapshot.style.opacity=camera.available?'1':'.4';
      if(!camera.enabled){view.badge.textContent=camera.error?'ERROR':'OFF';view.empty.textContent=camera.error||'Camera capture is off';}
      view.feed.setEnabled(Boolean(camera.enabled&&visible&&online),instance,camera.video_url);
    }
    $('primary-camera').value=primary||'';
  }
  function update(state){
    if(Array.isArray(state.cameras)){
      cameras=state.cameras;primary=state.primary_camera_id||state.camera?.id||cameras[0]?.id||null;
      const next=cameras.map(c=>[c.id,c.model,c.hardware_id,c.video_url,c.snapshot_url].join('|')).join(';')+'#'+primary;
      if(signature!==next){signature=next;rebuild();}
    }
    online=true;sync();
  }
  async function refresh(){if(loading)return;loading=true;try{update(await call('camera_list'));message('');}catch(error){message(error.message,'error');}finally{loading=false;}}
  $('cameras-refresh').addEventListener('click',refresh);$('primary-camera').addEventListener('change',event=>choose(event.target.value));
  return {update,refresh,setActive(value){active=value;sync();if(active)refresh();},disconnect(){online=false;sync();}};
}
