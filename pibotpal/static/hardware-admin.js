// SPDX-License-Identifier: MIT
const $=id=>document.getElementById(id);
const optionLabel=key=>key.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase());
export function initHardware({call}) {
  const section=document.createElement('section');section.className='hardware-admin';section.setAttribute('aria-labelledby','hardware-title');
  section.innerHTML=`<div class="admin-intro"><div><p class="eyebrow">ROBOT & EXPANSION DEVICES</p><h2 id="hardware-title">Hardware inventory</h2><p>Review the hardware on this Pi and record options for planned additions.</p></div><button id="hardware-refresh" class="outline">Refresh hardware</button></div><div id="hardware-notice" class="notice" role="status" aria-live="polite" hidden></div><p id="hardware-host" class="caption hardware-host">Reading this robot…</p><div id="hardware-discovered" class="hardware-discovered"></div><div id="hardware-devices" class="hardware-grid"></div>`;
  $('admin').append(section);
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
    for(const device of data.devices||[]){
      const card=document.createElement('article');card.className='panel hardware-device';
      const header=document.createElement('div');header.className='panel-heading';const title=document.createElement('h3');title.textContent=device.name||optionLabel(device.id);const badge=document.createElement('span');badge.className='tag';badge.textContent=device.detected===true?'DETECTED':device.configured?'CONFIGURED':'NOT VERIFIED';if(device.detected===true)badge.classList.add('teal');header.append(title,badge);card.append(header);
      const form=document.createElement('form');form.className='admin-form';
      const status=document.createElement('p');status.className='caption';status.textContent=typeof device.status==='object'?JSON.stringify(device.status):device.status||device.kind||'';form.append(status);
      if(device.notes){const notes=document.createElement('p');notes.className='caption';notes.textContent=Array.isArray(device.notes)?device.notes.join(' '):device.notes;form.append(notes);}
      const fields=document.createElement('div');fields.className='form-grid';for(const [key,value] of Object.entries(device.options||{}))fields.append(field(key,value));form.append(fields);
      if(Object.keys(device.options||{}).length){const save=document.createElement('button');save.type='submit';save.textContent='Save hardware options';form.append(save);
        form.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;try{const options={};for(const input of fields.querySelectorAll('[data-option]')){const kind=input.dataset.kind;options[input.dataset.option]=kind==='boolean'?input.checked:kind==='number'?Number(input.value):kind==='object'?JSON.parse(input.value):kind==='nullable'?(input.value||null):input.value;}
          await call('hardware_save',{device_id:device.id,options});await refresh();message(`${device.name||device.id} options saved. Check the device notes for driver availability.`);
        }catch(error){message(error.message,'error');}finally{save.disabled=false;}});
      }
      card.append(form);$('hardware-devices').append(card);
    }
  }
  async function refresh(){if(loading)return;loading=true;try{render(await call('hardware_get'));}catch(error){message(error.message,'error');}finally{loading=false;}}
  $('hardware-refresh').addEventListener('click',refresh);
  return {refresh};
}
