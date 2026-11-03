const $=id=>document.getElementById(id);
let status={},connected=false,rpcId=0,initialized=false,polling=false,model=null,cameraSource=false,previousMic=false,examples=[];
const names=['Right front','Left front','Left rear','Right rear'];
const jointNames=['Knee','Shoulder','Hip'];
const limits=[[-90,90],[-10,90],[-60,60]];
let pending=0;

async function rpc(method,params={}){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch('/mcp',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-03-26'},body:JSON.stringify({jsonrpc:'2.0',id:++rpcId,method,params}),signal:controller.signal});
    if(!response.ok)throw new Error(`Server returned ${response.status}: ${(await response.text()).slice(0,180)}`);
    const text=await response.text();let result;
    if(response.headers.get('content-type')?.includes('text/event-stream')){const data=text.split('\n').filter(line=>line.startsWith('data: ')).map(line=>line.slice(6));result=JSON.parse(data.at(-1));}else result=JSON.parse(text);
    if(result.error)throw new Error(result.error.message);
    return result.result;
  }finally{clearTimeout(timeout);}
}
async function call(name,args={}){
  if(!initialized){await rpc('initialize',{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'PiBotPal Web',version:'0.1.0'}});initialized=true;}
  const result=await rpc('tools/call',{name,arguments:args});
  const raw=result?.content?.filter(c=>c.type==='text').map(c=>c.text).join('\n')||'';
  if(result?.isError)throw new Error(raw||'The robot could not complete this command.');
  if(result?.structuredContent){const data=result.structuredContent;return Object.keys(data).length===1&&'result' in data?data.result:data;}
  try{return JSON.parse(raw);}catch{return raw;}
}
function notice(message,type=''){const n=$('notice');n.textContent=message;n.className=`notice ${type}`;n.hidden=!message;}
function log(message){const output=$('test-output');if(output.textContent==='Select an experiment to begin.')output.textContent='';output.textContent=`[${new Date().toLocaleTimeString()}] ${typeof message==='string'?message:JSON.stringify(message,null,2)}\n\n${output.textContent}`.slice(0,30000);}
async function command(name,args={},message){pending++;updateControls();try{const result=await call(name,args);if(message)notice(message);await refresh();return result;}catch(error){notice(error.message,'error');throw error;}finally{pending--;updateControls();}}
function motionAllowed(){return connected&&status.armed&&!status.busy&&!status.estop&&!pending;}
function updateControls(){document.querySelectorAll('[data-move],.motion-control,.joint-slider').forEach(button=>button.disabled=!motionAllowed());$('arm').disabled=!connected||pending>0;$('arm').textContent=status.armed?'Disarm motion':'Arm motion';$('arm').classList.toggle('armed',Boolean(status.armed));$('arm-label').textContent=status.estop?'Stop latched · arm to reset':status.armed?'Motion armed':'Motion disarmed';$('motion-tag').textContent=status.estop?'STOPPED':status.busy?'MOVING':status.armed?'ARMED':'DISARMED';$('motion-tag').className=`tag ${status.estop?'red':status.armed?'teal':''}`;}
function render(state){
  status=state;const angles=state.servo_angles||[];$('battery').textContent=Number.isFinite(state.battery_voltage)?`Battery ${state.battery_voltage.toFixed(2)} V`:'Battery unavailable';
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
  $('camera-toggle').textContent=camOn?'Disable camera':'Enable camera';$('camera-status').textContent=cam.error?'ERROR':camOn?'LIVE':'OFF';$('camera-dot').classList.toggle('active',camOn&&!cam.error);
  if(camOn&&!cameraSource){$('camera-feed').src=`/video.mjpg?t=${Date.now()}`;cameraSource=true;$('camera-feed').hidden=false;$('camera-empty').hidden=true;}
  if(!camOn&&cameraSource){$('camera-feed').removeAttribute('src');cameraSource=false;}
  if(!camOn){$('camera-feed').hidden=true;$('camera-empty').hidden=false;$('camera-empty').querySelector('strong').textContent=cam.error?'Camera unavailable':'Camera is off';$('camera-empty').querySelector('p').textContent=cam.error||'Enable the camera to see the live feed.';}
  $('snapshot').style.pointerEvents=camOn?'auto':'none';$('snapshot').style.opacity=camOn?'1':'.35';$('snapshot').setAttribute('aria-disabled',String(!camOn));
  const mic=state.microphone||state.media?.microphone||{},micOn=Boolean(mic.enabled);
  $('mic-metric').textContent=micOn?'Listening':'Muted';$('mic-detail').textContent=mic.error|| (micOn?'Microphone capture enabled':'Capture off');$('audio-status').textContent=mic.error?'UNAVAILABLE':micOn?'CAPTURE ON':'MUTED';$('audio-status').className=`tag ${micOn?'orange':''}`;$('mic-toggle').textContent=micOn?'Mute':'Unmute';
  if(micOn&&!previousMic){$('microphone-player').src=`/audio.wav?t=${Date.now()}`;$('microphone-player').hidden=false;}
  if(!micOn&&previousMic){$('microphone-player').pause();$('microphone-player').removeAttribute('src');$('microphone-player').load();$('microphone-player').hidden=true;}previousMic=micOn;
  const run=state.examples||state.example||{};$('test-running').hidden=!run.running;if(run.running)$('test-running').textContent=`Running ${run.example_id||'test'}${run.phase?' · '+run.phase:''}. Use STOP to cancel.`;
  if(run.finished_at&&run.finished_at!==render.lastFinished){render.lastFinished=run.finished_at;log(run.error?`Test failed: ${run.error}`:{test:run.example_id,result:run.result});}
  renderRecordings(state.recordings||[]);model?.update(state);updateControls();$('last-updated').textContent=`Telemetry ${new Date().toLocaleTimeString()}`;
}
async function refresh(){if(polling)return;polling=true;try{const state=await call('get_status');connected=true;$('connection-dot').className='connected';$('connection-text').textContent='Connected';render(state);}catch(error){connected=false;initialized=false;$('connection-dot').className='error';$('connection-text').textContent='Disconnected';$('activity').textContent='Disconnected';$('last-updated').textContent='Telemetry unavailable';$('battery').textContent='Battery unavailable';
    $('distance').textContent='—';$('range-detail').textContent='Disconnected · return unavailable';$('overlay-distance').textContent='NO LIVE RANGE RETURN';$('range-return').style.opacity='0';
    model?.update({...status,ultrasonic:{...status.ultrasonic,valid:false},camera:{enabled:false},microphone:{enabled:false},speaker:{busy:false}});
    $('camera-feed').removeAttribute('src');$('camera-feed').hidden=true;cameraSource=false;$('camera-empty').hidden=false;$('camera-empty').querySelector('strong').textContent='Camera disconnected';$('camera-empty').querySelector('p').textContent='Waiting for the robot connection to return.';$('camera-status').textContent='DISCONNECTED';$('camera-dot').classList.remove('active');
    $('microphone-player').pause();$('mic-metric').textContent='Unavailable';$('mic-detail').textContent='Robot disconnected';updateControls();}finally{polling=false;}}
