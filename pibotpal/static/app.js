const $=id=>document.getElementById(id);
let status={},connected=false,rpcId=0,initialized=false,polling=false,model=null,previousMic=false,examples=[];
const names=['Right front','Left front','Left rear','Right rear'];
const jointNames=['Knee','Shoulder','Hip'];
const limits=[[-90,90],[-10,90],[-60,60]];
let pending=0, lastServerInstance=null, controllerToggle=null;

// A live stream owns its connection and retries EOF/errors independently of telemetry.
// This also catches short restarts that happen between successful status polls.
class CameraFeed {
  constructor(element, changed, url="/video.mjpg") {
    this.element=element;this.changed=changed;this.enabled=false;this.generation=0;this.url=url;
    this.instance=null;this.controller=null;this.objectUrl=null;this.state='off';
  }
  setEnabled(enabled, instance, url=this.url) {
    const restarted=(instance&&this.instance&&instance!==this.instance)||url!==this.url;
    this.url=url;
    if(instance)this.instance=instance;
    if(enabled===this.enabled&&!restarted)return;
    this.enabled=enabled;this.generation++;
    this.controller?.abort();
    if(this.objectUrl){URL.revokeObjectURL(this.objectUrl);this.objectUrl=null;}
    this.element.removeAttribute('src');
    if(enabled)this.run(this.generation);else this.report('off');
  }
  report(state){this.state=state;this.changed(state);}
  async run(generation) {
    let delay=1000;
    while(this.enabled&&generation===this.generation){
      const controller=new AbortController();this.controller=controller;
      let lastFrame=performance.now();
      const watchdog=setInterval(()=>{if(performance.now()-lastFrame>10000)controller.abort();},1000);
      this.report('connecting');
      try{
        const response=await fetch(`${this.url}${this.url.includes("?")?"&":"?"}t=${Date.now()}`,{signal:controller.signal,cache:'no-store'});
        if(!response.ok||!response.body||!response.headers.get('content-type')?.startsWith('multipart/x-mixed-replace'))throw new Error('Camera stream unavailable');
        const reader=response.body.getReader();let buffered=new Uint8Array(0),length=null;
        while(this.enabled&&generation===this.generation){
          const {done,value}=await reader.read();
          if(done)throw new Error('Camera stream ended');
          if(buffered.length+value.length>16*1024*1024+8192)throw new Error('Camera frame buffer too large');
          const combined=new Uint8Array(buffered.length+value.length);combined.set(buffered);combined.set(value,buffered.length);buffered=combined;
          while(true){
            if(length===null){
              let end=-1;
              for(let i=0;i+3<buffered.length;i++)if(buffered[i]===13&&buffered[i+1]===10&&buffered[i+2]===13&&buffered[i+3]===10){end=i;break;}
              if(end<0){if(buffered.length>8192)throw new Error('Camera headers too large');break;}
              if(end>8192)throw new Error('Camera headers too large');
              const header=new TextDecoder().decode(buffered.subarray(0,end));
              const match=header.match(/(?:^|\r\n)Content-Length:\s*(\d+)\s*(?:\r\n|$)/i);
              length=match?Number(match[1]):0;
              if(!Number.isInteger(length)||length<4||length>16*1024*1024)throw new Error('Invalid camera frame length');
              buffered=buffered.slice(end+4);
            }
            if(buffered.length<length)break;
            const frame=buffered.slice(0,length);buffered=buffered.slice(length);length=null;
            if(frame[0]!==255||frame[1]!==216||frame.at(-2)!==255||frame.at(-1)!==217)throw new Error('Invalid JPEG frame');
            if(!this.enabled||generation!==this.generation)return;
            const next=URL.createObjectURL(new Blob([frame],{type:'image/jpeg'}));
            this.element.src=next;
            if(this.objectUrl)URL.revokeObjectURL(this.objectUrl);
            this.objectUrl=next;this.element.dataset.frameCount=String(Number(this.element.dataset.frameCount||0)+1);lastFrame=performance.now();delay=1000;this.report('live');
          }
          if(buffered.length>16*1024*1024+8192)throw new Error('Camera frame buffer too large');
        }
      }catch(error){
        if(!this.enabled||generation!==this.generation)return;
        this.report('retrying');
      }finally{clearInterval(watchdog);controller.abort();}
      await new Promise(resolve=>setTimeout(resolve,delay));
      delay=Math.min(8000,delay*2);
    }
  }
}

