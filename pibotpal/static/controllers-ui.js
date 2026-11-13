// SPDX-License-Identifier: MIT
const $=id=>document.getElementById(id);
const faceLabels={South:'A / ×',East:'B / ○',West:'X / □',North:'Y / △',C:'C',Z:'Z',L1:'L1',R1:'R1',L2:'L2',R2:'R2',Select:'Select',Start:'Start',Mode:'Mode'};
const canonical=name=>String(name).toLowerCase().replaceAll(/[^a-z0-9]/g,'');
const make=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;};

export function initControllers({call}){
  const section=$('admin-controllers');
  section.innerHTML=`
    <div class="admin-intro admin-section-intro"><div><h2>Controllers</h2><p>Watch every input, then choose what it does.</p></div><button id="controller-refresh" class="outline">Refresh</button></div>
    <div id="controller-message" class="notice" role="status" hidden></div>
    <article class="panel controller-monitor">
      <div class="panel-heading"><h2>Live gamepad</h2><div class="heading-actions"><span id="controller-transport" class="caption">Connecting…</span><span id="controller-status" class="tag">NOT CONNECTED</span></div></div>
      <div class="panel-body">
        <div class="controller-gate"><span id="controller-gate-state" class="tag">WAITING</span><p id="controller-gate-message">Connect a USB gamepad or receiver to the Pi.</p></div>
        <div class="controller-visual-layout">
          <div class="gamepad-visual" role="group" aria-label="Live controller inputs">
            <div class="pad-shoulders"><span data-pad-button="L2">L2</span><span data-pad-button="L1">L1</span><span data-pad-button="R1">R1</span><span data-pad-button="R2">R2</span></div>
            <div class="pad-main-row">
              <div class="pad-dpad" role="group" aria-label="Directional pad"><span class="pad-up" data-pad-axis="haty:-">↑</span><span class="pad-left" data-pad-axis="hatx:-">←</span><i></i><span class="pad-right" data-pad-axis="hatx:+">→</span><span class="pad-down" data-pad-axis="haty:+">↓</span></div>
              <div class="pad-center"><span data-pad-button="Select">Select</span><span data-pad-button="Start">Start</span></div>
              <div class="pad-faces" role="group" aria-label="Face buttons"><span class="pad-north" data-pad-button="North">Y / △</span><span class="pad-west" data-pad-button="West">X / □</span><span class="pad-east" data-pad-button="East">B / ○</span><span class="pad-south" data-pad-button="South">A / ×</span></div>
            </div>
            <div class="pad-sticks"><div><div id="controller-left-stick" class="pad-stick"><i></i></div><span>Left stick</span></div><div><div id="controller-right-stick" class="pad-stick"><i></i></div><span>Right stick</span></div></div>
            <p class="controller-visual-legend"><span class="held-key"></span> Held <span class="tap-key"></span> Recent tap</p>
          </div>
          <div class="controller-live-details"><h3>Sticks & axes</h3><div id="controller-axes"><p class="caption">No axes available.</p></div><h3>Buttons</h3><div id="controller-buttons" class="controller-buttons"></div><p id="controller-activity" class="caption">Waiting for input</p></div>
        </div>
        <p id="controller-error" class="notice error" role="status" hidden></p>
        <details class="controller-device-details"><summary>Receiver details</summary><div id="controller-devices" class="controller-devices"></div><p id="controller-driver-note" class="caption"></p></details>
      </div>
    </article>
    <div class="controller-settings-grid">
      <article class="panel controller-mapping-panel"><div class="panel-heading"><h2>Action mapping</h2><span id="controller-map-state" class="tag">SAVED</span></div>
        <form id="controller-mapping-form" class="panel-body">
          <p class="caption">Mappings take effect when both system motion and the controller are armed. Start always toggles controller arming. Saving a mapping never moves the robot.</p>
          <div class="controller-map-options"><label>Movement speed (%)<input id="controller-map-speed" type="number" min="1" max="80" step="1" value="40" required></label><label>Stick deadzone<input id="controller-map-deadzone" type="number" min="0.15" max="0.8" step="0.05" value="0.35" required></label></div>
          <div class="controller-mapping-scroll"><table class="controller-mapping-table"><thead><tr><th scope="col">Input</th><th scope="col">Action</th><th scope="col">Trigger</th></tr></thead><tbody id="controller-mapping-rows"></tbody></table></div>
          <div class="admin-actions"><button id="controller-map-save" class="primary" type="submit" disabled>Save mapping</button><button id="controller-map-reset" type="button" disabled>Restore defaults</button></div>
          <p id="controller-map-hint" class="caption">Waiting for the controller mapping…</p>
        </form>
      </article>
      <article class="panel controller-event-panel"><div class="panel-heading"><h2>Recent input</h2><span id="controller-event-count" class="tag">0 EVENTS</span></div><ol id="controller-event-log" class="controller-event-log"><li class="caption">Press a button or move a stick to see it here.</li></ol></article>
    </div>`;
  let active=false,source=null,pollTimer=null,retryTimer=null,loading=false,generation=0,transportEpoch=0,connectionStartedAt=0,streamOpened=false,lastStreamAt=0;
  let data={},inputSignature='',deviceSignature='',mappingSignature='',eventSignature='',lastSeq=-1,dirty=false,saving=false;
  const axes=[],buttons=[],pulses=new Map(),recentAxes=new Map(),mappingRows=[];
  const statusLabels={system_disarmed:'SYSTEM DISARMED',controller_disarmed:'CONTROLLER DISARMED',ready:'READY',waiting_neutral:'CENTER STICKS',running:'RUNNING',blocked:'BLOCKED',offline:'OFFLINE'};
  const message=(text,kind='')=>{$('controller-message').textContent=text;$('controller-message').className='notice '+kind;$('controller-message').hidden=!text;};
  const visible=()=>active&&!document.hidden;
  const changed=()=>{dirty=true;$('controller-map-state').textContent='UNSAVED';$('controller-map-state').className='tag orange';};
  function inputValue(id){
    const parts=id.split(':');
    if(parts[0]==='button'){const index=(data.button_names||[]).findIndex(name=>canonical(name)===canonical(parts[1]));return Boolean((data.raw_buttons||data.buttons||[])[index]);}
    const index=(data.axis_names||[]).findIndex(name=>canonical(name)===canonical(parts[1]));const value=Number((data.raw_axes||data.axes||[])[index]||0);
    return parts[2]==='-'?value<-.1:value>.1;
  }
  function pulse(id,value){pulses.set(id,performance.now()+350);if(id.startsWith('axis:'))recentAxes.set(canonical(id.split(':')[1]),{value,until:performance.now()+350});}
  function renderPulse(){
    for(const element of section.querySelectorAll('[data-pad-button]')){const id='button:'+element.dataset.padButton;const held=inputValue(id);element.classList.toggle('is-held',held);element.classList.toggle('is-tapped',!held&&(pulses.get(id)||0)>performance.now());element.setAttribute('aria-label',`${faceLabels[element.dataset.padButton]||element.dataset.padButton}: ${held?'pressed':'released'}`);}
    for(const element of section.querySelectorAll('[data-pad-axis]')){const [name,direction]=element.dataset.padAxis.split(':');const index=(data.axis_names||[]).findIndex(axis=>canonical(axis)===name);const axis=(data.axis_names||[])[index];const value=Number((data.raw_axes||data.axes||[])[index]||0);const held=direction==='-'?value<-.1:value>.1;const id='axis:'+axis+':'+direction;const until=pulses.get(id)||0;element.classList.toggle('is-held',held);element.classList.toggle('is-tapped',!held&&until>performance.now());}
    buttons.forEach(({element,name,index})=>{const held=Boolean((data.raw_buttons||data.buttons||[])[index]);element.classList.toggle('is-held',held);element.classList.toggle('is-tapped',!held&&(pulses.get('button:'+name)||0)>performance.now());element.setAttribute('aria-label',`${faceLabels[name]||name}: ${held?'pressed':'released'}`);});
    for(const [id,xNames,yNames] of [['controller-left-stick',['X'],['Y']],['controller-right-stick',['Z','RX'],['RZ','RY']]]){
      const value=names=>{const index=(data.axis_names||[]).findIndex(name=>names.includes(name));const current=Number((data.raw_axes||data.axes||[])[index]||0);const recent=recentAxes.get(canonical((data.axis_names||[])[index]));return {current,recent:recent&&recent.until>performance.now()?recent.value:0};};
      const x=value(xNames),y=value(yNames),held=Math.abs(x.current)>.05||Math.abs(y.current)>.05;const tapped=!held&&(Math.abs(x.recent)>.05||Math.abs(y.recent)>.05);const stick=$(id);stick.classList.toggle('is-held',held);stick.classList.toggle('is-tapped',tapped);stick.querySelector('i').style.transform=`translate(${(held?x.current:x.recent)*25}px,${(held?y.current:y.recent)*25}px)`;stick.setAttribute('aria-label',`${id.includes('left')?'Left':'Right'} stick: X ${x.current.toFixed(2)}, Y ${y.current.toFixed(2)}`);
    }
  }
  function buildInputs(next){
    const signature=JSON.stringify([next.active_device_id,next.axis_names,next.button_names,next.axes?.length,next.buttons?.length]);if(signature===inputSignature)return;inputSignature=signature;
    axes.length=0;buttons.length=0;$('controller-axes').replaceChildren();$('controller-buttons').replaceChildren();
    (next.axis_names||[]).forEach((name,index)=>{const row=make('label','controller-axis');const meter=make('meter');meter.min=-1;meter.max=1;meter.setAttribute('aria-label',name);const output=make('output');row.append(make('span','',name),meter,output);$('controller-axes').append(row);axes.push({meter,output});});
    (next.button_names||[]).forEach((name,index)=>{const element=make('span','controller-button-chip',faceLabels[name]&&faceLabels[name]!==name?`${faceLabels[name]} · ${name}`:name);$('controller-buttons').append(element);buttons.push({element,name,index});});
    if(!axes.length)$('controller-axes').append(make('p','caption','No axes available.'));
    if(!buttons.length)$('controller-buttons').append(make('p','caption','No buttons available.'));
  }
  function renderMapping(mapping,force=false){
    if(!mapping||!Array.isArray(mapping.inputs))return;
    const signature=JSON.stringify(mapping);if(!force&&(dirty||saving||signature===mappingSignature))return;mappingSignature=signature;
    $('controller-map-speed').value=mapping.speed??40;$('controller-map-deadzone').value=mapping.deadzone??.35;
    const bindings=new Map((mapping.bindings||[]).map(binding=>[binding.input,binding]));const actions=mapping.actions||[];const reserved=new Set(mapping.reserved||['button:Start']);mappingRows.length=0;$('controller-mapping-rows').replaceChildren();
    const inputs=[...mapping.inputs];for(const id of reserved)if(!inputs.some(input=>input.id===id))inputs.push({id,label:id.replace('button:','')});
    for(const input of inputs){
      const row=make('tr');const label=make('th','',input.label||input.id);label.scope='row';const actionCell=make('td'),modeCell=make('td');row.append(label,actionCell,modeCell);
      if(reserved.has(input.id)){actionCell.append(make('span','controller-reserved','Controller arm / disarm'));modeCell.append(make('span','caption','Reserved'));}
      else{
        const action=make('select');action.setAttribute('aria-label',`Action for ${input.label||input.id}`);const unassigned=make('option','','Unassigned');unassigned.value='';action.append(unassigned);
        for(const item of actions){const option=make('option','',item.label||item.id);option.value=item.id;action.append(option);}action.value=bindings.get(input.id)?.action||'';
        const mode=make('select');mode.setAttribute('aria-label',`Trigger for ${input.label||input.id}`);for(const [value,labelText] of [['press','Press'],['hold','Hold']]){const option=make('option','',labelText);option.value=value;mode.append(option);}
        mode.value=bindings.get(input.id)?.mode||'press';
        const updateMode=()=>{const hold=actions.find(item=>item.id===action.value)?.hold===true;mode.disabled=!hold;if(!hold)mode.value='press';};updateMode();
        action.addEventListener('change',()=>{updateMode();changed();});mode.addEventListener('change',changed);actionCell.append(action);modeCell.append(mode);mappingRows.push({id:input.id,action,mode});
      }
      $('controller-mapping-rows').append(row);
    }
    dirty=false;$('controller-map-state').textContent='SAVED';$('controller-map-state').className='tag';$('controller-map-save').disabled=false;$('controller-map-reset').disabled=false;$('controller-map-hint').textContent='Hold repeats bounded steps; release stops them. Center the sticks after arming, reconnecting, or editing a mapping. A 10-second hold requires release before continuing.';
  }
  function render(next){
    section.querySelector('.controller-visual-layout').classList.remove('inputs-stale');
    data=next;buildInputs(next);const connected=Boolean(next.connected);$('controller-status').textContent=connected?'RECEIVER CONNECTED':'NOT CONNECTED';$('controller-status').className='tag '+(connected?'teal':'');
    const control=next.control||{};$('controller-gate-state').textContent=statusLabels[control.state]||(!connected?'OFFLINE':next.enabled===false?'CONTROLLER DISARMED':'INPUT MONITOR');$('controller-gate-state').className='tag '+(control.state==='ready'?'teal':control.state==='running'?'orange':'');$('controller-gate-message').textContent=control.message||'Start toggles controller arming. System motion has a separate Arm motion control in the header.';
    const error=control.error||next.error||'';$('controller-error').textContent=error;$('controller-error').hidden=!error;$('controller-driver-note').textContent=next.note||'Receiver presence does not establish wireless pairing; press a control to verify live events.';
    axes.forEach(({meter,output},index)=>{const value=Number((next.raw_axes||next.axes||[])[index]||0);meter.value=value;output.textContent=value.toFixed(2);});
    const events=next.recent_events||[];const max=events.length?Math.max(...events.map(event=>Number(event.seq)||0)):-1;if(max<lastSeq)lastSeq=-1;
    for(const event of events){if(Number(event.seq)<=lastSeq)continue;if(Date.now()/1000-Number(event.time||0)<2){if(event.kind==='button'&&event.value)pulse('button:'+event.name,event.value);else if(event.kind==='axis'&&Math.abs(Number(event.value))>.1)pulse('axis:'+event.name+':'+(event.value<0?'-':'+'),Number(event.value));}}
    lastSeq=Math.max(lastSeq,max);renderPulse();
    const nextEvents=JSON.stringify(events.map(event=>event.seq));if(nextEvents!==eventSignature){eventSignature=nextEvents;$('controller-event-log').replaceChildren();for(const event of events.slice(-12).reverse()){const item=make('li');const name=event.kind==='button'?(faceLabels[event.name]||event.name):event.name;const value=event.kind==='button'?(event.value?'pressed':'released'):Number(event.value).toFixed(2);const time=event.time?new Date(Number(event.time)*1000).toLocaleTimeString():'';item.append(make('time','',time),make('strong','',name),make('span','',value));$('controller-event-log').append(item);}if(!events.length)$('controller-event-log').append(make('li','caption','Press a button or move a stick to see it here.'));}
    const count=Number(next.input_revision??next.events_seen)||0;$('controller-event-count').textContent=`${count} EVENTS`;$('controller-activity').textContent=count?`${count} input events received`:'Move a stick or press a button';
    const devices=next.devices||[],signature=JSON.stringify(devices);if(signature!==deviceSignature){deviceSignature=signature;$('controller-devices').replaceChildren();for(const device of devices){const row=make('div','controller-device');row.append(make('strong','',device.name||'USB gamepad'),make('p','caption',`USB ${device.vendor_id||'?'}:${device.product_id||'?'}`),make('code','',device.stable_path||device.path||device.id||''));if(device.readable===false)row.append(make('p','caption','Input device cannot be read.'));$('controller-devices').append(row);}if(!devices.length)$('controller-devices').append(make('p','caption','No USB joystick input device detected.'));}
    renderMapping(next.mapping);
  }
  function markStale(){
    data={...data,raw_axes:(data.axis_names||[]).map(()=>0),raw_buttons:(data.button_names||[]).map(()=>false)};pulses.clear();recentAxes.clear();renderPulse();
    section.querySelector('.controller-visual-layout').classList.add('inputs-stale');axes.forEach(({meter,output})=>{meter.value=0;output.textContent='—';});
    $('controller-status').textContent='UNAVAILABLE';$('controller-status').className='tag';$('controller-gate-state').textContent='OFFLINE';$('controller-gate-state').className='tag';$('controller-gate-message').textContent='Live inputs are unavailable. Reconnecting…';
  }
  async function refresh(){
    if(loading||!visible())return;loading=true;const requested=generation,epoch=transportEpoch;
    try{const result=await call('controller_get',{}, {timeout:2500});if(visible()&&requested===generation&&epoch===transportEpoch)render(result);}
    catch(error){if(visible()&&requested===generation){$('controller-transport').textContent='Reconnecting…';markStale();}}
    finally{loading=false;}
  }
  function closeTransport(){generation++;transportEpoch++;source?.close();source=null;clearInterval(pollTimer);clearTimeout(retryTimer);pollTimer=retryTimer=null;}
  function startPolling(){if(!visible())return;$('controller-transport').textContent='Polling';if(!pollTimer)pollTimer=setInterval(refresh,100);refresh();}
  function connect(){
    if(!visible()||source)return;const requested=generation;
    if(typeof EventSource==='undefined'){startPolling();return;}
    connectionStartedAt=performance.now();streamOpened=false;lastStreamAt=0;
    const stream=new EventSource('/controllers/events');source=stream;$('controller-transport').textContent='Connecting…';
    stream.onopen=()=>{if(requested!==generation||!visible()||source!==stream)return;streamOpened=true;lastStreamAt=performance.now();transportEpoch++;clearInterval(pollTimer);pollTimer=null;$('controller-transport').textContent='Live · 20 Hz';};
    stream.onmessage=event=>{if(requested!==generation||!visible()||source!==stream)return;try{render(JSON.parse(event.data));lastStreamAt=performance.now();}catch{}};
    stream.onerror=()=>{if(requested!==generation||!visible()||source!==stream)return;stream.close();if(source===stream)source=null;transportEpoch++;markStale();startPolling();clearTimeout(retryTimer);retryTimer=setTimeout(connect,5000);};
  }
  async function save(reset=false){
    if(saving)return;saving=true;$('controller-map-save').disabled=true;$('controller-map-reset').disabled=true;
    try{const result=reset?await call('controller_reset_mapping'):await call('controller_save_mapping',{speed:Number($('controller-map-speed').value),deadzone:Number($('controller-map-deadzone').value),bindings:mappingRows.filter(row=>row.action.value).map(row=>({input:row.id,action:row.action.value,mode:row.mode.value}))});dirty=false;render(result);renderMapping(result.mapping,true);message(reset?'Default mapping restored.':'Mapping saved.');}
    catch(error){message(error.message,'error');}
    finally{saving=false;$('controller-map-save').disabled=!data.mapping;$('controller-map-reset').disabled=!data.mapping;}
  }
  $('controller-map-speed').addEventListener('input',changed);$('controller-map-deadzone').addEventListener('input',changed);$('controller-mapping-form').addEventListener('submit',event=>{event.preventDefault();save();});$('controller-map-reset').addEventListener('click',()=>save(true));$('controller-refresh').addEventListener('click',refresh);
  setInterval(()=>{if(visible()){renderPulse();if(source&&performance.now()-(streamOpened?lastStreamAt:connectionStartedAt)>(streamOpened?3000:5000))source.onerror?.();}},50);
  document.addEventListener('visibilitychange',()=>{if(document.hidden){closeTransport();$('controller-transport').textContent='Paused';}else if(active){connect();refresh();}});
  return {setActive(value){if(active===value){if(value){connect();refresh();}return;}active=value;closeTransport();if(active){connect();refresh();}else $('controller-transport').textContent='Paused';},refresh};
}