function renderRecordings(recordings){const signature=recordings.map(r=>r.name).join('|');if(signature===renderRecordings.signature)return;renderRecordings.signature=signature;$('recordings-list').replaceChildren();$('recordings-panel').hidden=!recordings.length;for(const recording of recordings){const a=document.createElement('a');a.className='recording-link';a.textContent=`${recording.name} ↓`;a.href=recording.url;a.download=recording.name;$('recordings-list').append(a);}}
function initJoints(){for(let leg=0;leg<4;leg++){const section=document.createElement('div');section.className='joint-leg';const h=document.createElement('h3');h.innerHTML=`<span>0${leg+1}</span>${names[leg]}`;section.append(h);for(let joint=0;joint<3;joint++){const index=leg*3+joint,row=document.createElement('div');row.className='joint-row';row.innerHTML=`<label for="joint-${index}">${jointNames[joint]}<output id="joint-value-${index}">—</output></label><input id="joint-${index}" class="joint-slider" type="range" min="${limits[joint][0]}" max="${limits[joint][1]}" value="0" step="1" disabled aria-label="${names[leg]} ${jointNames[joint]} angle">`;const input=row.querySelector('input');input.addEventListener('input',()=>{$(`joint-value-${index}`).textContent=`${input.value}°`;});input.addEventListener('change',()=>{if(motionAllowed())command('set_servo',{index,angle:Number(input.value),speed:20}).catch(()=>{});});section.append(row);}$('joint-grid').append(section);}$('leg').replaceChildren(...names.map((name,index)=>{const option=document.createElement('option');option.value=index;option.textContent=`${index+1} · ${name}`;return option;}));}
function makeOption(name,schema){const label=document.createElement('label');label.textContent=schema.title||name.replaceAll('_',' ');let input;if(schema.enum){input=document.createElement('select');for(const value of schema.enum){const option=document.createElement('option');option.value=value;option.textContent=String(value);input.append(option);}}else if(schema.type==='array'||schema.type==='object'){input=document.createElement('textarea');input.rows=3;input.placeholder=schema.type==='array'?'[ … ]':'{ … }';}else{input=document.createElement('input');input.type=schema.type==='boolean'?'checkbox':schema.type==='number'||schema.type==='integer'?'number':'text';if(schema.minimum!==undefined)input.min=schema.minimum;if(schema.maximum!==undefined)input.max=schema.maximum;if(input.type==='number')input.step=schema.type==='integer'?'1':'any';}
  input.dataset.parameter=name;input.dataset.type=schema.type||'string';if(schema.default!==undefined){if(input.type==='checkbox')input.checked=schema.default;else input.value=typeof schema.default==='object'?JSON.stringify(schema.default):schema.default;}if(schema.description)input.title=schema.description;label.append(input);return label;
}
async function loadExamples(){try{const result=await call('list_examples');examples=Array.isArray(result)?result:result.examples||[];$('example-count').textContent=examples.length;$('test-summary').textContent=`${examples.length} EXAMPLES`;$('examples').replaceChildren();for(const example of examples){const card=document.createElement('article');card.className='panel example';const top=document.createElement('div');top.className='example-top';const number=document.createElement('span');number.className='example-number';number.textContent=String(example.id).match(/^\d+/)?.[0].padStart(2,'0')||'＋';const badge=document.createElement('span');badge.className='tag';badge.textContent=example.requires?.includes('motion')?'MOTION':example.requires?.includes('motion_optional')?'OPTIONAL MOTION':(example.requires||[]).join(' · ')||'UTILITY';top.append(number,badge);const title=document.createElement('h3');title.textContent=example.title;const description=document.createElement('p');description.textContent=example.description;const source=document.createElement('div');source.className='example-source';source.textContent=example.source||`${example.id}.py`;card.append(top,title,description,source);const options=document.createElement('div');options.className='example-options';for(const [name,schema] of Object.entries(example.parameters?.properties||{}))options.append(makeOption(name,schema));card.append(options);const available=example.available!==false;if(!available){const reason=document.createElement('p');reason.className='disabled-reason';reason.textContent=example.reason||'This example needs additional configuration.';card.append(reason);}const run=document.createElement('button');run.textContent=available?'Run experiment ↗':'Unavailable';run.disabled=!available;run.addEventListener('click',async()=>{try{const args={};for(const input of options.querySelectorAll('[data-parameter]')){const type=input.dataset.type;if(type==='boolean')args[input.dataset.parameter]=input.checked;else if(input.value!=='')args[input.dataset.parameter]=type==='number'||type==='integer'?Number(input.value):type==='object'||type==='array'?JSON.parse(input.value):input.value;}if((example.requires?.includes('motion')||(example.requires?.includes('motion_optional')&&['pose','play'].includes(args.operation)))&&!status.armed){notice('Arm motion before running this experiment.','warning');return;}run.disabled=true;const result=await command('run_example',{example_id:example.id,options:args},`Started ${example.title}.`);log({test:example.id,result});}catch(error){log(error.message);}finally{run.disabled=!available;}});card.append(run);$('examples').append(card);}}catch(error){$('test-summary').textContent='CATALOG UNAVAILABLE';log(error.message);}}