const cameraFeed=new CameraFeed($('camera-feed'),state=>{
  const live=state==='live';
  $('camera-feed').hidden=!live;$('camera-empty').hidden=live;
  if(state==='connecting'||state==='retrying'){
    $('camera-empty').querySelector('strong').textContent='Reconnecting camera';
    $('camera-empty').querySelector('p').textContent='The video feed will resume automatically.';
  }
  $('camera-status').textContent=live?'LIVE':state==='off'?'OFF':'RECONNECTING';
  $('camera-dot').classList.toggle('active',live);
});

async function rpc(method,params={},options={}){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),options.timeout||15000);
  try{
    const response=await fetch('/mcp',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-03-26'},body:JSON.stringify({jsonrpc:'2.0',id:++rpcId,method,params}),signal:controller.signal});
    if(!response.ok)throw new Error(`Server returned ${response.status}: ${(await response.text()).slice(0,180)}`);
    const text=await response.text();let result;
    if(response.headers.get('content-type')?.includes('text/event-stream')){const data=text.split('\n').filter(line=>line.startsWith('data: ')).map(line=>line.slice(6));result=JSON.parse(data.at(-1));}else result=JSON.parse(text);
    if(result.error)throw new Error(result.error.message);
    return result.result;
  }finally{clearTimeout(timeout);}
}
async function call(name,args={},options={}){
  if(!initialized){await rpc('initialize',{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'PiBotPal Web',version:'0.1.0'}});initialized=true;}
  const result=await rpc('tools/call',{name,arguments:args},options);
  const raw=result?.content?.filter(c=>c.type==='text').map(c=>c.text).join('\n')||'';
  if(result?.isError)throw new Error(raw||'The robot could not complete this command.');
  if(result?.structuredContent){const data=result.structuredContent;return Object.keys(data).length===1&&'result' in data?data.result:data;}
  try{return JSON.parse(raw);}catch{return raw;}
}
function notice(message,type=''){const n=$('notice');n.textContent=message;n.className=`notice ${type}`;n.hidden=!message;}
function log(message){const output=$('test-output');if(output.textContent==='Select an experiment to begin.')output.textContent='';output.textContent=`[${new Date().toLocaleTimeString()}] ${typeof message==='string'?message:JSON.stringify(message,null,2)}\n\n${output.textContent}`.slice(0,30000);}
async function command(name,args={},message){pending++;updateControls();try{const result=await call(name,args);if(message)notice(message);await refresh();return result;}catch(error){notice(error.message,'error');throw error;}finally{pending--;updateControls();}}
function motionAllowed(){return connected&&status.armed&&!status.busy&&!status.estop&&!status.plan?.running&&!pending;}
function updateControls(){document.querySelectorAll('[data-move],.motion-control,.joint-slider').forEach(button=>button.disabled=!motionAllowed());$('arm').disabled=!connected||pending>0;$('arm').textContent=status.armed?'Disarm motion':'Arm motion';$('arm').classList.toggle('armed',Boolean(status.armed));$('arm').title=status.estop?'Reset stop and arm motion':status.armed?'Disarm motion':'Arm motion';$('motion-tag').textContent=status.estop?'STOPPED':status.busy?'MOVING':status.armed?'ARMED':'DISARMED';$('motion-tag').className=`tag ${status.estop?'red':status.armed?'teal':''}`;controllerToggle?.update(status.gamepad,connected);}
function render(state){
  if(state.server_instance_id&&lastServerInstance&&state.server_instance_id!==lastServerInstance)initialized=false;
  if(state.server_instance_id)lastServerInstance=state.server_instance_id;
  status=state;const angles=state.servo_angles||[];
  const power=state.power||{powered:true,voltage:state.battery_voltage,valid:Number.isFinite(state.battery_voltage)},powerValid=power.valid!==false&&Number.isFinite(power.voltage);
  $('battery').textContent=powerValid?`Battery ${power.voltage.toFixed(2)} V`:'Battery unavailable';
  $('power-voltage').textContent=powerValid?power.voltage.toFixed(2):'—';
  $('power-detail').textContent=powerValid?`Measured supply · ${power.source||'Robot HAT ADC'}`:power.error||'Supply voltage unavailable';
  $('power-state').textContent=power.powered===true?'POWERED':'POWER STATE UNKNOWN';$('power-state').className=`tag ${power.powered===true?'teal':''}`;
  const piPower=power.pi||{},servoBudget=state.servo_budget||{};
  if(piPower.under_voltage===true){$('power-state').textContent='LOW VOLTAGE';$('power-state').className='tag red';$('power-detail').textContent='Pi undervoltage detected';}
  else if(piPower.under_voltage_occurred===true){$('power-state').textContent='PAST LOW VOLTAGE';$('power-state').className='tag orange';}
  const budgetDetails=[];
  if(Number.isFinite(piPower.cpu?.max_mhz))budgetDetails.push(`CPU cap ${(piPower.cpu.max_mhz/1000).toFixed(1)} GHz`);
  if(Number.isFinite(servoBudget.max_active_servos))budgetDetails.push(`Servo limit ${servoBudget.max_active_servos} at a time`);
  if(budgetDetails.length)$('power-detail').textContent+=' · '+budgetDetails.join(' · ');
  $('power-charging').textContent=power.charging===true?'Charging':power.charging===false?'Not charging':'Unknown';
  $('power-external').textContent=power.external_power===true?'Connected':power.external_power===false?'Disconnected':'Unknown';
  $('power-runtime').textContent=Number.isFinite(power.estimated_runtime_minutes)?`About ${Math.round(power.estimated_runtime_minutes)} min`:'Not calibrated';
  $('power-sleep').textContent=Number.isFinite(power.sleep_after_minutes)?`In ${Math.round(power.sleep_after_minutes)} min`:'Not configured';
  $('activity').textContent=state.busy?(state.action||'Moving'):state.estop?'Stopped':state.armed?'Ready':'Disarmed';
  $('hardware-mode').textContent=state.simulate||state.simulated||state.mode==='simulation'?'Simulation · no hardware writes':state.action_error||state.error||state.hardware_error||state.errors?.at(-1)||'Robot HAT · live hardware';
  const distance=state.ultrasonic?.valid?state.ultrasonic.distance_cm:null,hasRange=Number.isFinite(distance);
  $('distance').textContent=hasRange?distance.toFixed(1):'—';
  $('range-detail').textContent=hasRange?'Forward ultrasonic return':state.ultrasonic?.error||'No valid ultrasonic return';
  $('overlay-distance').textContent=hasRange?`${distance.toFixed(1)} CM AHEAD`:'NO RANGE RETURN';
  $('range-return').style.opacity=hasRange?'1':'0';
  $('range-return').style.stroke=distance<20?'#fa7474':'#f0ac72';
  const r=hasRange?Math.max(18,Math.min(90,distance*.55)):50;
  $('range-return').setAttribute('d',`M${90-r*.7} ${76-r*.7}a${r} ${r} 0 0 1 ${r*1.4} 0`);
  $('joint-count').innerHTML=`${angles.filter(Number.isFinite).length} <em>/ 12</em>`;
  $('pose-label').textContent=angles.some(Number.isFinite)?'Commanded pose · body frame':'Reference outline · pose unknown';
  for(let i=0;i<12;i++){const input=$(`joint-${i}`),value=angles[i];$(`joint-value-${i}`).textContent=Number.isFinite(value)?`${value.toFixed(1)}°`:'—';if(Number.isFinite(value)&&document.activeElement!==input)input.value=value;}
  const cam=state.camera||state.media?.camera||{};
  const camOn=Boolean(cam.enabled);
  $('camera-toggle').textContent=camOn?'Disable camera':'Enable camera';
  cameraFeed.setEnabled(camOn&&!$('dashboard').hidden,state.server_instance_id,cam.video_url||'/video.mjpg');
  $('snapshot').href=cam.snapshot_url||'/snapshot.jpg';
  $('feed-label').textContent=cam.model?`${cam.model.toUpperCase()} · PRIMARY`:'PRIMARY CAMERA';
  cameraViews?.update(state);
  if(!camOn){$('camera-feed').hidden=true;$('camera-empty').hidden=false;$('camera-empty').querySelector('strong').textContent=cam.error?'Camera unavailable':'Camera is off';$('camera-empty').querySelector('p').textContent=cam.error||'Enable the camera to see the live feed.';}
  $('snapshot').style.pointerEvents=camOn?'auto':'none';$('snapshot').style.opacity=camOn?'1':'.35';$('snapshot').setAttribute('aria-disabled',String(!camOn));
  const mic=state.microphone||state.media?.microphone||{},micOn=Boolean(mic.enabled);
  $('mic-metric').textContent=micOn?'Listening':'Muted';$('mic-detail').textContent=mic.error|| (micOn?'Microphone capture enabled':'Capture off');$('audio-status').textContent=mic.error?'UNAVAILABLE':micOn?'CAPTURE ON':'MUTED';$('audio-status').className=`tag ${micOn?'orange':''}`;$('mic-toggle').textContent=micOn?'Mute':'Unmute';
  if(micOn&&!previousMic){$('microphone-player').src=`/audio.wav?t=${Date.now()}`;$('microphone-player').hidden=false;}
  if(!micOn&&previousMic){$('microphone-player').pause();$('microphone-player').removeAttribute('src');$('microphone-player').load();$('microphone-player').hidden=true;}previousMic=micOn;
  const run=state.examples||state.example||{};$('test-running').hidden=!run.running;if(run.running)$('test-running').textContent=`Running ${run.example_id||'test'}${run.phase?' · '+run.phase:''}. Use STOP to cancel.`;
  if(run.finished_at&&run.finished_at!==render.lastFinished){render.lastFinished=run.finished_at;log(run.error?`Test failed: ${run.error}`:{test:run.example_id,result:run.result});}
  renderRecordings(state.recordings||[]);model?.update(state);plans?.update(state);updateControls();$('last-updated').textContent=`Telemetry ${new Date().toLocaleTimeString()}`;
}
async function refresh(){if(polling)return;polling=true;try{const state=await call('get_status',{}, {timeout:5000});connected=true;$('connection-dot').className='connected';$('connection-text').textContent='Connected';render(state);}catch(error){connected=false;plans?.update({});initialized=false;$('connection-dot').className='error';$('connection-text').textContent='Disconnected';$('activity').textContent='Disconnected';$('last-updated').textContent='Telemetry unavailable';$('battery').textContent='Battery unavailable';$('power-voltage').textContent='—';$('power-detail').textContent='Disconnected · reading unavailable';$('power-state').textContent='CONNECTION LOST';$('power-state').className='tag orange';$('power-charging').textContent='Unknown';$('power-external').textContent='Unknown';$('power-runtime').textContent='Unavailable';$('power-sleep').textContent='Unavailable';
    $('distance').textContent='—';$('range-detail').textContent='Disconnected · return unavailable';$('overlay-distance').textContent='NO LIVE RANGE RETURN';$('range-return').style.opacity='0';
    model?.update({...status,ultrasonic:{...status.ultrasonic,valid:false},camera:{enabled:false},microphone:{enabled:false},speaker:{busy:false}});
    cameraFeed.setEnabled(false);cameraViews?.disconnect();$('camera-feed').hidden=true;$('camera-empty').hidden=false;$('camera-empty').querySelector('strong').textContent='Camera disconnected';$('camera-empty').querySelector('p').textContent='Waiting for the robot connection to return.';$('camera-status').textContent='DISCONNECTED';$('camera-dot').classList.remove('active');
    $('microphone-player').pause();$('mic-metric').textContent='Unavailable';$('mic-detail').textContent='Robot disconnected';updateControls();}finally{polling=false;}}
