// SPDX-License-Identifier: MIT
const $ = id => document.getElementById(id);
const ACTIONS = ['stand','sit','ready','forward','backward','turn left','turn right','turn left angle','turn right angle','push up','wave','look left','look right','look up','look down','dance'];
const copy = value => JSON.parse(JSON.stringify(value));
const markup = `
  <div class="plan-intro"><div><p class="eyebrow">SAVE A POSE · BUILD A ROUTINE</p><h2>A few steps, in the right order.</h2><p>Draft instructions, review every step, then save and run deliberately.</p></div><button id="plan-refresh" class="outline">Refresh library</button></div>
  <div id="plan-notice" class="notice" role="status" aria-live="polite" hidden></div>
  <article class="panel plan-execution"><div class="panel-heading"><h2>Execution</h2><span id="plan-state" class="tag">IDLE</span></div><div class="panel-body"><div class="plan-progress-row"><div><strong id="plan-progress">No plan running</strong><p id="plan-progress-detail" class="caption">Arm motion explicitly before starting a saved plan. STOP cancels the whole routine.</p></div><button id="plan-stop" class="danger">■ Stop plan</button></div></div></article>
  <div class="plan-layout">
    <div class="plan-editor-column">
      <article class="panel"><div class="panel-heading"><h2>Describe a plan</h2><span class="tag">DRAFT ONLY</span></div><form id="plan-draft-form" class="admin-form"><label>Instructions<textarea id="plan-instructions" rows="3" maxlength="4000" required placeholder="Stand. Move forward 4 steps, turn left, move forward four steps."></textarea></label><label class="check-label"><input id="plan-use-llm" type="checkbox">Use configured assistant LLM to draft this plan</label><p class="caption">Simple instructions use the built-in parser. An available configured model can help with other phrasing. Drafting never moves the robot. When the LLM option is selected, these instructions and up to 32 saved pose names/IDs are sent to the assistant provider configured in Admin; camera and audio are excluded.</p><div class="admin-actions"><button id="plan-draft" class="primary" type="submit">Create editable draft</button></div></form></article>
      <article class="panel"><div class="panel-heading"><h2>Plan editor</h2><button id="plan-new" class="subtle">+ New plan</button></div><form id="plan-editor" class="admin-form"><label>Plan name<input id="plan-name" maxlength="80" required placeholder="A short walk"></label><div id="plan-steps" class="plan-steps"></div><div class="admin-actions"><button id="plan-add-action" type="button">+ Movement</button><button id="plan-add-pose" type="button">+ Saved pose</button><button id="plan-add-wait" type="button">+ Wait</button></div><p id="plan-editor-detail" class="caption">Add a step or describe your routine above. Plans run in order, with no automatic restart.</p><div class="admin-actions"><button class="primary" type="submit">Save plan</button><button id="plan-run" type="button" disabled>Run saved plan ↗</button><button id="plan-delete" type="button" class="delete-button" hidden>Delete plan</button></div></form></article>
    </div>
    <div class="plan-library-column">
      <article class="panel"><div class="panel-heading"><h2>Saved poses</h2><span class="tag">12 SERVO COMMANDS</span></div><form id="pose-form" class="admin-form"><label>Pose name<input id="pose-name" required maxlength="80" placeholder="My standing pose"></label><button id="pose-capture" type="submit">Save current servo positions</button><p class="caption">Captures all twelve commanded joint angles after a known pose has been sent. These servos have no position feedback; hand-positioning a leg cannot be recorded.</p></form><div id="pose-library" class="plan-library"></div></article>
      <article class="panel"><div class="panel-heading"><h2>Saved plans</h2><span id="plan-library-count" class="tag">0 PLANS</span></div><div id="plan-library" class="plan-library"></div></article>
      <article class="panel"><div class="panel-heading"><h2>Growing into decisions</h2><span class="tag">NEXT STEPS</span></div><div class="panel-body"><p class="caption">This first planner runs reviewed movement, saved-pose, and wait steps. Sensor conditions and branching will be added as explicit, bounded steps. Lidar, Coral, and a rear camera can be recorded in Admin’s hardware inventory.</p></div></article>
    </div>
  </div>`;