initJoints();
document.querySelectorAll('[data-tab]').forEach(tab=>tab.addEventListener('click',()=>{document.querySelectorAll('[data-tab]').forEach(t=>{t.classList.toggle('active',t===tab);t.toggleAttribute('aria-current',t===tab);});document.querySelectorAll('.tab-panel').forEach(panel=>panel.hidden=panel.id!==tab.dataset.tab);const dashboard=tab.dataset.tab==='dashboard';$('page-title').textContent=dashboard?'A little closer to the ground.':'Meet your crawler’s capabilities.';$('page-description').textContent=dashboard?'See what your crawler sees. Keep every leg in view.':'Explore the examples through the same MCP control layer.';if(!dashboard&&!examples.length)loadExamples();}));
$('arm').addEventListener('click',()=>command('arm',{armed:!status.armed},status.armed?'Motion disarmed.':'Motion armed. Place the crawler on a clear, stable surface.').catch(()=>{}));
const stop=()=>command('stop',{},'Stop requested. Motion is disarmed.').catch(()=>{});$('stop').addEventListener('click',stop);$('drive-stop').addEventListener('click',stop);
document.querySelectorAll('[data-move]').forEach(button=>button.addEventListener('click',()=>command('move',{name:button.dataset.move,speed:Number($('speed').value),steps:1}).catch(()=>{})));
$('speed').addEventListener('input',()=>{$('speed-value').textContent=`${$('speed').value}%`;});
$('leg-form').addEventListener('submit',event=>{event.preventDefault();command('set_leg',{leg:Number($('leg').value),x:Number($('leg-x').value),y:Number($('leg-y').value),z:Number($('leg-z').value),speed:Number($('speed').value)}).catch(()=>{});});
$('camera-toggle').addEventListener('click',()=>command('camera',{enabled:!status.camera?.enabled}).catch(()=>{}));
$('camera-feed').addEventListener('error',()=>{$('camera-feed').hidden=true;$('camera-empty').hidden=false;$('camera-empty').querySelector('strong').textContent='Camera feed unavailable';$('camera-empty').querySelector('p').textContent=status.camera?.error||'Toggle the camera to reconnect.';$('camera-status').textContent='NO FEED';});
$('camera-feed').addEventListener('load',()=>{$('camera-feed').hidden=false;$('camera-empty').hidden=true;});
$('mic-toggle').addEventListener('click',async()=>{try{const enabling=!status.microphone?.enabled;await command('microphone',{enabled:enabling});if(enabling)$('microphone-player').play().catch(()=>notice('Microphone is enabled. Press play in the audio control to listen.'));}catch{}});
$('speak-form').addEventListener('submit',event=>{event.preventDefault();const text=$('speech').value.trim();if(text)command('speak',{text},'Speech sent to the robot speaker.').catch(()=>{});});
$('clear-log').addEventListener('click',()=>{$('test-output').textContent='Select an experiment to begin.';});
$('reset-view').addEventListener('click',()=>model?.reset());
window.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();stop();}});
window.addEventListener('offline',()=>{connected=false;updateControls();});
function stopOnLeave(){ $('microphone-player').pause();if(!initialized||!(status.armed||status.microphone?.enabled||status.examples?.running))return;fetch('/mcp',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-03-26'},body:JSON.stringify({jsonrpc:'2.0',id:++rpcId,method:'tools/call',params:{name:'stop',arguments:{}}})}).catch(()=>{});notice('Motion stopped and audio muted when this page left the foreground.');}
window.addEventListener('pagehide',stopOnLeave);document.addEventListener('visibilitychange',()=>{if(document.hidden)stopOnLeave();else refresh();});
try{const {RobotView}=await import('./robot-view.js');model=new RobotView($('robot-view'));}catch(error){$('model-error').textContent=`3D rendering unavailable: ${error.message}`;$('model-error').hidden=false;}
await refresh();loadExamples();setInterval(refresh,500);setInterval(()=>{$('clock').textContent=new Date().toLocaleTimeString('en-GB');},1000);
