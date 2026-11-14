// SPDX-License-Identifier: MIT
const copy = value => JSON.parse(JSON.stringify(value));
const JOINTS = ['End / knee', 'Shoulder', 'Angle / hip'];
const LEGS = ['Right front', 'Left front', 'Left rear', 'Right rear'];
const BOUNDS = [[-90, 90], [-10, 90], [-60, 60]];
const finitePose = values => Array.isArray(values) && values.length === 12 && values.every(Number.isFinite);
const title = value => value ? value[0].toUpperCase() + value.slice(1) : 'New action';
const svgNS = 'http://www.w3.org/2000/svg';

export function initActionsEditor({root, call, getStatus, onChanged, onAddToPlan}) {
  root.innerHTML = `
    <div class="action-library-intro"><div><h2>One library for every controller</h2><p>Stand, Sit, Wave and your own actions are made of servo keyframes. Saving an action updates the dashboard, USB controller, plans and MCP clients.</p></div><button id="action-new" type="button" class="primary">+ New keyframe action</button></div>
    <div class="keyframe-workspace">
      <aside class="panel action-library-panel" aria-label="Shared motion actions"><div class="panel-heading"><h3>Actions</h3><span id="action-count" class="tag">LOADING</span></div><label class="action-search">Find an action<input id="action-search" type="search" placeholder="Stand, wave, my action…"></label><div id="action-library" class="action-library"></div></aside>
      <article class="panel keyframe-editor"><div class="panel-heading"><div><h2 id="action-editor-title">Choose an action</h2><p id="action-origin" class="caption">Loading the shared motion library.</p></div><span id="action-edit-state" class="tag">LOADING</span></div>
        <form id="action-form" class="admin-form" novalidate>
          <div class="action-metadata"><label>Action name<input id="action-name" maxlength="64" required placeholder="my greeting"></label><label>Playback<select id="action-playback"><option value="keyframes">Complete each keyframe</option><option value="continuous">Continuous sampled path</option></select></label></div>
          <label>Description<textarea id="action-description" maxlength="2000" rows="2" placeholder="What this action should do"></textarea></label>
          <p id="action-playback-help" class="caption"></p>
          <div class="keyframe-timeline-heading"><h3>Keyframes</h3><span id="action-frame-count" class="caption">0 frames</span></div>
          <div id="action-timeline" class="keyframe-timeline" role="list" aria-label="Action keyframes"></div>
          <div class="keyframe-tools"><button id="frame-add" type="button">+ Add frame</button><button id="frame-duplicate" type="button">Duplicate frame</button><button id="frame-left" type="button" aria-label="Move selected frame earlier">← Earlier</button><button id="frame-right" type="button" aria-label="Move selected frame later">Later →</button><button id="frame-remove" type="button" class="delete-button">Remove frame</button></div>
          <div class="keyframe-preview"><div class="keyframe-preview-heading"><div><h3>Pose preview</h3><p class="caption">Side view of each leg · browser only · no robot movement</p></div><button id="action-preview" type="button" class="outline">▶ Preview sequence</button></div><svg id="action-pose-preview" viewBox="0 0 680 190" role="img" aria-label="Selected keyframe leg positions"></svg><label class="preview-scrubber">Scrub keyframes<input id="action-scrub" type="range" min="0" max="0" step="0.01" value="0"></label><p id="action-preview-detail" class="caption">Preview uses commanded angles, without position feedback.</p></div>
          <div class="selected-keyframe-heading"><h3 id="frame-editor-title">Frame 1</h3><button id="frame-capture" type="button">Use current servo positions</button></div>
          <div class="keyframe-transition"><label>Transition speed into this frame<div class="keyframe-unit"><input id="frame-speed" type="number" min="1" max="144" step="any" required><span>°/s</span></div></label><label>Hold after reaching this frame<div class="keyframe-unit"><input id="frame-hold" type="number" min="0" max="30" step="any" required><span>seconds</span></div></label></div>
          <p id="action-budget-hint" class="caption"></p>
          <div id="frame-angles" class="keyframe-leg-grid"></div>
          <p class="caption keyframe-position-note">Angles are logical servo commands. The first physical pose is unknown after restart; initialization is staggered and cannot be previewed as measured movement.</p>
          <div id="action-notice" class="notice" role="status" aria-live="polite" hidden></div>
          <p id="action-validation" class="keyframe-validation" role="status"></p>
          <div class="action-save-row"><button id="action-save" type="submit" class="primary" disabled>Save action</button><button id="action-discard" type="button" disabled>Discard changes</button><button id="action-copy" type="button">Save as new action</button><button id="action-run" type="button" class="outline" disabled>Run saved action ↗</button><button id="action-add-to-plan" type="button" disabled>+ Add to routine</button></div>
          <div class="action-maintenance"><button id="action-reset" type="button" class="subtle" hidden>Restore built-in default</button><button id="action-delete" type="button" class="delete-button" hidden>Delete action</button><p id="action-save-hint" class="caption">Saving never starts movement. Run requires arming.</p></div>
        </form>
      </article>
    </div>`;
  const $ = id => root.querySelector(`#${id}`);
  let actions = [], draft = null, baseline = null, selectedName = null, selectedFrame = 0;
  let dirty = false, saving = false, previewTimer = null, previewFrame = 0, previewStarted = 0;
  let maxFrames = 128, previewAngles = null, previewWidth = 0;
  const angleInputs = [];
  for (let leg = 0; leg < 4; leg++) {
    const fieldset = document.createElement('fieldset');
    const legend = document.createElement('legend'); legend.textContent = LEGS[leg]; fieldset.append(legend);
    for (let joint = 0; joint < 3; joint++) {
      const index = leg * 3 + joint, label = document.createElement('label'); label.textContent = JOINTS[joint];
      const unit = document.createElement('div'); unit.className = 'keyframe-unit';
      const input = document.createElement('input'); Object.assign(input, {type: 'number', min: BOUNDS[joint][0], max: BOUNDS[joint][1], step: 'any', required: true});
      input.id = `frame-angle-${index}`; input.setAttribute('aria-label', `${LEGS[leg]} ${JOINTS[joint]} angle`);
      const suffix = document.createElement('span'); suffix.textContent = '°'; unit.append(input, suffix); label.append(unit); fieldset.append(label); angleInputs.push(input);
      input.addEventListener('input', () => { if (draft) { draft.frames[selectedFrame].angles[index] = input.value === '' ? NaN : Number(input.value); changed(false); } });
    }
    $('frame-angles').append(fieldset);
  }
  function message(text, kind = '') { $('action-notice').textContent = text; $('action-notice').className = `notice ${kind}`; $('action-notice').hidden = !text; }
  function stopPreview() { clearInterval(previewTimer); previewTimer = null; $('action-preview').textContent = '▶ Preview sequence'; }
  function validation() {
    if (!draft) return 'Choose an action or create a new one.';
    if (!/^[a-z0-9][a-z0-9 _-]{0,63}$/.test(draft.name) || draft.name.includes('  ') || ['stop','arm','halt'].includes(draft.name)) return 'Use an action name of 1–64 lowercase letters, numbers, spaces, underscores or hyphens; stop, arm and halt are reserved.';
    if (!draft.frames.length || draft.frames.length > maxFrames) return `An action needs 1–${maxFrames} keyframes.`;
    for (const [index, frame] of draft.frames.entries()) {
      if (!finitePose(frame.angles)) return `Frame ${index + 1} needs all twelve finite servo angles.`;
      for (const [joint, angle] of frame.angles.entries()) if (angle < BOUNDS[joint % 3][0] || angle > BOUNDS[joint % 3][1]) return `Frame ${index + 1}, ${LEGS[Math.floor(joint / 3)]} ${JOINTS[joint % 3]} must be between ${BOUNDS[joint % 3].join(' and ')}°.`;
      if (!Number.isFinite(frame.speed_deg_s) || frame.speed_deg_s < 1 || frame.speed_deg_s > 144) return `Frame ${index + 1} transition speed must be 1–144°/s.`;
      if (!Number.isFinite(frame.hold_seconds) || frame.hold_seconds < 0 || frame.hold_seconds > 30) return `Frame ${index + 1} hold must be 0–30 seconds.`;
    }
    if (!selectedName && actions.some(action => action.name === draft.name)) return 'That action name already exists. Choose another name or edit the existing action.';
    return '';
  }
  function controls() {
    const robot = getStatus() || {}, blocked = robot.busy || robot.plan?.running || (robot.examples?.running && !robot.examples?.permits_manual_motion);
    const known = typeof robot.armed === 'boolean' && typeof robot.busy === 'boolean';
    const error = validation(), count = draft?.frames?.length || 0;
    for (const id of ['action-name','action-description','action-playback','frame-speed','frame-hold']) $(id).disabled = saving || !draft;
    for (const input of angleInputs) input.disabled = saving || !draft;
    for (const button of root.querySelectorAll('.action-library-item,.keyframe-card')) button.disabled = saving;
    $('action-save').disabled = saving || !dirty || !known || blocked || Boolean(error);
    $('action-save').textContent = saving ? 'Saving…' : robot.armed ? 'Disarm and save action' : 'Save action';
    $('action-discard').disabled = saving || !dirty;
    $('action-run').disabled = saving || dirty || !selectedName || !known || !robot.armed || blocked || robot.estop;
    $('action-add-to-plan').disabled = !selectedName || dirty || saving;
    $('action-edit-state').textContent = saving ? 'SAVING' : dirty ? 'UNSAVED CHANGES' : draft ? 'SAVED' : 'NO ACTION';
    $('action-edit-state').className = `tag ${dirty ? 'orange' : ''}`;
    $('action-validation').textContent = dirty ? error : '';
    $('action-new').disabled = saving || (!finitePose(robot.servo_angles) && !actions.some(a => a.name === 'stand'));
    $('action-copy').disabled = saving || !draft;
    $('frame-add').disabled = saving || !draft || count >= maxFrames;
    $('frame-duplicate').disabled = saving || !draft || count >= maxFrames;
    $('frame-left').disabled = saving || !draft || selectedFrame === 0;
    $('frame-right').disabled = saving || !draft || selectedFrame >= count - 1;
    $('frame-remove').disabled = saving || !draft || count <= 1;
    $('frame-capture').disabled = saving || !draft || blocked || !finitePose(robot.servo_angles);
    $('action-preview').disabled = !draft || count < 2 || draft.frames.some(f => !finitePose(f.angles) || !Number.isFinite(f.speed_deg_s) || f.speed_deg_s <= 0 || !Number.isFinite(f.hold_seconds));
    $('action-scrub').disabled = !draft || count < 2;
    $('action-reset').hidden = !baseline?.builtin;
    $('action-reset').disabled = saving || dirty || !baseline?.modified || !known || blocked;
    $('action-reset').textContent = robot.armed ? 'Disarm and restore default' : 'Restore built-in default';
    $('action-delete').hidden = !selectedName || baseline?.builtin;
    $('action-delete').disabled = saving || dirty || !known || robot.armed || blocked;
    $('action-save-hint').textContent = !known ? 'Robot state is unavailable; saving and running are disabled.' : blocked ? 'Stop or finish the current movement, plan or test before saving or running.' : error && dirty ? 'Correct the highlighted values before saving. Your draft is kept.' : dirty && robot.armed ? 'Saving explicitly disarms motion. The action will not run until you arm and run it.' : dirty ? 'Saving updates this shared action everywhere. It never moves the robot.' : !robot.armed ? 'Saved action. Arm motion to run; editing and previewing never move the robot.' : 'Saved action. Use Run to move the robot, or Preview for a browser-only animation.';
    const budget = robot.servo_budget || {};
    $('action-budget-hint').textContent = `The frame rate is a ceiling for changing servo angles. The caller’s speed setting and the robot’s power budget can slow it further.${Number.isFinite(budget.per_servo_deg_s) ? ` Current per-servo limit: ${budget.per_servo_deg_s}°/s.` : ''}`;
    $('action-playback-help').textContent = draft?.playback === 'continuous' ? 'Continuous paths join sampled waypoints without an intentional stop at each one. Holds and speed changes divide the path. Used by walking and turning actions.' : 'Each keyframe is completed before the next begins. The speed and hold belong to the selected destination frame.';
  }
  function drawPose(angles) {
    const svg = $('action-pose-preview'); svg.replaceChildren();
    if (!finitePose(angles)) return;
    previewAngles = [...angles];
    const columns = svg.clientWidth < 500 ? 2 : 4;
    svg.setAttribute('viewBox', `0 0 ${columns * 170} ${Math.ceil(4 / columns) * 190}`);
    const element = (tag, attributes, content) => { const value = document.createElementNS(svgNS, tag); for (const [key, val] of Object.entries(attributes)) value.setAttribute(key, val); if (content !== undefined) value.textContent = content; svg.append(value); return value; };
    for (let leg = 0; leg < 4; leg++) {
      const knee = angles[leg * 3] * Math.PI / 180, shoulder = angles[leg * 3 + 1] * Math.PI / 180;
      const upper = Math.PI / 2 - shoulder, lower = upper + knee - Math.PI / 2;
      const points = [[0, 0], [33, 0], [33 + 48 * Math.cos(upper), 48 * Math.sin(upper)]];
      points.push([points[2][0] + 78 * Math.cos(lower), points[2][1] + 78 * Math.sin(lower)]);
      const row = Math.floor(leg / columns) * 190, x = 18 + (leg % columns) * 170, y = row + 78;
      element('text', {x: x + 67, y: row + 18, 'text-anchor': 'middle', class: 'preview-leg-title'}, LEGS[leg]);
      element('line', {x1: x, x2: x + 148, y1: row + 150, y2: row + 150, class: 'preview-ground'});
      element('polyline', {points: points.map(([r,z]) => `${x + r * .85},${y - z * .85}`).join(' '), class: 'preview-limb'});
      points.forEach(([r,z], i) => element('circle', {cx: x + r * .85, cy: y - z * .85, r: i === 3 ? 4.5 : 3, class: i === 3 ? 'preview-foot' : 'preview-joint'}));
      element('text', {x: x + 67, y: row + 177, 'text-anchor': 'middle', class: 'preview-angle-label'}, `Hip angle ${angles[leg * 3 + 2].toFixed(1)}°`);
    }
  }
  function scrub(value) {
    if (!draft?.frames.length) return;
    const low = Math.min(draft.frames.length - 1, Math.floor(value)), high = Math.min(draft.frames.length - 1, low + 1), weight = value - low;
    const a = draft.frames[low].angles, b = draft.frames[high].angles;
    if (finitePose(a) && finitePose(b)) drawPose(a.map((v, i) => v + (b[i] - v) * weight));
    $('action-preview-detail').textContent = `Preview frame ${(value + 1).toFixed(weight ? 2 : 0)} of ${draft.frames.length}. Commanded angles only; actual motion follows power limits and the selected joint order.`;
  }
  function renderTimeline() {
    $('action-timeline').replaceChildren();
    for (const [index, frame] of (draft?.frames || []).entries()) {
      const item = document.createElement('div'); item.setAttribute('role', 'listitem');
      const button = document.createElement('button'); button.type = 'button'; button.className = 'keyframe-card'; button.dataset.frame = index;
      button.setAttribute('aria-label', `Edit keyframe ${index + 1}`); button.setAttribute('aria-pressed', String(index === selectedFrame));
      const number = document.createElement('strong'); number.textContent = `${index + 1}`;
      const rate = document.createElement('span'); rate.textContent = `${Number.isFinite(frame.speed_deg_s) ? frame.speed_deg_s : '—'}°/s`;
      const hold = document.createElement('small'); hold.textContent = frame.hold_seconds ? `Hold ${frame.hold_seconds}s` : 'No hold'; button.append(number, rate, hold);
      button.addEventListener('click', () => { stopPreview(); selectedFrame = index; renderSelected(); }); item.append(button); $('action-timeline').append(item);
    }
    $('action-frame-count').textContent = `${draft?.frames.length || 0} / ${maxFrames} frames`;
  }
  function renderSelected() {
    const frame = draft?.frames?.[selectedFrame];
    $('frame-editor-title').textContent = `Frame ${selectedFrame + 1} · servo targets`;
    $('frame-speed').value = frame?.speed_deg_s ?? 30; $('frame-hold').value = frame?.hold_seconds ?? 0;
    angleInputs.forEach((input, i) => { input.value = Number.isFinite(frame?.angles[i]) ? Number(frame.angles[i].toFixed(3)) : ''; input.title = Number.isFinite(frame?.angles[i]) ? `Stored command: ${frame.angles[i]}°` : '';  input.disabled = !frame; });
    $('action-scrub').max = Math.max(0, (draft?.frames.length || 1) - 1); $('action-scrub').value = selectedFrame;
    renderTimeline(); scrub(selectedFrame); controls();
  }
  function changed(timeline = true) { stopPreview(); dirty = true; message(''); if (timeline) renderTimeline(); scrub(selectedFrame); controls(); }
  function setDraft(action, name = action?.name || null, isNew = false) {
    stopPreview(); selectedName = name; baseline = action ? copy(action) : null; draft = action ? copy(action) : null; selectedFrame = 0; dirty = isNew;
    $('action-name').value = draft?.name || ''; $('action-name').readOnly = Boolean(name);
    $('action-description').value = draft?.description || ''; $('action-playback').value = draft?.playback || 'keyframes';
    $('action-editor-title').textContent = name ? title(name) : 'New keyframe action';
    $('action-origin').textContent = !draft ? 'The action library is unavailable. Refresh the library to try again.' : isNew ? 'New action · save it to make it available to every controller' : draft.builtin ? draft.modified ? 'Built-in action · customized definition' : 'Built-in action · default definition' : 'Custom action · shared motion library';
    renderSelected(); renderLibrary();
  }
  function leaveDraft() { if (!dirty) return true; message('Save or discard your current changes before opening another action.', 'warning'); $('action-notice').scrollIntoView({block: 'nearest'}); return false; }
  function renderLibrary() {
    const query = $('action-search').value.toLowerCase().trim(); $('action-library').replaceChildren();
    for (const action of actions.filter(a => a.name.toLowerCase().includes(query))) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'action-library-item'; button.dataset.action = action.name; button.setAttribute('aria-pressed', String(action.name === selectedName));
      const name = document.createElement('strong'); name.textContent = title(action.name); const detail = document.createElement('small'); detail.textContent = `${action.frames.length} ${action.frames.length === 1 ? 'frame' : 'frames'} · ${action.builtin ? action.modified ? 'Customized' : 'Built-in' : 'Custom'}`;
      button.append(name, detail); button.addEventListener('click', () => { if (action.name !== selectedName && !saving && leaveDraft()) { setDraft(action); message(''); } }); $('action-library').append(button);
    }
    if (!actions.length) { const text = document.createElement('p'); text.className = 'caption'; text.textContent = 'No actions loaded. Refresh the library to try again.'; $('action-library').append(text); }
    $('action-count').textContent = `${actions.length} ACTIONS`;
  }
  function freshAction(fromSelected = false) {
    if (!fromSelected && !leaveDraft()) return;
    const robot = getStatus() || {}, seed = fromSelected ? draft : actions.find(a => a.name === 'stand');
    let frames;
    if (fromSelected && seed) frames = copy(seed.frames);
    else if (finitePose(robot.servo_angles) && !robot.busy) frames = [{angles: [...robot.servo_angles], speed_deg_s: 30, hold_seconds: 0}];
    else if (seed) frames = [copy(seed.frames[0])];
    if (!frames) return;
    setDraft({name: '', description: fromSelected ? draft?.description || '' : '', frames, playback: fromSelected ? draft?.playback || 'keyframes' : 'keyframes', builtin: false, modified: false}, null, true);
    message(fromSelected ? 'Copied keyframes into a new action. Choose a unique name and save.' : finitePose(robot.servo_angles) && !robot.busy ? 'New action starts from the current commanded servo positions.' : 'New action starts from the saved Stand keyframe, not a measured physical pose.');
    $('action-name').focus();
  }
  async function publish(action) {
    const index = actions.findIndex(a => a.name === action.name); if (index < 0) actions.push(copy(action)); else actions[index] = copy(action);
    setDraft(action); await onChanged?.(copy(actions));
  }
  $('action-search').addEventListener('input', renderLibrary);
  $('action-new').addEventListener('click', () => freshAction());
  $('action-copy').addEventListener('click', () => freshAction(true));
  $('action-name').addEventListener('input', () => { if (draft) { draft.name = $('action-name').value.trim().toLowerCase(); changed(false); } });
  $('action-description').addEventListener('input', () => { if (draft) { draft.description = $('action-description').value; changed(false); } });
  $('action-playback').addEventListener('change', () => { if (draft) { draft.playback = $('action-playback').value; changed(false); } });
  for (const [id, field] of [['frame-speed', 'speed_deg_s'], ['frame-hold', 'hold_seconds']]) $(id).addEventListener('input', () => { if (draft) { draft.frames[selectedFrame][field] = $(id).value === '' ? NaN : Number($(id).value); changed(); } });
  for (const id of ['frame-add', 'frame-duplicate']) $(id).addEventListener('click', () => { if (!draft || draft.frames.length >= maxFrames) return; draft.frames.splice(selectedFrame + 1, 0, copy(draft.frames[selectedFrame])); selectedFrame++; changed(); renderSelected(); });
  for (const [id, delta] of [['frame-left', -1], ['frame-right', 1]]) $(id).addEventListener('click', () => { const next = selectedFrame + delta; if (!draft || next < 0 || next >= draft.frames.length) return; [draft.frames[selectedFrame], draft.frames[next]] = [draft.frames[next], draft.frames[selectedFrame]]; selectedFrame = next; changed(); renderSelected(); });
  $('frame-remove').addEventListener('click', () => { if (!draft || draft.frames.length <= 1) return; draft.frames.splice(selectedFrame, 1); selectedFrame = Math.min(selectedFrame, draft.frames.length - 1); changed(); renderSelected(); });
  $('frame-capture').addEventListener('click', () => { const robot = getStatus() || {}; if (draft && finitePose(robot.servo_angles) && !robot.busy) { draft.frames[selectedFrame].angles = [...robot.servo_angles]; changed(); renderSelected(); message('Current commanded angles copied into this frame. Save to keep them.'); } });
  $('action-scrub').addEventListener('input', () => { stopPreview(); scrub(Number($('action-scrub').value)); });
  $('action-preview').addEventListener('click', () => {
    if (previewTimer) { stopPreview(); return; }
    if (!draft || draft.frames.length < 2) return;
    previewFrame = 0; previewStarted = performance.now(); $('action-preview').textContent = '■ Stop preview'; scrub(0);
    previewTimer = setInterval(() => {
      if (document.hidden || root.closest('[hidden]')) { stopPreview(); return; }
      const a = draft.frames[previewFrame], b = draft.frames[previewFrame + 1];
      if (!b) { stopPreview(); return; }
      const duration = Math.max(.05, Math.max(...a.angles.map((v, i) => Math.abs(v - b.angles[i]))) / b.speed_deg_s);
      const elapsed = (performance.now() - previewStarted) / 1000;
      const weight = Math.max(0, Math.min(1, (elapsed - a.hold_seconds) / duration));
      $('action-scrub').value = previewFrame + weight; scrub(previewFrame + weight);
      if (weight === 1) { previewFrame++; previewStarted = performance.now(); if (previewFrame === draft.frames.length - 1) stopPreview(); }
    }, 100);
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopPreview(); });
  $('action-discard').addEventListener('click', () => { if (selectedName) setDraft(actions.find(a => a.name === selectedName) || baseline); else { dirty = false; setDraft(actions.find(a => a.name === 'stand') || actions[0] || null); } message('Unsaved changes discarded.'); });
  $('action-form').addEventListener('submit', async event => {
    event.preventDefault(); controls(); if ($('action-save').disabled) return;
    const submitted = copy(draft), wasArmed = getStatus()?.armed; saving = true; stopPreview(); controls(); message('');
    try {
      const result = await call('action_save', {name: submitted.name, frames: submitted.frames, description: submitted.description || '', playback: submitted.playback, ...(wasArmed ? {disarm: true} : {})});
      await publish(result); message('Action saved. Every controller now uses this definition.');
    } catch (error) { message(error.message, 'error'); }
    finally { saving = false; controls(); }
  });
  $('action-run').addEventListener('click', async () => { controls(); if ($('action-run').disabled) return; try { await call('move', {name: selectedName, speed: 80, steps: 1}); message('Saved action started. Keep this interface connected.'); } catch (error) { message(error.message, 'error'); } });
  $('action-add-to-plan').addEventListener('click', () => { if (selectedName && !dirty) onAddToPlan?.(selectedName); });
  $('action-reset').addEventListener('click', async () => { controls(); if ($('action-reset').disabled) return; saving = true; controls(); try { await publish(await call('action_reset', {name: selectedName, ...(getStatus()?.armed ? {disarm: true} : {})})); message('Built-in action restored.'); } catch (error) { message(error.message, 'error'); } finally { saving = false; controls(); } });
  $('action-delete').addEventListener('click', async () => { controls(); if ($('action-delete').disabled) return; saving = true; controls(); try { await call('action_delete', {name: selectedName}); actions = actions.filter(a => a.name !== selectedName); setDraft(actions.find(a => a.name === 'stand') || actions[0] || null); await onChanged?.(copy(actions)); message('Custom action deleted.'); } catch (error) { message(error.message, 'error'); } finally { saving = false; controls(); } });
  new ResizeObserver(entries => { const width = Math.round(entries[0].contentRect.width); if (width !== previewWidth) { previewWidth = width; if (width && previewAngles) drawPose(previewAngles); } }).observe($('action-pose-preview'));
  setDraft(null);
  return {
    setLibrary(next, limits = {}) {
      actions = copy(next || []); maxFrames = limits.max_frames || limits.max_keyframes || 128;
      if (!dirty && !saving) { const action = actions.find(a => a.name === selectedName) || actions.find(a => a.name === 'stand') || actions[0] || null; setDraft(action); }
      else renderLibrary();
      controls();
    },
    update() { controls(); },
    stopPreview,
  };
}