export function initPlans({call, notice, getStatus}) {
  $('plan').innerHTML = markup;
  let library = {poses:[], plans:[]}, steps = [], selectedId = null, dirty = false, loading = false, execution = {};
  function message(text, kind='') { $('plan-notice').textContent=text; $('plan-notice').className=`notice ${kind}`; $('plan-notice').hidden=!text; }
  async function attempt(fn) { try { return await fn(); } catch(error) { message(error.message,'error'); } }
  function changed() { dirty=true; controls(); }
  function controls() {
    const robot=getStatus()||{}, active=Boolean(execution.running || execution.status==='running');
    $('plan-run').disabled=!selectedId || dirty || active || !robot.armed || robot.busy || robot.estop;
    $('plan-run').title=dirty?'Save changes before running':!robot.armed?'Arm motion before running':'';
    $('plan-delete').hidden=!selectedId;
    $('pose-capture').disabled=!Array.isArray(robot.servo_angles)||robot.servo_angles.length!==12||robot.servo_angles.some(v=>!Number.isFinite(v))||robot.busy||active;
    $('plan-add-pose').disabled=!library.poses.length;
  }
  function inputField(labelText, value, config, onChange) {
    const label=document.createElement('label');label.textContent=labelText;
    const input=document.createElement('input'); Object.assign(input,config);input.value=value;
    input.addEventListener('change',()=>{onChange(input.type==='number'?Number(input.value):input.value);changed();});label.append(input);return label;
  }
  function selectField(labelText, value, choices, onChange) {
    const label=document.createElement('label');label.textContent=labelText;
    const select=document.createElement('select');
    for(const [key,title] of choices){const option=document.createElement('option');option.value=key;option.textContent=title;select.append(option);}
    select.value=value;select.addEventListener('change',()=>{onChange(select.value);changed();});label.append(select);return label;
  }
  function renderSteps() {
    $('plan-steps').replaceChildren();
    if(!steps.length){const empty=document.createElement('p');empty.className='caption';empty.textContent='No steps yet.';$('plan-steps').append(empty);}
    steps.forEach((step,index)=>{
      const row=document.createElement('div');row.className='plan-step';
      const top=document.createElement('div');top.className='plan-step-top';const title=document.createElement('strong');title.textContent=`${index+1} · ${step.type==='action'?'Movement':step.type==='pose'?'Saved pose':'Wait'}`;top.append(title);
      const actions=document.createElement('div');actions.className='plan-step-actions';
      for(const [text,label,disabled,fn] of [ ['↑','Move step up',index===0,()=>{[steps[index-1],steps[index]]=[steps[index],steps[index-1]];}], ['↓','Move step down',index===steps.length-1,()=>{[steps[index+1],steps[index]]=[steps[index],steps[index+1]];}], ['×','Remove step',false,()=>steps.splice(index,1)] ]){
        const b=document.createElement('button');b.type='button';b.textContent=text;b.title=label;b.setAttribute('aria-label',`${label} ${index+1}`);b.disabled=disabled;b.addEventListener('click',()=>{fn();changed();renderSteps();});actions.append(b);
      }
      top.append(actions);row.append(top);const fields=document.createElement('div');fields.className='plan-step-fields';
      if(step.type==='action'){
        fields.append(selectField('Action',step.name,ACTIONS.map(a=>[a,a]),v=>step.name=v));
        fields.append(inputField('Cycles',step.steps||1,{type:'number',min:1,max:5,step:1,required:true},v=>step.steps=v));
      }else if(step.type==='pose'){
        const choices=library.poses.map(p=>[p.id,p.name]);
        if(!choices.some(([id])=>id===step.pose_id))choices.unshift([step.pose_id||'',step.pose_id?'Saved pose snapshot':'Choose a saved pose']);
        fields.append(selectField('Pose',step.pose_id,choices,v=>{step.pose_id=v;delete step.angles;delete step.pose_name;}));
      }else fields.append(inputField('Seconds',step.seconds??1,{type:'number',min:0.1,max:30,step:0.1,required:true},v=>step.seconds=v));
      if(step.type!=='wait')fields.append(inputField('Speed (%)',step.speed||35,{type:'number',min:1,max:80,step:1,required:true},v=>step.speed=v));
      row.append(fields);$('plan-steps').append(row);
    });
    $('plan-editor-detail').textContent=`${steps.length} steps · Save changes before running. Motion requires manual arming; the operator heartbeat must stay connected.`;
    controls();
  }
  function renderLibrary() {
    $('pose-library').replaceChildren();$('plan-library').replaceChildren();
    for(const pose of library.poses){
      const row=document.createElement('div');row.className='plan-library-item';const text=document.createElement('div');const title=document.createElement('strong');title.textContent=pose.name;const detail=document.createElement('small');detail.textContent=(pose.angles||[]).map(a=>Number(a).toFixed(1)+'°').join(' · ');text.append(title,detail);row.append(text);
      const buttons=document.createElement('div');buttons.className='plan-step-actions';
      const add=document.createElement('button');add.type='button';add.textContent='+ Step';add.addEventListener('click',()=>{steps.push({type:'pose',pose_id:pose.id,speed:35});changed();renderSteps();});
      const remove=document.createElement('button');remove.type='button';remove.textContent='Delete';remove.addEventListener('click',()=>attempt(async()=>{await call('pose_delete',{pose_id:pose.id});await refresh();message('Saved pose deleted. Existing plan snapshots are preserved.');}));buttons.append(add,remove);row.append(buttons);$('pose-library').append(row);
    }
    for(const plan of library.plans){const b=document.createElement('button');b.type='button';b.className='plan-saved-plan';const name=document.createElement('strong');name.textContent=plan.name;const count=document.createElement('small');count.textContent=`${plan.steps?.length||0} steps · Load for review`;b.append(name,count);b.addEventListener('click',()=>load(plan));$('plan-library').append(b);}
    for(const [id,items,text] of [['pose-library',library.poses,'No saved poses yet.'],['plan-library',library.plans,'No saved plans yet.']])if(!items.length){const p=document.createElement('p');p.className='caption';p.textContent=text;$(id).append(p);}
    $('plan-library-count').textContent=`${library.plans.length} PLANS`;
  }
  function load(plan) { selectedId=plan?.id||null;steps=copy(plan?.steps||[]);$('plan-name').value=plan?.name||'';dirty=false;renderSteps();message(plan?'Loaded for review. Arm explicitly before running.':'New plan.'); }
  async function refresh() {
    if(loading)return;loading=true;
    try{const result=await call('plan_list');library={poses:result.poses||[],plans:result.plans||[]};if(result.execution)execution=result.execution;renderLibrary();renderSteps();updateExecution();}catch(error){message(error.message,'error');}finally{loading=false;}
  }
  function updateExecution() {
    const active=Boolean(execution.running||execution.status==='running');
    $('plan-state').textContent=String(execution.phase||execution.status||(active?'running':'idle')).toUpperCase();
    $('plan-state').className=`tag ${active?'teal':''}`;
    const count=execution.total_steps??execution.total??execution.step_count;
    const index=execution.step_index??execution.current_step;
    $('plan-progress').textContent=execution.plan_name||execution.name||(active?'Plan running':'No plan running');
    $('plan-progress-detail').textContent=execution.error||(active?`Step ${Number.isInteger(index)?index+1:'—'}${count?` of ${count}`:''} · ${execution.message||execution.action||execution.phase||'executing'}`:execution.phase==='complete'||execution.status==='completed'?'Plan complete. Review the result before starting another routine.':'Arm motion explicitly before starting a saved plan. STOP cancels the whole routine.');
    controls();
  }
  $('plan-name').addEventListener('input',changed);
  $('plan-new').addEventListener('click',()=>load(null));
  $('plan-refresh').addEventListener('click',refresh);
  $('plan-add-action').addEventListener('click',()=>{steps.push({type:'action',name:'stand',steps:1,speed:35});changed();renderSteps();});
  $('plan-add-wait').addEventListener('click',()=>{steps.push({type:'wait',seconds:1});changed();renderSteps();});
  $('plan-add-pose').addEventListener('click',()=>{if(library.poses.length){steps.push({type:'pose',pose_id:library.poses[0].id,speed:35});changed();renderSteps();}});
  $('pose-form').addEventListener('submit',event=>{event.preventDefault();attempt(async()=>{await call('pose_save',{name:$('pose-name').value.trim()});$('pose-name').value='';await refresh();message('Current commanded servo positions saved.');});});
  $('plan-draft-form').addEventListener('submit',event=>{event.preventDefault();attempt(async()=>{const button=$('plan-draft');button.disabled=true;try{const result=await call('plan_draft',{text:$('plan-instructions').value.trim(),use_llm:$('plan-use-llm').checked},{timeout:180000});selectedId=null;steps=copy(result.steps||[]);$('plan-name').value=result.name||'New plan';dirty=true;renderSteps();message(`Draft ready${result.source?' · '+result.source:''}. Review every step, then save. ${(result.warnings||[]).join(' ')}`);}finally{button.disabled=false;}});});
  $('plan-editor').addEventListener('submit',event=>{event.preventDefault();attempt(async()=>{if(!steps.length)throw new Error('Add at least one step.');const args={name:$('plan-name').value.trim(),steps:copy(steps)};if(selectedId)args.plan_id=selectedId;const saved=await call('plan_save',args);await refresh();load(saved);message('Plan saved. Review it and arm motion before running.');});});
  $('plan-run').addEventListener('click',()=>attempt(async()=>{if(dirty||!selectedId)throw new Error('Save this plan before running.');const robot=getStatus();if(!robot?.armed)throw new Error('Arm motion before running this plan.');execution=await call('plan_run',{plan_id:selectedId});updateExecution();message('Saved plan started. Keep this controller connected.');}));
  $('plan-delete').addEventListener('click',()=>attempt(async()=>{await call('plan_delete',{plan_id:selectedId});load(null);await refresh();message('Plan deleted.');}));
  $('plan-stop').addEventListener('click',()=>attempt(async()=>{const result=await call('plan_stop');execution=result.plan||result;updateExecution();message('Plan stopped and motion disarmed.');}));
  renderSteps();renderLibrary();
  return {refresh,update(state){if(state?.plan)execution=state.plan;updateExecution();}};
}
