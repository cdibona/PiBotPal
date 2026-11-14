// SPDX-License-Identifier: MIT
import {uniqueActionName, sequenceTiming, sampleSequence, createPoseThumbnail} from './sequencer.js';
const copy = value => JSON.parse(JSON.stringify(value));
const JOINTS = ['End / knee', 'Shoulder', 'Angle / hip'];
const LEGS = ['Right front', 'Left front', 'Left rear', 'Right rear'];
const BOUNDS = [[-90, 90], [-10, 90], [-60, 60]];
const finitePose = values => Array.isArray(values) && values.length === 12 && values.every(Number.isFinite);
const title = value => value ? value[0].toUpperCase() + value.slice(1) : 'New action';
const svgNS = 'http://www.w3.org/2000/svg';

export function initActionsEditor({root, call, getStatus, onChanged, onAddToPlan, playback}) {
  root.innerHTML = `
    <div class="action-library-intro action-studio-toolbar"><p>Choose an action to edit, or duplicate it to make your own.</p><button id="action-new" type="button" class="primary">+ New keyframe action</button></div>
    <div class="keyframe-workspace">
      <aside class="panel action-library-panel" aria-label="Shared motion actions"><div class="panel-heading"><h3>Actions</h3><span id="action-count" class="tag">LOADING</span></div><label class="action-search">Find an action<input id="action-search" type="search" placeholder="Stand, wave, my action…"></label><div id="action-library" class="action-library"></div></aside>
      <article class="panel keyframe-editor"><div class="panel-heading action-title-row"><div><h2 id="action-editor-title">Choose an action</h2><p id="action-origin" class="caption">Loading the shared motion library.</p></div><div class="action-title-actions"><span id="action-edit-state" class="tag">LOADING</span><button id="action-copy" type="button">⧉ Duplicate action</button><button id="action-save" type="submit" form="action-form" class="primary" disabled>Save action</button></div></div>
        <div class="action-transport" aria-label="Action playback on the robot"><div class="action-transport-buttons"><button id="action-run" type="button" class="primary" disabled>▶ Arm &amp; play on robot</button><button id="action-stop" type="button" class="danger">■ Stop robot</button></div><p id="action-run-hint" class="caption">Select a saved action to play it on the robot.</p><p id="action-progress" class="action-progress" role="status" aria-live="polite"></p><p id="action-transport-notice" class="notice" role="status" hidden></p></div>
        <form id="action-form" class="admin-form" novalidate>
          <div id="action-notice" class="notice" role="status" aria-live="polite" hidden></div><p id="action-validation" class="keyframe-validation" role="status"></p>
          <section class="action-sequencer" aria-label="Keyframe filmstrip">
            <div class="keyframe-timeline-heading"><div><h3>Keyframe filmstrip</h3><p class="caption">Click a pose to edit. Drag to reorder, or use Earlier / Later.</p></div><span id="action-frame-count" class="caption">0 frames</span></div>
            <div class="sequence-preview-transport" aria-label="Browser preview controls"><button id="action-rewind" type="button" aria-label="Rewind browser preview">↤ Rewind</button><button id="action-preview" type="button" class="outline">▶ Preview</button><button id="action-loop" type="button" aria-pressed="false">↻ Loop</button><output id="action-preview-time">0.00 / 0.00 s</output><span class="preview-only-label">BROWSER ONLY</span></div>
            <div class="filmstrip-scroll"><div class="filmstrip-track"><div id="action-ruler" class="filmstrip-ruler" aria-hidden="true"></div><div id="action-timeline" class="keyframe-timeline" role="list" aria-label="Action keyframes"></div><div id="action-playhead" class="filmstrip-playhead" aria-hidden="true"></div></div></div>
            <label class="preview-scrubber sequence-time-scrubber">Preview time<input id="action-time-scrub" type="range" min="0" max="0" step="0.001" value="0"></label>
            <p id="action-preview-detail" class="caption">Preview starts at the first saved pose; the robot’s approach to that pose is not timed.</p>
            <div class="keyframe-tools"><button id="frame-previous" type="button" aria-label="Select previous keyframe">‹ Previous</button><button id="frame-next" type="button" aria-label="Select next keyframe">Next ›</button><button id="frame-add" type="button">+ Add frame</button><button id="frame-duplicate" type="button">Duplicate frame</button><button id="frame-left" type="button" aria-label="Move selected frame earlier">← Earlier</button><button id="frame-right" type="button" aria-label="Move selected frame later">Later →</button><button id="frame-remove" type="button" class="delete-button">Remove frame</button></div>
          </section>
          <div class="selected-keyframe-heading"><h3 id="frame-editor-title">Frame 1</h3><button id="frame-capture" type="button">Use current servo positions</button></div>
          <div class="sequence-inspector">
            <div class="keyframe-preview"><div class="keyframe-preview-heading"><div><h3>Pose canvas</h3><p class="caption">Side view of each leg. Click an outlined joint to edit the selected frame.</p></div><span id="action-canvas-state" class="tag">EDITING FRAME 1</span></div><svg id="action-pose-preview" viewBox="0 0 680 190" role="group" aria-label="Interactive keyframe servo joints"></svg></div>
            <div class="sequence-frame-inspector"><div id="selected-joint-editor" class="selected-joint-editor"><div><strong id="selected-joint-name">Select a joint on the canvas</strong><p id="selected-joint-detail" class="caption">Outlined nodes are servo pivots. Solid feet are endpoints.</p></div><div class="selected-joint-inputs"><label>Joint angle<input id="selected-joint-slider" type="range" min="-90" max="90" step="0.1" disabled></label><label>Degrees<div class="keyframe-unit"><input id="selected-joint-angle" type="number" step="any" disabled><span>°</span></div></label></div></div>
              <div class="keyframe-transition"><label>Transition speed into this frame<div class="keyframe-unit"><input id="frame-speed" type="number" min="1" max="144" step="any" required><span>°/s</span></div></label><label>Hold at this frame<div class="keyframe-unit"><input id="frame-hold" type="number" min="0" max="30" step="any" required><span>seconds</span></div></label></div><p id="frame-timing-detail" class="caption"></p><p id="action-budget-hint" class="caption"></p>
            </div>
          </div>
          <details id="action-angle-details" class="sequence-details"><summary>All 12 servo angles</summary><div class="sequence-details-body"><div id="frame-angles" class="keyframe-leg-grid"></div><label class="preview-scrubber">Scrub between frame positions<input id="action-scrub" type="range" min="0" max="0" step="0.01" value="0"></label><p class="caption keyframe-position-note">Angles are logical servo commands, without position feedback. The robot’s first physical pose is unknown after restart.</p></div></details>
          <details id="action-metadata-details" class="sequence-details"><summary>Action name, description &amp; playback</summary><div class="sequence-details-body"><div class="action-metadata"><label>Action name<input id="action-name" maxlength="64" required placeholder="my greeting"></label><label>Playback<select id="action-playback"><option value="keyframes">Complete each keyframe</option><option value="continuous">Continuous sampled path</option></select></label></div><label>Description<textarea id="action-description" maxlength="2000" rows="2" placeholder="What this action should do"></textarea></label><p id="action-playback-help" class="caption"></p></div></details>
          <div class="action-save-row"><button id="action-discard" type="button" disabled>Discard changes</button><button id="action-add-to-plan" type="button" disabled>+ Add to routine</button></div>
          <div class="action-maintenance"><button id="action-reset" type="button" class="subtle" hidden>Restore built-in default</button><button id="action-delete" type="button" class="delete-button" hidden>Delete action</button><p id="action-save-hint" class="caption">Saving never starts movement.</p></div>
        </form>
      </article>
    </div>`;
  const $ = id => root.querySelector(`#${id}`);
  let actions = [], draft = null, baseline = null, selectedName = null, selectedFrame = 0, selectedJoint = null;
  let dirty = false, saving = false, previewTimer = null, previewStarted = 0, previewTime = 0, previewLoop = false, dragFrame = null;
  let timing = {duration: 0, frames: [], valid: false};
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
      input.addEventListener('input', () => { if (draft) { draft.frames[selectedFrame].angles[index] = input.value === '' ? NaN : Number(input.value); changed(false); syncJointEditor(); } });
    }
    $('frame-angles').append(fieldset);
  }
  function message(text, kind = '') { $('action-notice').textContent = text; $('action-notice').className = `notice ${kind}`; $('action-notice').hidden = !text; }
  function stopPreview() { clearInterval(previewTimer); previewTimer = null; $('action-preview').textContent = '▶ Preview'; }
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
    const error = validation(), count = draft?.frames?.length || 0, pending = Boolean(playback?.pending), editingLocked = saving || pending;
    const k = robot.keyframes || {}, active = robot.busy && ['moving','holding'].includes(k.phase);
    const matching = k.action === selectedName && k.revision === baseline?.revision;
    const first = Number.isInteger(k.frame_index) ? k.frame_index : -1, last = Number.isInteger(k.frame_end_index) ? k.frame_end_index : first;
    for (const card of root.querySelectorAll('.keyframe-card')) card.classList.toggle('is-playing', Boolean(active && matching && !dirty && Number(card.dataset.frame) >= first && Number(card.dataset.frame) <= last));
    $('action-progress').textContent = active ? `${title(k.action)} · ${k.phase === 'holding' ? 'holding' : 'playing'} frame${last > first ? 's' : ''} ${first + 1}${last > first ? `–${last + 1}` : ''} of ${k.frame_count} · cycle ${k.cycle || 1}/${k.cycles || 1}${dirty && k.action === selectedName ? ' · saved revision is playing; your draft is separate' : ''}` : matching && ['complete','cancelled','failed'].includes(k.phase) ? `${title(k.action)} · ${k.phase}` : '';
    $('action-stop').disabled = !known || !playback || playback.stopping;
    $('action-stop').textContent = playback?.stopping ? 'Stopping…' : '■ Stop robot';
    for (const id of ['action-name','action-description','action-playback','frame-speed','frame-hold']) $(id).disabled = editingLocked || !draft;
    for (const input of angleInputs) input.disabled = editingLocked || !draft;
    for (const button of root.querySelectorAll('.action-library-item,.keyframe-card')) button.disabled = editingLocked;
    $('action-save').disabled = editingLocked || !dirty || !known || blocked || Boolean(error);
    $('action-save').textContent = saving ? 'Saving…' : robot.armed ? 'Disarm and save action' : 'Save action';
    $('action-discard').disabled = editingLocked || !dirty;
    $('action-run').disabled = editingLocked || dirty || !selectedName || !known || !playback || blocked || Boolean(error);
    $('action-run').textContent = playback?.stopping ? 'Stopping…' : pending ? 'Starting…' : robot.armed ? '▶ Play on robot' : '▶ Arm & play on robot';
    $('action-run-hint').textContent = !known ? 'Connect to the robot to play this action.' : pending ? 'Waiting for the robot. Stop remains available.' : dirty ? 'Save your keyframe changes before playing on the robot.' : !selectedName ? 'Save this new action before playing it.' : blocked ? 'Stop or finish the current motion, routine or test before playing.' : robot.armed ? 'Plays this saved action once using its keyframe speeds and the power budget.' : 'Arm & play enables motion and starts this saved action once. Editing and Preview never move the robot.';
    for (const id of ['selected-joint-slider','selected-joint-angle']) $(id).disabled = editingLocked || selectedJoint === null || !draft;
    for (const node of root.querySelectorAll('.preview-joint-node')) { node.setAttribute('aria-disabled',String(editingLocked)); node.tabIndex = editingLocked ? -1 : 0; }
    $('action-add-to-plan').disabled = !selectedName || dirty || editingLocked;
    $('action-edit-state').textContent = saving ? 'SAVING' : dirty ? 'UNSAVED CHANGES' : draft ? 'SAVED' : 'NO ACTION';
    $('action-edit-state').className = `tag ${dirty ? 'orange' : ''}`;
    $('action-validation').textContent = dirty ? error : '';
    $('action-new').disabled = editingLocked || (!finitePose(robot.servo_angles) && !actions.some(a => a.name === 'stand'));
    $('action-copy').disabled = editingLocked || !draft;
    $('frame-previous').disabled = editingLocked || !draft || selectedFrame === 0;
    $('frame-next').disabled = editingLocked || !draft || selectedFrame >= count - 1;
    $('action-rewind').disabled = editingLocked || !timing.valid;
    $('action-loop').disabled = editingLocked || !timing.valid;
    $('action-time-scrub').disabled = editingLocked || !timing.valid || timing.duration <= 0;
    $('frame-add').disabled = editingLocked || !draft || count >= maxFrames;
    $('frame-duplicate').disabled = editingLocked || !draft || count >= maxFrames;
    $('frame-left').disabled = editingLocked || !draft || selectedFrame === 0;
    $('frame-right').disabled = editingLocked || !draft || selectedFrame >= count - 1;
    $('frame-remove').disabled = editingLocked || !draft || count <= 1;
    $('frame-capture').disabled = editingLocked || !draft || blocked || !finitePose(robot.servo_angles);
    $('action-preview').disabled = editingLocked || !draft || !timing.valid || timing.duration <= 0;
    $('action-scrub').disabled = editingLocked || !draft || count < 2;
    $('action-reset').hidden = !baseline?.builtin;
    $('action-reset').disabled = editingLocked || dirty || !baseline?.modified || !known || blocked;
    $('action-reset').textContent = robot.armed ? 'Disarm and restore default' : 'Restore built-in default';
    $('action-delete').hidden = !selectedName || baseline?.builtin;
    $('action-delete').disabled = editingLocked || dirty || !known || robot.armed || blocked;
    $('action-save-hint').textContent = !known ? 'Robot state is unavailable; saving and running are disabled.' : blocked ? 'Stop or finish the current movement, plan or test before saving or running.' : error && dirty ? 'Correct the highlighted values before saving. Your draft is kept.' : dirty && robot.armed ? 'Saving explicitly disarms motion. The action will not run until you arm and run it.' : dirty ? 'Saving updates this shared action everywhere. It never moves the robot.' : !robot.armed ? 'Saved action. Use Arm & play above to run; editing and previewing never move the robot.' : 'Saved action. Use Play on robot above, or Preview for a browser-only animation.';
    const budget = robot.servo_budget || {};
    $('action-budget-hint').textContent = `The frame rate is a ceiling for changing servo angles. The caller’s speed setting and the robot’s power budget can slow it further.${Number.isFinite(budget.per_servo_deg_s) ? ` Current per-servo limit: ${budget.per_servo_deg_s}°/s.` : ''}`;
    $('action-playback-help').textContent = draft?.playback === 'continuous' ? 'Continuous paths join sampled waypoints without an intentional stop at each one. Holds and speed changes divide the path. Used by walking and turning actions.' : 'Each keyframe is completed before the next begins. The speed and hold belong to the selected destination frame.';
  }
  function drawPose(angles) {
    const svg = $('action-pose-preview'); svg.replaceChildren();
    if (!finitePose(angles)) { previewAngles = null; return; }
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
      points.forEach(([r,z], i) => {
        const cx = x + r * .85, cy = y - z * .85;
        if (i === 3) { element('circle', {cx, cy, r: 4.5, class: 'preview-foot'}); return; }
        const index = leg * 3 + [2,1,0][i], label = `${LEGS[leg]} ${JOINTS[index % 3]}`;
        const node = element('g', {class: 'preview-joint-node', role: 'button', tabindex: '0', 'data-servo': index, 'aria-label': `Edit ${label} angle`, 'aria-pressed': String(selectedJoint === index)});
        for (const [radius, className] of [[13,'preview-joint-hit'],[6,'preview-joint']]) {
          const circle = document.createElementNS(svgNS,'circle'); circle.setAttribute('cx',cx); circle.setAttribute('cy',cy); circle.setAttribute('r',radius); circle.setAttribute('class',className); node.append(circle);
        }
        const tip = document.createElementNS(svgNS,'title'); tip.textContent = `${label}: ${angles[index].toFixed(1)}° · click to edit`; node.append(tip);
        node.addEventListener('click', () => selectJoint(index));
        node.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectJoint(index, true); } });
      });
      element('text', {x: x + 67, y: row + 177, 'text-anchor': 'middle', class: 'preview-angle-label'}, `Hip angle ${angles[leg * 3 + 2].toFixed(1)}°`);
    }
  }
  function syncJointEditor(skip = '') {
    const frame = draft?.frames?.[selectedFrame], index = selectedJoint;
    if (index === null || !frame) { $('selected-joint-name').textContent = 'Select a joint on the canvas'; $('selected-joint-detail').textContent = 'Outlined nodes are servo pivots. Solid feet are endpoints.'; $('selected-joint-angle').value = ''; return; }
    const value = frame.angles[index], [low, high] = BOUNDS[index % 3];
    $('selected-joint-name').textContent = `${LEGS[Math.floor(index / 3)]} · ${JOINTS[index % 3]}`;
    $('selected-joint-detail').textContent = `Editing frame ${selectedFrame + 1} · ${low} to ${high}° · save to keep your changes. This does not move the robot.`;
    for (const id of ['selected-joint-slider','selected-joint-angle']) { $(id).min = low; $(id).max = high; if (skip !== id) $(id).value = Number.isFinite(value) ? Number(value.toFixed(3)) : ''; }
  }
  function selectJoint(index, keyboard = false) {
    if (!draft || saving || playback?.pending) return;
    stopPreview(); selectedJoint = index; $('action-scrub').value = selectedFrame; scrub(selectedFrame); syncJointEditor(); controls();
    if (keyboard) root.querySelector(`.preview-joint-node[data-servo="${index}"]`)?.focus();
  }
  const seconds = value => Number.isFinite(value) ? `${value.toFixed(2)}s` : '—';
  function revealFrame(index) {
    const strip = root.querySelector('.filmstrip-scroll'), card = root.querySelector(`.keyframe-card[data-frame="${index}"]`);
    if (!strip?.clientWidth || !card) return;
    const left = card.getBoundingClientRect().left - strip.getBoundingClientRect().left + strip.scrollLeft;
    const right = left + card.getBoundingClientRect().width, margin = 8;
    if (left < strip.scrollLeft + margin) strip.scrollLeft = Math.max(0, left - margin);
    else if (right > strip.scrollLeft + strip.clientWidth - margin) strip.scrollLeft = right - strip.clientWidth + margin;
  }
  function followPlayhead(position) {
    if (!previewTimer) return;
    const strip = root.querySelector('.filmstrip-scroll'); if (!strip?.clientWidth) return;
    const margin = Math.min(40, strip.clientWidth / 4);
    if (position > strip.scrollLeft + strip.clientWidth - margin) strip.scrollLeft = position - strip.clientWidth * .65;
    else if (position < strip.scrollLeft + margin) strip.scrollLeft = Math.max(0, position - strip.clientWidth * .35);
  }
  function showTime(value) {
    if (!draft || !timing.valid) return;
    const sample = sampleSequence(draft.frames, timing, value); previewTime = sample.time;
    if (finitePose(sample.angles)) drawPose(sample.angles);
    $('action-time-scrub').value = sample.time; $('action-scrub').value = sample.position;
    $('action-preview-time').textContent = `${sample.time.toFixed(2)} / ${timing.duration.toFixed(2)} s`;
    $('action-preview-detail').textContent = `${sample.phase === 'hold' ? 'Hold at' : sample.phase === 'complete' ? 'End at' : 'Moving toward'} frame ${sample.frame + 1} · first pose starts at 0s; initial approach is unknown. Power limits can slow the robot. Frames are shown at equal spacing.`;
    $('action-canvas-state').textContent = Math.abs(sample.position - selectedFrame) < .0001 ? `EDITING FRAME ${selectedFrame + 1}` : `PREVIEW · FRAME ${(sample.position + 1).toFixed(2)}`;
    for (const card of root.querySelectorAll('.keyframe-card')) card.classList.toggle('is-previewing', Number(card.dataset.frame) === sample.frame);
    const first = $('action-timeline').firstElementChild, width = first?.getBoundingClientRect().width || 144;
    const cursor = sample.position * (width + 10) + width / 2;
    $('action-playhead').style.left = `${cursor}px`; followPlayhead(cursor);
  }
  function scrub(value) {
    if (!draft?.frames.length) return;
    const low = Math.min(draft.frames.length - 1, Math.floor(value)), high = Math.min(draft.frames.length - 1, low + 1), weight = value - low;
    if (weight > 0) { if (timing.valid) showTime(timing.frames[low].departure + timing.frames[high].transition * weight); return; }
    if (timing.valid) showTime(timing.frames[low].arrival);
    // Several zero-time frames may share an arrival. Explicit frame selection
    // always shows that frame, while timed playback advances instant boundaries.
    drawPose(draft.frames[low].angles); $('action-scrub').value = low;
    $('action-canvas-state').textContent = low === selectedFrame ? `EDITING FRAME ${low + 1}` : `PREVIEW · FRAME ${low + 1}`;
    const first = $('action-timeline').firstElementChild, width = first?.getBoundingClientRect().width || 144;
    $('action-playhead').style.left = `${low * (width + 10) + width / 2}px`;
    for (const card of root.querySelectorAll('.keyframe-card')) card.classList.toggle('is-previewing', Number(card.dataset.frame) === low);
    $('action-preview-detail').textContent = `Selected frame ${low + 1}${timing.valid ? ` at ${seconds(timing.frames[low].arrival)}` : ''}. Preview starts at the first saved pose; initial approach and power-budget delays are not included.`;
  }
  function selectFrame(index, focus = false) {
    if (!draft || index < 0 || index >= draft.frames.length || saving || playback?.pending) return;
    stopPreview(); selectedFrame = index; renderSelected();
    if (focus) root.querySelector(`[data-frame="${index}"]`)?.focus({preventScroll: true});
  }
  function moveFrame(from, to) {
    if (!draft || saving || playback?.pending || from === to || from < 0 || to < 0 || from >= draft.frames.length || to >= draft.frames.length) return;
    const [frame] = draft.frames.splice(from, 1); draft.frames.splice(to, 0, frame); selectedFrame = to; changed(); renderSelected();
    message(`Frame moved to position ${to + 1}. Save to keep this sequence.`);
  }
  function renderTimeline() {
    timing = sequenceTiming(draft?.frames || []);
    if (!timing.valid) { drawPose(null); $('action-preview-time').textContent = 'Check frame values'; }
    $('action-timeline').replaceChildren(); $('action-ruler').replaceChildren();
    for (const [index, frame] of (draft?.frames || []).entries()) {
      const item = document.createElement('div'); item.setAttribute('role', 'listitem');
      const button = document.createElement('button'); button.type = 'button'; button.className = 'keyframe-card'; button.dataset.frame = index; button.draggable = true;
      button.setAttribute('aria-label', `Edit keyframe ${index + 1}`); button.setAttribute('aria-pressed', String(index === selectedFrame));
      button.title = 'Click to edit. Drag to reorder, or use Alt + Left / Right.';
      const number = document.createElement('strong'); number.textContent = `FRAME ${index + 1}`;
      const rate = document.createElement('span'); rate.textContent = `${Number.isFinite(frame.speed_deg_s) ? frame.speed_deg_s : '—'}°/s`;
      const hold = document.createElement('small'); hold.textContent = `Hold ${seconds(frame.hold_seconds)}`;
      const duration = document.createElement('small'); duration.textContent = index ? `Transition ${seconds(timing.frames[index]?.transition)}` : 'First pose';
      const thumb = createPoseThumbnail(frame.angles); thumb.setAttribute('aria-hidden','true');
      button.append(number, thumb, rate, hold, duration);
      button.addEventListener('click', () => selectFrame(index));
      button.addEventListener('keydown', event => {
        if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
        event.preventDefault(); const target = event.key === 'Home' ? 0 : event.key === 'End' ? draft.frames.length - 1 : index + (event.key === 'ArrowLeft' ? -1 : 1);
        if (event.altKey) { moveFrame(index, target); root.querySelector(`[data-frame="${selectedFrame}"]`)?.focus({preventScroll: true}); } else selectFrame(target, true);
      });
      button.addEventListener('dragstart', event => { if (saving || playback?.pending) { event.preventDefault(); return; } dragFrame = index; event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', String(index)); button.classList.add('is-dragging'); });
      button.addEventListener('dragover', event => { if (dragFrame === null) return; event.preventDefault(); event.dataTransfer.dropEffect = 'move'; button.classList.add('is-drop-target'); });
      button.addEventListener('dragleave', () => button.classList.remove('is-drop-target'));
      button.addEventListener('drop', event => { event.preventDefault(); if (dragFrame === null) return; const from = dragFrame; dragFrame = null; moveFrame(from, index); });
      button.addEventListener('dragend', () => { dragFrame = null; root.querySelectorAll('.is-dragging,.is-drop-target').forEach(node => node.classList.remove('is-dragging','is-drop-target')); });
      item.append(button); $('action-timeline').append(item);
      const tick = document.createElement('span'); tick.textContent = seconds(timing.frames[index]?.arrival); $('action-ruler').append(tick);
    }
    $('action-frame-count').textContent = `${draft?.frames.length || 0} / ${maxFrames} frames · ${seconds(timing.duration)}`;
    $('action-time-scrub').max = timing.duration || 0;
    const frame = timing.frames[selectedFrame];
    $('frame-timing-detail').textContent = !frame ? '' : selectedFrame === 0 ? `First pose at 0s. Its approach time depends on the robot’s current pose. Hold ${seconds(frame.hold)}.` : `Arrival ${seconds(frame.arrival)} · transition ${seconds(frame.transition)} · hold ${seconds(frame.hold)}.`;
  }
  function renderSelected() {
    const frame = draft?.frames?.[selectedFrame];
    $('frame-editor-title').textContent = `Frame ${selectedFrame + 1} · servo targets`;
    $('frame-speed').value = frame?.speed_deg_s ?? 30; $('frame-hold').value = frame?.hold_seconds ?? 0;
    angleInputs.forEach((input, i) => { input.value = Number.isFinite(frame?.angles[i]) ? Number(frame.angles[i].toFixed(3)) : ''; input.title = Number.isFinite(frame?.angles[i]) ? `Stored command: ${frame.angles[i]}°` : '';  input.disabled = !frame; });
    $('action-scrub').max = Math.max(0, (draft?.frames.length || 1) - 1); $('action-scrub').value = selectedFrame;
    renderTimeline(); scrub(selectedFrame); syncJointEditor(); controls(); revealFrame(selectedFrame);
  }
  function changed(timeline = true) { stopPreview(); dirty = true; message(''); renderTimeline(); scrub(selectedFrame); controls(); }
  function setDraft(action, name = action?.name || null, isNew = false) {
    stopPreview(); transportMessage(''); selectedJoint = null; selectedName = name; baseline = action ? copy(action) : null; draft = action ? copy(action) : null; selectedFrame = 0; previewTime = 0; dirty = isNew;
    $('action-name').value = draft?.name || ''; $('action-name').readOnly = Boolean(name);
    $('action-description').value = draft?.description || ''; $('action-playback').value = draft?.playback || 'keyframes';
    $('action-editor-title').textContent = title(draft?.name || name) || 'New keyframe action';
    $('action-metadata-details').open = isNew;
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
    setDraft({name: fromSelected ? uniqueActionName(seed?.name || 'action', actions.map(a => a.name)) : '', description: fromSelected ? draft?.description || '' : '', frames, playback: fromSelected ? draft?.playback || 'keyframes' : 'keyframes', builtin: false, modified: false}, null, true);
    message(fromSelected ? `Duplicated all keyframes as “${draft.name}”. This is an unsaved copy; the original is unchanged.` : finitePose(robot.servo_angles) && !robot.busy ? 'New action starts from the current commanded servo positions.' : 'New action starts from the saved Stand keyframe, not a measured physical pose.');
    if (!fromSelected) $('action-name').focus();
  }
  async function publish(action) {
    const index = actions.findIndex(a => a.name === action.name); if (index < 0) actions.push(copy(action)); else actions[index] = copy(action);
    setDraft(action); await onChanged?.(copy(actions));
  }
  for (const id of ['selected-joint-slider','selected-joint-angle']) $(id).addEventListener('input', () => {
    if (!draft || selectedJoint === null || saving || playback?.pending) return;
    const value = $(id).value === '' ? NaN : Number($(id).value);
    draft.frames[selectedFrame].angles[selectedJoint] = value;
    angleInputs[selectedJoint].value = Number.isFinite(value) ? value : '';
    changed(false); syncJointEditor(id);
  });
  $('action-search').addEventListener('input', renderLibrary);
  $('action-new').addEventListener('click', () => freshAction());
  $('action-copy').addEventListener('click', () => freshAction(true));
  $('action-name').addEventListener('input', () => { if (draft) { draft.name = $('action-name').value.trim().toLowerCase(); $('action-editor-title').textContent = title(draft.name); changed(false); } });
  $('action-description').addEventListener('input', () => { if (draft) { draft.description = $('action-description').value; changed(false); } });
  $('action-playback').addEventListener('change', () => { if (draft) { draft.playback = $('action-playback').value; changed(false); } });
  for (const [id, field] of [['frame-speed', 'speed_deg_s'], ['frame-hold', 'hold_seconds']]) $(id).addEventListener('input', () => { if (draft) { draft.frames[selectedFrame][field] = $(id).value === '' ? NaN : Number($(id).value); changed(); } });
  for (const id of ['frame-add', 'frame-duplicate']) $(id).addEventListener('click', () => { if (!draft || draft.frames.length >= maxFrames) return; draft.frames.splice(selectedFrame + 1, 0, copy(draft.frames[selectedFrame])); selectedFrame++; changed(); renderSelected(); });
  for (const [id, delta] of [['frame-left', -1], ['frame-right', 1]]) $(id).addEventListener('click', () => moveFrame(selectedFrame, selectedFrame + delta));
  for (const [id, delta] of [['frame-previous', -1], ['frame-next', 1]]) $(id).addEventListener('click', () => selectFrame(selectedFrame + delta));
  $('frame-remove').addEventListener('click', () => { if (!draft || draft.frames.length <= 1) return; draft.frames.splice(selectedFrame, 1); selectedFrame = Math.min(selectedFrame, draft.frames.length - 1); changed(); renderSelected(); });
  $('frame-capture').addEventListener('click', () => { const robot = getStatus() || {}; if (draft && finitePose(robot.servo_angles) && !robot.busy) { draft.frames[selectedFrame].angles = [...robot.servo_angles]; changed(); renderSelected(); message('Current commanded angles copied into this frame. Save to keep them.'); } });
  $('action-scrub').addEventListener('input', () => { stopPreview(); scrub(Number($('action-scrub').value)); });
  $('action-time-scrub').addEventListener('input', () => { stopPreview(); showTime(Number($('action-time-scrub').value)); });
  $('action-rewind').addEventListener('click', () => selectFrame(0));
  $('action-loop').addEventListener('click', () => { previewLoop = !previewLoop; $('action-loop').setAttribute('aria-pressed',String(previewLoop)); });
  $('action-preview').addEventListener('click', () => {
    if (previewTimer) { stopPreview(); return; }
    if (!draft || !timing.valid || timing.duration <= 0) return;
    if (previewTime >= timing.duration) previewTime = 0;
    previewStarted = performance.now() - previewTime * 1000; $('action-preview').textContent = 'Ⅱ Pause';
    previewTimer = setInterval(() => {
      if (document.hidden || root.closest('[hidden]')) { stopPreview(); return; }
      const elapsed = (performance.now() - previewStarted) / 1000;
      if (elapsed >= timing.duration && previewLoop) { previewStarted = performance.now() - (elapsed % timing.duration) * 1000; showTime(elapsed % timing.duration); }
      else { showTime(Math.min(elapsed, timing.duration)); if (elapsed >= timing.duration) stopPreview(); }
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
  function transportMessage(text, kind = '') { $('action-transport-notice').textContent = text; $('action-transport-notice').className = `notice ${kind}`; $('action-transport-notice').hidden = !text; }
  $('action-run').addEventListener('click', async () => { controls(); if ($('action-run').disabled) return; stopPreview(); transportMessage(''); try { await playback.play('move', {name: selectedName, speed: 80, steps: 1}); transportMessage('Saved action started. Keep this controller connected.'); } catch (error) { transportMessage(error.message, 'error'); } finally { controls(); } });
  $('action-stop').addEventListener('click', async () => { if ($('action-stop').disabled) return; stopPreview(); transportMessage(''); try { await playback.stop(); transportMessage('Robot stopped and motion disarmed.'); } catch (error) { transportMessage(error.message, 'error'); } finally { controls(); } });
  $('action-add-to-plan').addEventListener('click', () => { if (selectedName && !dirty) onAddToPlan?.(selectedName); });
  $('action-reset').addEventListener('click', async () => { controls(); if ($('action-reset').disabled) return; saving = true; controls(); try { await publish(await call('action_reset', {name: selectedName, ...(getStatus()?.armed ? {disarm: true} : {})})); message('Built-in action restored.'); } catch (error) { message(error.message, 'error'); } finally { saving = false; controls(); } });
  $('action-delete').addEventListener('click', async () => { controls(); if ($('action-delete').disabled) return; saving = true; controls(); try { await call('action_delete', {name: selectedName}); actions = actions.filter(a => a.name !== selectedName); setDraft(actions.find(a => a.name === 'stand') || actions[0] || null); await onChanged?.(copy(actions)); message('Custom action deleted.'); } catch (error) { message(error.message, 'error'); } finally { saving = false; controls(); } });
  new ResizeObserver(entries => { const width = Math.round(entries[0].contentRect.width); if (width !== previewWidth) { previewWidth = width; if (width && previewAngles) drawPose(previewAngles); } }).observe($('action-pose-preview'));
  playback?.subscribe(controls);
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