function renderRecordings(recordings){const signature=recordings.map(r=>r.name).join('|');if(signature===renderRecordings.signature)return;renderRecordings.signature=signature;$('recordings-list').replaceChildren();$('recordings-panel').hidden=!recordings.length;for(const recording of recordings){const a=document.createElement('a');a.className='recording-link';a.textContent=`${recording.name} ↓`;a.href=recording.url;a.download=recording.name;$('recordings-list').append(a);}}
function initJoints(){for(let leg=0;leg<4;leg++){const section=document.createElement('div');section.className='joint-leg';const h=document.createElement('h3');h.innerHTML=`<span>0${leg+1}</span>${names[leg]}`;section.append(h);for(let joint=0;joint<3;joint++){const index=leg*3+joint,row=document.createElement('div');row.className='joint-row';row.innerHTML=`<label for="joint-${index}">${jointNames[joint]}<output id="joint-value-${index}">—</output></label><input id="joint-${index}" class="joint-slider" type="range" min="${limits[joint][0]}" max="${limits[joint][1]}" value="0" step="1" disabled aria-label="${names[leg]} ${jointNames[joint]} angle">`;const input=row.querySelector('input');input.addEventListener('input',()=>{$(`joint-value-${index}`).textContent=`${input.value}°`;});input.addEventListener('change',()=>{if(motionAllowed())command('set_servo',{index,angle:Number(input.value),speed:20}).catch(()=>{});});section.append(row);}$('joint-grid').append(section);}$('leg').replaceChildren(...names.map((name,index)=>{const option=document.createElement('option');option.value=index;option.textContent=`${index+1} · ${name}`;return option;}));}
function makeOption(name,schema){const label=document.createElement('label');label.textContent=schema.title||name.replaceAll('_',' ');let input;if(schema.enum){input=document.createElement('select');for(const value of schema.enum){const option=document.createElement('option');option.value=value;option.textContent=String(value);input.append(option);}}else if(schema.type==='array'||schema.type==='object'){input=document.createElement('textarea');input.rows=3;input.placeholder=schema.type==='array'?'[ … ]':'{ … }';}else{input=document.createElement('input');input.type=schema.type==='boolean'?'checkbox':schema.type==='number'||schema.type==='integer'?'number':'text';if(schema.minimum!==undefined)input.min=schema.minimum;if(schema.maximum!==undefined)input.max=schema.maximum;if(input.type==='number')input.step=schema.type==='integer'?'1':'any';}
  input.dataset.parameter=name;input.dataset.type=schema.type||'string';if(schema.default!==undefined){if(input.type==='checkbox')input.checked=schema.default;else input.value=typeof schema.default==='object'?JSON.stringify(schema.default):schema.default;}if(schema.description)input.title=schema.description;label.append(input);return label;
}
async function loadExamples(){try{const result=await call('list_examples');examples=Array.isArray(result)?result:result.examples||[];$('example-count').textContent=examples.length;$('test-summary').textContent=`${examples.length} EXAMPLES`;$('examples').replaceChildren();for(const example of examples){const card=document.createElement('article');card.className='panel example';const top=document.createElement('div');top.className='example-top';const number=document.createElement('span');number.className='example-number';number.textContent=String(example.id).match(/^\d+/)?.[0].padStart(2,'0')||'＋';const badge=document.createElement('span');badge.className='tag';badge.textContent=example.requires?.includes('motion')?'MOTION':example.requires?.includes('motion_optional')?'OPTIONAL MOTION':(example.requires||[]).join(' · ')||'UTILITY';top.append(number,badge);const title=document.createElement('h3');title.textContent=example.title;const description=document.createElement('p');description.textContent=example.description;const source=document.createElement('div');source.className='example-source';source.textContent=example.source||`${example.id}.py`;card.append(top,title,description,source);const options=document.createElement('div');options.className='example-options';for(const [name,schema] of Object.entries(example.parameters?.properties||{}))options.append(makeOption(name,schema));card.append(options);const available=example.available!==false;if(!available){const reason=document.createElement('p');reason.className='disabled-reason';reason.textContent=example.reason||'This example needs additional configuration.';card.append(reason);}const run=document.createElement('button');run.textContent=available?'Run experiment ↗':'Unavailable';run.disabled=!available;run.addEventListener('click',async()=>{try{const args={};for(const input of options.querySelectorAll('[data-parameter]')){const type=input.dataset.type;if(type==='boolean')args[input.dataset.parameter]=input.checked;else if(input.value!=='')args[input.dataset.parameter]=type==='number'||type==='integer'?Number(input.value):type==='object'||type==='array'?JSON.parse(input.value):input.value;}if((example.requires?.includes('motion')||(example.requires?.includes('motion_optional')&&['pose','play'].includes(args.operation)))&&!status.armed){notice('Arm motion before running this experiment.','warning');return;}run.disabled=true;const result=await command('run_example',{example_id:example.id,options:args},`Started ${example.title}.`);log({test:example.id,result});}catch(error){log(error.message);}finally{run.disabled=!available;}});card.append(run);$('examples').append(card);}}catch(error){$('test-summary').textContent='CATALOG UNAVAILABLE';log(error.message);}}

