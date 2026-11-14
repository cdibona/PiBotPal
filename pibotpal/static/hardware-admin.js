// SPDX-License-Identifier: MIT
import {initControllers} from './controllers-ui.js';
import {initPowerAdmin} from './power-admin.js';
const $ = id => document.getElementById(id);
const optionLabel = key => key.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
const expansionDevices = new Set(['rear_camera', 'lidar', 'coral', 'imu']);

export function initHardware({call}) {
  const power = initPowerAdmin({call, root: $('admin-hardware')});
  const section = document.createElement('section');
  section.className = 'hardware-admin';
  section.setAttribute('aria-labelledby', 'hardware-title');
  section.innerHTML = `
    <div class="admin-intro admin-section-intro"><div><h2 id="hardware-title">Hardware inventory</h2><p>Review onboard devices and configure preferences for future additions.</p></div><button id="hardware-refresh" class="outline">Refresh hardware</button></div>
    <div id="hardware-notice" class="notice" role="status" aria-live="polite" hidden></div>
    <div class="hardware-host-card"><span class="eyebrow">THIS ROBOT</span><p id="hardware-host">Reading this robot…</p></div>
    <p class="hardware-inventory-note">These options record hardware preferences. Live camera settings are in <a href="#admin/cameras">Cameras</a>; gamepads and receivers are in <a href="#admin/controllers">Controllers</a>.</p>
    <div id="hardware-devices" class="hardware-grid"></div>
    <details class="hardware-connections"><summary>USB & serial connection details</summary><div id="hardware-discovered" class="hardware-discovered"></div></details>`;
  $('admin-hardware').append(section);
  const controllers = initControllers({call});
  let loading = false;
  function message(text, kind = '') {
    $('hardware-notice').textContent = text;
    $('hardware-notice').className = `notice ${kind}`;
    $('hardware-notice').hidden = !text;
  }
  function field(key, value) {
    const label = document.createElement('label');
    label.textContent = key === 'enabled' ? 'Enabled preference' : optionLabel(key);
    let input;
    if (typeof value === 'boolean') {
      label.className = 'check-label'; input = document.createElement('input');
      input.type = 'checkbox'; input.checked = value; label.prepend(input);
    } else if (value !== null && typeof value === 'object') {
      input = document.createElement('textarea'); input.value = JSON.stringify(value, null, 2); input.rows = 3; label.append(input);
    } else {
      input = document.createElement('input'); input.type = typeof value === 'number' ? 'number' : 'text';
      if (input.type === 'number') input.step = 'any'; input.value = value ?? ''; label.append(input);
    }
    input.dataset.option = key; input.dataset.kind = value === null ? 'nullable' : typeof value;
    return label;
  }
  function render(data) {
    const host = data.host || {};
    $('hardware-host').textContent = [host.model, Number.isFinite(host.memory_total_mb) ? `${Math.round(host.memory_total_mb)} MB RAM` : null, Number.isFinite(host.memory_available_mb) ? `${Math.round(host.memory_available_mb)} MB available` : null].filter(Boolean).join(' · ') || 'Hardware configuration is stored on this robot.';
    $('hardware-discovered').replaceChildren();
    const discovered = data.discovered || {};
    for (const [key, title] of [['serial_ports', 'Serial ports'], ['usb_cameras', 'USB cameras'], ['coral_usb_ids', 'Coral USB devices']]) {
      const row = document.createElement('div'), label = document.createElement('strong'), detail = document.createElement('span');
      label.textContent = title;
      const items = discovered[key] || [];
      detail.textContent = items.length ? items.map(item => typeof item === 'string' ? item : JSON.stringify(item)).join(' · ') : 'None detected';
      row.append(label, detail); $('hardware-discovered').append(row);
    }
    const expanded = new Set([...$('hardware-devices').querySelectorAll('details[open]')].map(card => card.dataset.deviceId));
    $('hardware-devices').replaceChildren();
    const groups = new Map();
    for (const [key, title, description] of [
      ['current', 'Current hardware', 'Open a device to review its driver status and saved preferences.'],
      ['expansion', 'Expansion preferences', 'Prepare mounting and connection options. Drivers for these additions are not active yet.'],
    ]) {
      const group = document.createElement('section'); group.className = 'hardware-group';
      const heading = document.createElement('h3'); heading.textContent = title;
      const note = document.createElement('p'); note.className = 'caption'; note.textContent = description;
      const grid = document.createElement('div'); grid.className = 'hardware-group-grid';
      group.append(heading, note, grid); $('hardware-devices').append(group); groups.set(key, {group, grid});
    }
    for (const device of data.devices || []) {
      if (device.id === 'gamepad' || device.kind === 'input') continue;
      const card = document.createElement('details'); card.className = 'panel hardware-device'; card.dataset.deviceId = device.id;
      card.open = expanded.has(device.id);
      const header = document.createElement('summary'); header.className = 'panel-heading';
      const title = document.createElement('h3'); title.textContent = device.name || optionLabel(device.id);
      const badge = document.createElement('span'); badge.className = 'tag';
      badge.textContent = device.detected === true ? 'DETECTED' : 'NOT VERIFIED';
      if (device.detected === true) badge.classList.add('teal'); header.append(title, badge); card.append(header);
      const form = document.createElement('form'); form.className = 'admin-form';
      const status = document.createElement('p'); status.className = 'hardware-driver-status';
      status.textContent = typeof device.status === 'object' ? JSON.stringify(device.status) : device.status || device.kind || ''; form.append(status);
      if (device.notes) {
        const notes = document.createElement('p'); notes.className = 'caption';
        notes.textContent = Array.isArray(device.notes) ? device.notes.join(' ') : device.notes; form.append(notes);
      }
      const fields = document.createElement('div'); fields.className = 'form-grid';
      for (const [key, value] of Object.entries(device.options || {})) fields.append(field(key, value)); form.append(fields);
      if (Object.keys(device.options || {}).length) {
        const save = document.createElement('button'); save.type = 'submit'; save.textContent = 'Save hardware options'; form.append(save);
        form.addEventListener('submit', async event => {
          event.preventDefault(); save.disabled = true;
          try {
            const options = {};
            for (const input of fields.querySelectorAll('[data-option]')) {
              const kind = input.dataset.kind;
              options[input.dataset.option] = kind === 'boolean' ? input.checked : kind === 'number' ? Number(input.value) : kind === 'object' ? JSON.parse(input.value) : kind === 'nullable' ? (input.value || null) : input.value;
            }
            await call('hardware_save', {device_id: device.id, options}); await refresh();
            message(`${device.name || device.id} preferences saved. Saving preferences does not activate hardware.`);
          } catch (error) { message(error.message, 'error'); }
          finally { save.disabled = false; }
        });
      }
      card.append(form); groups.get(expansionDevices.has(device.id) ? 'expansion' : 'current').grid.append(card);
    }
    for (const {group, grid} of groups.values()) group.hidden = !grid.childElementCount;
  }
  async function refresh() {
    if (loading) return; loading = true;
    try { await Promise.all([call('hardware_get').then(render), power.refresh()]); }
    catch (error) { message(error.message, 'error'); }
    finally { loading = false; }
  }
  $('hardware-refresh').addEventListener('click', refresh);
  return {
    refresh,
    refreshControllers: controllers.refresh,
    setActive(name) { controllers.setActive(name === 'controllers'); power.setActive(name === 'hardware'); if (name === 'hardware') refresh(); },
  };
}
