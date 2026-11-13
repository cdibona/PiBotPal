// SPDX-License-Identifier: MIT
const $ = id => document.getElementById(id);
const label = camera => camera.label || camera.name || camera.model || camera.id;
const sizeKey = size => `${size.width}x${size.height}`;
const sizeLabel = size => `${size.width} × ${size.height}`;

export function initCameraViews({call, notice, CameraFeed, getStatus}) {
  const dashboard = new Map(), previews = new Map(), drafts = new Map();
  let cameras = [], primary = null, signature = '', active = false, online = true, loading = false;
  const root = $('admin-cameras');
  root.innerHTML = `<div class="admin-intro admin-section-intro"><div><h2>Camera streams</h2><p>Choose a resolution for each camera and select the primary view for your apps.</p></div><button id="cameras-refresh" class="outline">Refresh cameras</button></div><p class="caption camera-stream-note">Resolution changes apply to this robot’s live feed and snapshots. Each camera restarts independently; larger frames use more bandwidth.</p><div id="cameras-notice" class="notice" role="status" hidden></div><div id="camera-admin-list" class="camera-admin-grid"></div>`;

  function message(text, type = '') {
    $('cameras-notice').textContent = text;
    $('cameras-notice').className = 'notice ' + type;
    $('cameras-notice').hidden = !text;
  }
  function currentCamera(id) { return cameras.find(camera => camera.id === id); }
  function modeDraft(camera) {
    if (!drafts.has(camera.id)) drafts.set(camera.id, {value: sizeKey(camera), dirty: false, applying: false, message: '', error: false});
    return drafts.get(camera.id);
  }
  function modes(camera) {
    return camera.resolutions?.length ? camera.resolutions : [{width: camera.width || 640, height: camera.height || 480, frame_rate: camera.frame_rate}];
  }
  function append(tag, parent, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    parent.append(element);
    return element;
  }
  async function configure(id) {
    const camera = currentCamera(id), draft = modeDraft(camera);
    const selected = modes(camera).find(mode => sizeKey(mode) === draft.value);
    if (!selected || draft.applying || !draft.dirty) return;
    draft.applying = true;
    draft.error = false;
    draft.message = `Applying ${sizeLabel(selected)}…`;
    sync();
    try {
      const result = await call('camera_configure', {camera_id: id, width: selected.width, height: selected.height}, {timeout: 35000});
      update(result);
      const configured = currentCamera(id);
      draft.value = sizeKey(configured);
      draft.dirty = false;
      draft.message = `${sizeLabel(configured)} saved.${configured.enabled ? ' The live feed is restarting.' : ' Capture is off.'}`;
    } catch (error) {
      draft.error = true;
      draft.message = error.message;
    } finally {
      draft.applying = false;
      sync();
    }
  }
  function createCard(camera, container, isAdmin) {
    const card = append('article', container, 'panel csi-camera-card');
    card.dataset.cameraId = camera.id;
    const heading = append('div', card, 'panel-heading');
    const name = append('div', heading, 'camera-card-title');
    append('h3', name, '', label(camera));
    const role = append('span', name, 'caption');
    const badge = append('span', heading, 'tag');
    const stage = append('div', card, 'csi-camera-stage');
    const image = append('img', stage);
    image.alt = `Live view from ${label(camera)}`;
    image.hidden = true;
    const empty = append('p', stage, 'caption');
    const body = append('div', card, 'panel-body');
    const detail = append('p', body, 'camera-stream-detail');
    let resolution, apply, resolutionHint, resolutionStatus;
    if (isAdmin) {
      const form = append('form', body, 'camera-resolution-form');
      const field = append('label', form, 'camera-resolution-field');
      append('span', field, '', 'Stream resolution');
      resolution = append('select', field, 'camera-resolution-select');
      resolution.setAttribute('aria-label', `${label(camera)} stream resolution`);
      apply = append('button', form, 'outline', 'Apply');
      apply.type = 'submit';
      resolutionHint = append('p', form, 'caption camera-resolution-hint');
      resolutionStatus = append('p', form, 'camera-resolution-status');
      resolutionStatus.setAttribute('role', 'status');
      resolutionStatus.setAttribute('aria-live', 'polite');
      resolutionStatus.hidden = true;
      resolution.addEventListener('change', () => {
        const current = currentCamera(camera.id), draft = modeDraft(current);
        draft.value = resolution.value;
        draft.dirty = draft.value !== sizeKey(current);
        draft.message = '';
        sync();
      });
      form.addEventListener('submit', event => { event.preventDefault(); configure(camera.id); });
    }
    const actions = append('div', body, 'admin-actions camera-actions');
    const toggle = append('button', actions);
    toggle.type = 'button';
    toggle.addEventListener('click', async () => {
      toggle.disabled = true;
      try {
        const current = currentCamera(camera.id);
        await call('camera', {camera_id: camera.id, enabled: !current?.enabled});
        await refresh();
      } catch (error) { message(error.message, 'error'); }
      finally { toggle.disabled = false; }
    });
    const primaryButton = append('button', actions, '', 'Use as primary');
    primaryButton.type = 'button';
    primaryButton.addEventListener('click', () => choose(camera.id));
    const snapshot = append('a', actions, 'text-link', 'Save snapshot ↗');
    snapshot.href = camera.snapshot_url;
    snapshot.download = `${camera.id}.jpg`;
    let values;
    if (isAdmin) {
      const diagnostics = append('details', body, 'capture-diagnostics');
      append('summary', diagnostics, '', 'Sensor details & diagnostics');
      append('p', diagnostics, 'caption camera-hardware-id', camera.hardware_id || camera.id);
      values = append('pre', diagnostics);
    }
    const feed = new CameraFeed(image, state => {
      const live = state === 'live';
      image.hidden = !live;
      empty.hidden = live;
      if (state !== 'off') badge.textContent = live ? 'LIVE' : 'CONNECTING';
      empty.textContent = state === 'off' ? 'Camera capture is off' : 'Waiting for camera frames…';
      const draft = drafts.get(camera.id);
      if (live && draft && !draft.error && !draft.applying && draft.message.endsWith('The live feed is restarting.')) {
        draft.message = draft.message.replace('The live feed is restarting.', 'Live feed ready.');
        if (resolutionStatus) resolutionStatus.textContent = draft.message;
      }
    }, camera.video_url);
    image.addEventListener('error', () => { if (feed.enabled) feed.controller?.abort(); });
    return {camera, card, feed, badge, toggle, primaryButton, snapshot, detail, values, image, empty, stage, role, isAdmin, resolution, apply, resolutionHint, resolutionStatus};
  }
  async function choose(id) {
    try {
      update(await call('camera_select', {camera_id: id}));
      notice('Primary camera changed. Your apps now use this camera.');
    } catch (error) { message(error.message, 'error'); }
  }
  function rebuild() {
    for (const map of [dashboard, previews]) {
      for (const view of map.values()) view.feed.setEnabled(false);
      map.clear();
    }
    $('other-camera-feeds').replaceChildren();
    $('camera-admin-list').replaceChildren();
    for (const camera of cameras) {
      if (camera.id !== primary) dashboard.set(camera.id, createCard(camera, $('other-camera-feeds'), false));
      previews.set(camera.id, createCard(camera, $('camera-admin-list'), true));
    }
    $('other-camera-feeds').hidden = !dashboard.size;
    if (!cameras.length) append('p', $('camera-admin-list'), 'caption', 'No CSI camera sensors detected. Check the camera connections and refresh.');
    const select = $('primary-camera');
    select.replaceChildren();
    for (const camera of cameras) {
      const option = append('option', select, '', `${label(camera)}${camera.id === primary ? ' · primary' : ''}`);
      option.value = camera.id;
    }
    select.value = primary || '';
    select.disabled = !cameras.length;
    $('primary-camera-control').hidden = !cameras.length;
  }
  function syncResolution(view, camera, draft) {
    const available = modes(camera), next = JSON.stringify(available);
    if (view.resolution.dataset.modes !== next) {
      view.resolution.replaceChildren();
      for (const mode of available) {
        const native = camera.sensor_resolution && sizeKey(mode) === sizeKey(camera.sensor_resolution);
        const option = append('option', view.resolution, '', `${sizeLabel(mode)}${native ? ' · full sensor' : ''}`);
        option.value = sizeKey(mode);
      }
      view.resolution.dataset.modes = next;
    }
    if (!draft.dirty && !draft.applying) draft.value = sizeKey(camera);
    if (!available.some(mode => sizeKey(mode) === draft.value)) { draft.value = sizeKey(camera); draft.dirty = false; }
    view.resolution.value = draft.value;
    view.resolution.disabled = draft.applying || !online || !camera.resolutions?.length;
    view.apply.disabled = draft.applying || !draft.dirty || !online;
    view.apply.textContent = draft.applying ? 'Applying…' : 'Apply';
    const selected = available.find(mode => sizeKey(mode) === draft.value);
    const hint = [];
    if (selected?.frame_rate) hint.push(`Target ${selected.frame_rate} fps`);
    if (selected?.width * selected?.height > 1920 * 1080) hint.push('High resolution · slower preview');
    if (camera.sensor_resolution) hint.push(`Sensor maximum ${sizeLabel(camera.sensor_resolution)}`);
    view.resolutionHint.textContent = hint.join(' · ') || 'Resolution options are reported by the camera driver.';
    view.resolutionStatus.textContent = draft.message;
    view.resolutionStatus.hidden = !draft.message;
    view.resolutionStatus.classList.toggle('error', draft.error);
  }
  function sync() {
    const instance = getStatus()?.server_instance_id;
    for (const [map, visible] of [[dashboard, !$('dashboard').hidden], [previews, active]]) {
      for (const [id, view] of map) {
        const camera = currentCamera(id);
        if (!camera) continue;
        const draft = modeDraft(camera);
        view.toggle.textContent = camera.enabled ? 'Stop capture' : 'Start capture';
        view.toggle.disabled = draft.applying || !online;
        view.primaryButton.hidden = id === primary;
        view.primaryButton.disabled = draft.applying || !online;
        view.role.textContent = id === primary ? 'Primary · used by apps' : 'Additional camera';
        view.detail.textContent = camera.error || `${sizeLabel({width: camera.width || 640, height: camera.height || 480})}${camera.available ? ' · live capture' : camera.timestamp ? ' · last capture' : ' · configured'}${camera.frame_rate ? ` · target ${camera.frame_rate} fps` : ''}`;
        if (camera.available && Number.isFinite(camera.brightness) && camera.brightness < 5) view.detail.textContent += ' · Very dark image: check lens and lighting';
        if (view.values) view.values.textContent = JSON.stringify({brightness: camera.brightness ?? null, sensor_rotation: camera.sensor_rotation ?? null, ...camera.capture_metadata}, null, 2);
        view.stage.style.aspectRatio = `${camera.width || 640} / ${camera.height || 480}`;
        view.snapshot.setAttribute('aria-disabled', String(!camera.available || draft.applying));
        view.snapshot.style.pointerEvents = camera.available && !draft.applying ? 'auto' : 'none';
        view.snapshot.tabIndex = camera.available && !draft.applying ? 0 : -1;
        const captureSize = sizeKey(camera);
        if (view.captureSize && view.captureSize !== captureSize) view.feed.setEnabled(false);
        view.captureSize = captureSize;
        view.feed.setEnabled(Boolean(camera.enabled && visible && online && !draft.applying), instance, camera.video_url);
        if (draft.applying) { view.badge.textContent = 'APPLYING'; view.empty.textContent = 'Applying stream resolution…'; }
        else if (!camera.enabled) { view.badge.textContent = camera.error ? 'ERROR' : 'OFF'; view.empty.textContent = camera.error || 'Camera capture is off'; }
        if (view.isAdmin) syncResolution(view, camera, draft);
      }
    }
    $('primary-camera').value = primary || '';
  }
  function update(state) {
    if (Array.isArray(state.cameras)) {
      cameras = state.cameras;
      primary = state.primary_camera_id || state.camera?.id || cameras[0]?.id || null;
      const next = cameras.map(camera => [camera.id, camera.label, camera.model, camera.hardware_id, camera.video_url, camera.snapshot_url].join('|')).join(';') + '#' + primary;
      if (signature !== next) { signature = next; rebuild(); }
    }
    online = true;
    sync();
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    try { update(await call('camera_list')); message(''); }
    catch (error) { message(error.message, 'error'); }
    finally { loading = false; }
  }
  $('cameras-refresh').addEventListener('click', refresh);
  $('primary-camera').addEventListener('change', event => choose(event.target.value));
  return {update, refresh, setActive(value) { active = value; sync(); if (active) refresh(); }, disconnect() { online = false; sync(); }};
}