initJoints();
let admin = null, plans = null, hardwareAdmin = null, cameraViews = null;
function selectTab(name, updateHash = true) {
  const [mainName,sectionName]=name.split("/");name=mainName;
  if (!['dashboard','plan','testing','admin','apps'].includes(name)) name = 'dashboard';
  document.querySelectorAll('[data-tab]').forEach(tab=>{const active=tab.dataset.tab===name;tab.classList.toggle('active',active);if(active)tab.setAttribute('aria-current','page');else tab.removeAttribute('aria-current');});
  document.querySelectorAll('.tab-panel').forEach(panel=>panel.hidden=panel.id!==name);
  document.querySelectorAll('.power-panel,.metrics').forEach(panel=>panel.hidden=['admin','apps'].includes(name));
  document.body.classList.toggle('admin-active',name==='admin');
  const titles={plan:['Motion plans','Save poses, arrange steps, and review routines before running.'],dashboard:['Robot dashboard','Live cameras, sensors, and motion controls.'],testing:['Testing lab','Explore the crawler’s capabilities with hardware experiments.'],admin:['Administration','Configure the robot and its connected devices and services.'],apps:['Apps','Take the robot controls with you on Android and Windows.']};
  $('page-title').textContent=titles[name][0];$('page-description').textContent=titles[name][1];
  if(name==='testing')loadExamples();
  if(name==='admin'){admin?.selectSection(sectionName||admin?.section||'hardware',false);admin?.refresh();}
  hardwareAdmin?.setActive(name==='admin'?admin?.section:null);
  if(name==='plan')plans?.refresh();
  if(updateHash)history.replaceState(null,'',name==='dashboard'?location.pathname:name==='admin'?'#admin/'+(admin?.section||'hardware'):'#'+name);
  cameraViews?.setActive(name==='admin'&&admin?.section==='cameras');
  if(connected)render(status);
}
document.querySelectorAll('[data-tab]').forEach(tab=>tab.addEventListener('click',()=>selectTab(tab.dataset.tab)));
window.addEventListener('hashchange',()=>selectTab(location.hash.slice(1),false));

