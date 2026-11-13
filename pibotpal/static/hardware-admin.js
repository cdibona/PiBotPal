// SPDX-License-Identifier: MIT
const $=id=>document.getElementById(id);
const optionLabel=key=>key.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase());
export function initHardware({call}) {
  const section=document.createElement('section');section.className='hardware-admin';section.setAttribute('aria-labelledby','hardware-title');
  section.innerHTML=`<div class="admin-intro"><div><p class="eyebrow">ROBOT & EXPANSION DEVICES</p><h2 id="hardware-title">Hardware inventory</h2><p>Review the hardware on this Pi and record options for planned additions.</p></div><button id="hardware-refresh" class="outline">Refresh hardware</button></div><div id="hardware-notice" class="notice" role="status" aria-live="polite" hidden></div><p id="hardware-host" class="caption hardware-host">Reading this robot…</p><div id="hardware-discovered" class="hardware-discovered"></div><div id="hardware-devices" class="hardware-grid"></div>`;
  ($('admin-hardware')||$('admin')).append(section);
  let loading=false;
  function message(text,kind=''){$('hardware-notice').textContent=text;$('hardware-notice').className=`notice ${kind}`;$('hardware-notice').hidden=!text;}
  function field(key,value){
    const label=document.createElement('label');label.textContent=optionLabel(key);let input;
    if(typeof value==='boolean'){label.className='check-label';input=document.createElement('input');input.type='checkbox';input.checked=value;label.prepend(input);}
    else if(value!==null&&typeof value==='object'){input=document.createElement('textarea');input.value=JSON.stringify(value,null,2);input.rows=3;label.append(input);}
    else{input=document.createElement('input');input.type=typeof value==='number'?'number':'text';if(input.type==='number')input.step='any';input.value=value??'';label.append(input);}
    input.dataset.option=key;input.dataset.kind=value===null?'nullable':typeof value;return label;
  }
  function render(data){
    const host=data.host||{};$('hardware-host').textContent=[host.model,Number.isFinite(host.memory_total_mb)?`${Math.round(host.memory_total_mb)} MB RAM`:null,Number.isFinite(host.memory_available_mb)?`${Math.round(host.memory_available_mb)} MB available`:null].filter(Boolean).join(' · ')||'Hardware configuration is stored on this robot.';
    $('hardware-discovered').replaceChildren();
    const discovered=data.discovered||{};
    for(const [key,title] of [['serial_ports','Serial ports'],['usb_cameras','USB cameras'],['coral_usb_ids','Coral USB devices']]){
      const row=document.createElement('div');const label=document.createElement('strong');label.textContent=title;const detail=document.createElement('span');
      const items=discovered[key]||[];detail.textContent=items.length?items.map(item=>typeof item==='string'?item:JSON.stringify(item)).join(' · '):'None detected';
      row.append(label,detail);$('hardware-discovered').append(row);
    }
    $('hardware-devices').replaceChildren();
    const groups=new Map();
    for(const [key,title] of [['current','Current hardware'],['expansion','Expansion preferences']]){
      const group=document.createElement('section');group.className='hardware-group';const heading=document.createElement('h3');heading.textContent=title;
      const grid=document.createElement('div');grid.className='hardware-group-grid';group.append(heading,grid);$('hardware-devices').append(group);groups.set(key,{group,grid});
    }

    for(const device of data.devices||[]){
      const card=document.createElement('details');card.className='panel hardware-device';
      const header=document.createElement('summary');header.className='panel-heading';const title=document.createElement('h3');title.textContent=device.name||optionLabel(device.id);const badge=document.createElement('span');badge.className='tag';badge.textContent=device.detected===true?'DETECTED':device.configured?'CONFIGURED':'NOT VERIFIED';if(device.detected===true)badge.classList.add('teal');header.append(title,badge);card.append(header);
      const form=document.createElement('form');form.className='admin-form';
      const status=document.createElement('p');status.className='caption';status.textContent=typeof device.status==='object'?JSON.stringify(device.status):device.status||device.kind||'';form.append(status);
      if(device.notes){const notes=document.createElement('p');notes.className='caption';notes.textContent=Array.isArray(device.notes)?device.notes.join(' '):device.notes;form.append(notes);}
      const fields=document.createElement('div');fields.className='form-grid';for(const [key,value] of Object.entries(device.options||{}))fields.append(field(key,value));form.append(fields);
      if(Object.keys(device.options||{}).length){const save=document.createElement('button');save.type='submit';save.textContent='Save hardware options';form.append(save);
        form.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;try{const options={};for(const input of fields.querySelectorAll('[data-option]')){const kind=input.dataset.kind;options[input.dataset.option]=kind==='boolean'?input.checked:kind==='number'?Number(input.value):kind==='object'?JSON.parse(input.value):kind==='nullable'?(input.value||null):input.value;}
          await call('hardware_save',{device_id:device.id,options});await refresh();message(`${device.name||device.id} options saved. Check the device notes for driver availability.`);
        }catch(error){message(error.message,'error');}finally{save.disabled=false;}});
      }
      card.append(form);groups.get(['rear_camera','lidar','coral','imu'].includes(device.id)?'expansion':'current').grid.append(card);
    }
    for(const {group,grid} of groups.values())group.hidden=!grid.childElementCount;
  }
  async function refresh(){if(loading)return;loading=true;try{render(await call('hardware_get'));}catch(error){message(error.message,'error');}finally{loading=false;}}
  const controllerPanel=document.createElement('article');controllerPanel.className='panel controller-monitor';
  controllerPanel.innerHTML=`<div class="panel-heading"><h2>USB gamepad</h2><span id="controller-status" class="tag">NOT CHECKED</span></div><div class="panel-body"><p id="controller-note" class="caption">Checking attached game controllers…</p><div id="controller-devices" class="controller-devices"></div><div class="controller-input-grid"><div><h3>Axes</h3><div id="controller-axes"></div></div><div><h3>Buttons</h3><div id="controller-buttons" class="controller-buttons"></div></div></div><p id="controller-error" class="caption"></p><p class="caption">Input monitoring only. Controller buttons do not arm or move the crawler.</p></div>`;
  section.prepend(controllerPanel);let controllerLoading=false;
  async function refreshController(){
    if(controllerLoading)return;controllerLoading=true;
    try{
      const data=await call('controller_get');$('controller-status').textContent=data.connected?'CONNECTED':'NOT CONNECTED';$('controller-status').className='tag '+(data.connected?'teal':'');
      $('controller-note').textContent=data.note||'USB receiver detection does not establish the controller brand or wireless pairing.';
      $('controller-devices').replaceChildren();
      for(const device of data.devices||[]){const row=document.createElement('p');row.className='caption';row.textContent=`${device.name||'USB gamepad'} · ${device.vendor_id||'?'}:${device.product_id||'?'} · ${device.stable_path||device.path||device.id}${device.readable===false?' · no read permission':''}`;$('controller-devices').append(row);}
      if(!data.devices?.length){const row=document.createElement('p');row.className='caption';row.textContent='No USB joystick input device detected.';$('controller-devices').append(row);}
      $('controller-axes').replaceChildren();
      (data.axes||[]).forEach((value,index)=>{const row=document.createElement('label');row.className='controller-axis';const label=document.createElement('span');label.textContent=data.axis_names?.[index]||`Axis ${index+1}`;const meter=document.createElement('meter');meter.min=-1;meter.max=1;meter.value=value;const output=document.createElement('output');output.textContent=Number(value).toFixed(2);row.append(label,meter,output);$('controller-axes').append(row);});
      $('controller-buttons').replaceChildren();(data.buttons||[]).forEach((pressed,index)=>{const button=document.createElement('span');button.className='tag '+(pressed?'teal':'');button.textContent=data.button_names?.[index]||`Button ${index+1}`;button.setAttribute('aria-label',`${button.textContent}: ${pressed?'pressed':'released'}`);$('controller-buttons').append(button);});
      $('controller-error').textContent=data.error||'';
    }catch(error){$('controller-status').textContent='UNAVAILABLE';$('controller-error').textContent=error.message;}
    finally{controllerLoading=false;}
  }
  setInterval(()=>{if(!$('admin').hidden&&!$('admin-hardware')?.hidden)refreshController();},1000);
  $('hardware-refresh').addEventListener('click',()=>{refresh();refreshController();});
  return {refresh(){refreshController();return refresh();}};
}