$('arm').addEventListener('click',()=>command('arm',{armed:!status.armed},status.armed?'Motion disarmed.':'Motion armed. Place the crawler on a clear, stable surface.').catch(()=>{}));
const stop=()=>command('stop',{},'Stop requested. Motion is disarmed.').catch(()=>{});$('stop').addEventListener('click',stop);$('drive-stop').addEventListener('click',stop);
document.querySelectorAll('[data-move]').forEach(button=>button.addEventListener('click',()=>command('move',{name:button.dataset.move,speed:Number($('speed').value),steps:1}).catch(()=>{})));
$('speed').addEventListener('input',()=>{$('speed-value').textContent=`${$('speed').value}%`;});
$('leg-form').addEventListener('submit',event=>{event.preventDefault();command('set_leg',{leg:Number($('leg').value),x:Number($('leg-x').value),y:Number($('leg-y').value),z:Number($('leg-z').value),speed:Number($('speed').value)}).catch(()=>{});});
$('camera-toggle').addEventListener('click',()=>command('camera',{enabled:!status.camera?.enabled,...(status.primary_camera_id?{camera_id:status.primary_camera_id}:{})}).catch(()=>{}));
$('camera-feed').addEventListener('error',()=>{if(cameraFeed.enabled)cameraFeed.controller?.abort();});
$('camera-feed').addEventListener('load',()=>{if(cameraFeed.enabled){$('camera-feed').hidden=false;$('camera-empty').hidden=true;}});
$('mic-toggle').addEventListener('click',async()=>{try{const enabling=!status.microphone?.enabled;await command('microphone',{enabled:enabling});if(enabling)$('microphone-player').play().catch(()=>notice('Microphone is enabled. Press play in the audio control to listen.'));}catch{}});
$('speak-form').addEventListener('submit',event=>{event.preventDefault();const text=$('speech').value.trim();if(text)command('speak',{text},'Speech sent to the robot speaker.').catch(()=>{});});
$('clear-log').addEventListener('click',()=>{$('test-output').textContent='Select an experiment to begin.';});
$('reset-view').addEventListener('click',()=>model?.reset());
window.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();stop();}});
window.addEventListener('offline',()=>{connected=false;cameraFeed.setEnabled(false);cameraViews?.disconnect();plans?.update({});updateControls();});
window.addEventListener('online',()=>refresh());
function stopOnLeave(){ $('microphone-player').pause();if(!initialized||!(status.armed||status.microphone?.enabled||status.examples?.running||status.plan?.running))return;fetch('/mcp',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-03-26'},body:JSON.stringify({jsonrpc:'2.0',id:++rpcId,method:'tools/call',params:{name:'stop',arguments:{}}})}).catch(()=>{});notice('Motion stopped and audio muted when this page left the foreground.');}
window.addEventListener('pagehide',stopOnLeave);document.addEventListener('visibilitychange',()=>{if(document.hidden)stopOnLeave();else refresh();});
try{const {RobotView}=await import('./robot-view.js');model=new RobotView($('robot-view'));}catch(error){$('model-error').textContent=`3D rendering unavailable: ${error.message}`;$('model-error').hidden=false;}
try{const {initAdmin}=await import('./admin.js');admin=initAdmin({call,notice,onConfigChanged:loadExamples,onSectionChange:name=>{hardwareAdmin?.setActive(!$('admin').hidden?name:null);cameraViews?.setActive(name==='cameras'&&!$('admin').hidden);}});}catch(error){$('admin').textContent=`Admin unavailable: ${error.message}`;}
try{const {initPlans}=await import('./plans.js');plans=initPlans({call,notice,getStatus:()=>connected?status:{}});}catch(error){$('plan').textContent=`Plans unavailable: ${error.message}`;}
try{const {initHardware}=await import('./hardware-admin.js');hardwareAdmin=initHardware({call});}catch(error){console.error('Hardware inventory unavailable',error);}
try{const {initCameraViews}=await import('./cameras-ui.js');cameraViews=initCameraViews({call,notice,CameraFeed,getStatus:()=>connected?status:{}});}catch(error){console.error('Camera settings unavailable',error);}
try{const {initControllerToggle}=await import('./controller-toggle.js');controllerToggle=initControllerToggle({call,notice,onChanged:gamepad=>{status.gamepad=gamepad;if(typeof gamepad.system_armed==='boolean'){status.armed=gamepad.system_armed;if(status.armed)status.estop=false;}updateControls();hardwareAdmin?.refreshControllers();}});}catch(error){console.error('Controller toggle unavailable',error);}
selectTab(location.hash.slice(1),false);
setInterval(refresh,500);refresh();loadExamples();setInterval(()=>{$('clock').textContent=new Date().toLocaleTimeString('en-GB');},1000);
