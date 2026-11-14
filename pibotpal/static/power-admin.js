// SPDX-License-Identifier: MIT
const limits = {
  max_active_servos: {min: 1, max: 6, integer: true},
  per_servo_deg_s: {min: 5, max: 60},
  total_deg_s: {min: 5, max: 180},
  settle_ms: {min: 40, max: 500, integer: true},
};
const settingNames = [...Object.keys(limits), 'body_sequence'];
const fieldLabels = {max_active_servos: 'Servos commanded together', per_servo_deg_s: 'Speed per servo', total_deg_s: 'Combined servo speed', settle_ms: 'Settle interval'};
const frequency = value => Number.isFinite(value) ? `${Math.round(value)} MHz` : 'Unavailable';

export function initPowerAdmin({call, root}) {
  const section = document.createElement('section');
  section.className = 'panel hardware-power-card';
  section.setAttribute('aria-labelledby', 'hardware-power-title');
  section.innerHTML = `
    <div class="panel-heading"><div><h2 id="hardware-power-title">Power & motion budget</h2><p>Monitor the Pi’s power state and limit how quickly servo commands change.</p></div><button id="hardware-power-refresh" type="button" class="outline">Refresh power</button></div>
    <div class="hardware-power-telemetry">
      <div class="power-host-summary"><div class="power-host-title"><h3>Raspberry Pi</h3><span id="hardware-power-state" class="tag">CONNECTING</span></div>
        <dl class="power-host-values"><div><dt>CPU frequency</dt><dd id="power-cpu-current">Unavailable</dd></div><div><dt>CPU cap</dt><dd id="power-cpu-max">Unavailable</dd></div><div><dt>CPU minimum</dt><dd id="power-cpu-min">Unavailable</dd></div><div><dt>Governor</dt><dd id="power-cpu-governor">Unavailable</dd></div><div><dt>Temperature</dt><dd id="power-cpu-temperature">Unavailable</dd></div></dl>
        <p class="power-caption">CPU values are read from the Pi. This panel does not change CPU settings.</p>
      </div>
      <div class="power-health-summary"><h3>Supply & throttling</h3><div class="power-health-table" role="table" aria-label="Pi power flags"><div class="power-health-head" role="row"><span role="columnheader">Condition</span><span role="columnheader">Now</span><span role="columnheader">Since boot</span></div><div role="row"><span role="rowheader">Undervoltage</span><strong id="power-undervoltage-now" role="cell">Unknown</strong><strong id="power-undervoltage-history" role="cell">Unknown</strong></div><div role="row"><span role="rowheader">Throttling</span><strong id="power-throttled-now" role="cell">Unknown</strong><strong id="power-throttled-history" role="cell">Unknown</strong></div></div>
        <p id="power-host-error" class="power-host-error" role="status" hidden></p><p id="power-last-stop" class="power-host-error" hidden></p><p id="power-host-updated" class="power-caption">Waiting for host telemetry.</p>
      </div>
    </div>
    <form id="servo-power-form" class="servo-power-form">
      <div class="servo-power-title"><div><h3>Servo command limits</h3><p>Limits apply to commanded movement, not measured electrical current.</p></div><span id="servo-power-edit-state" class="tag">LOADING</span></div>
      <p id="servo-power-error" class="power-host-error" role="status" hidden></p>
      <div class="servo-power-fields">
        <label for="servo-power-count">Servos commanded together<div class="power-input-unit"><input id="servo-power-count" name="max_active_servos" type="number" min="1" max="6" step="1" required value="3"><span>servos</span></div><small>Maximum changing at the same time · 1–6</small></label>
        <label for="servo-power-per-servo">Speed per servo<div class="power-input-unit"><input id="servo-power-per-servo" name="per_servo_deg_s" type="number" min="5" max="60" step="any" required value="30"><span>°/s</span></div><small>Maximum angle change per servo · 5–60</small></label>
        <label for="servo-power-total">Combined servo speed<div class="power-input-unit"><input id="servo-power-total" name="total_deg_s" type="number" min="5" max="180" step="any" required value="60"><span>°/s</span></div><small>Sum across moving servos · 5–180</small></label>
        <label for="servo-power-settle">Settle interval<div class="power-input-unit"><input id="servo-power-settle" name="settle_ms" type="number" min="40" max="500" step="1" required value="80"><span>ms</span></div><small>Pause between command groups · 40–500</small></label>
      </div>
      <div class="servo-power-sequence"><label for="servo-power-sequence">Whole-body movement order<select id="servo-power-sequence" name="body_sequence"><option value="joints">Matching joints across all legs</option><option value="legs">One leg at a time</option></select></label><p id="servo-power-sequence-hint" class="caption"></p></div>
      <div id="hardware-power-notice" class="notice" role="status" aria-live="polite" hidden></div>
      <div class="servo-power-actions"><button id="servo-power-save" type="submit" class="primary" disabled>Save servo limits</button><button id="servo-power-reset" type="button" disabled>Discard changes</button><p id="servo-power-save-hint">Waiting for robot state before changes can be saved.</p></div>
      <p class="servo-power-note">Servos holding their position still draw power. These limits spread out movement commands; they do not switch servo power off or measure current.</p>
    </form>`;
  root.append(section);
  const $ = id => section.querySelector(`#${id}`);
  const form = $('servo-power-form');
  const fields = new Map([...form.querySelectorAll('input[name], select[name]')].map(input => [input.name, input]));
  let active = false, loading = false, saving = false, dirty = false;
  let state = null, saved = null, timer = null, generation = 0;
  const visible = () => active && !document.hidden;
  function message(text, kind = '') {
    $('hardware-power-notice').textContent = text;
    $('hardware-power-notice').className = `notice ${kind}`;
    $('hardware-power-notice').hidden = !text;
  }
  function values() {
    return Object.fromEntries([...fields].map(([key, input]) => [key, key === 'body_sequence' ? input.value : Number(input.value)]));
  }
  function validationError() {
    for (const [key, input] of fields) {
      if (key === 'body_sequence') {
        if (!['joints', 'legs'].includes(input.value)) return 'Choose a whole-body movement order.';
        continue;
      }
      const value = Number(input.value), range = limits[key];
      if (input.value.trim() === '' || !Number.isFinite(value) || value < range.min || value > range.max || (range.integer && !Number.isInteger(value)))
        return `${fieldLabels[key]} must be ${range.integer ? 'a whole number ' : ''}from ${range.min} to ${range.max}.`;
    }
    return '';
  }
  function sameValues() {
    const current = values();
    return saved && settingNames.every(key => current[key] === saved[key]);
  }
  function controls() {
    const known = state && typeof state.armed === 'boolean' && typeof state.busy === 'boolean';
    const writable = known && !state.busy;
    const needsRepair = Boolean(state?.servos?.error);
    const invalid = validationError();
    $('servo-power-save').disabled = saving || (!dirty && !needsRepair) || !writable || Boolean(invalid);
    $('servo-power-save').textContent = saving ? 'Saving…' : state?.armed ? 'Disarm and save' : 'Save servo limits';
    $('servo-power-reset').disabled = saving || !dirty || !saved;
    $('servo-power-edit-state').textContent = saving ? 'SAVING' : !saved ? 'LOADING' : dirty ? 'UNSAVED CHANGES' : needsRepair ? 'NEEDS ATTENTION' : 'SAVED';
    $('servo-power-edit-state').className = `tag ${dirty || needsRepair ? 'orange' : ''}`;
    $('servo-power-save-hint').textContent = !known ? 'Robot state is unavailable. Saving is disabled.' : state.busy ? 'Stop the current movement or wait for it to finish before saving.' : invalid || (!dirty && !needsRepair ? 'Saved. Change a setting to enable saving.' : state.armed ? 'Saving will disarm system motion and controller actions, then apply your changes.' : needsRepair ? 'Save these valid values to replace the invalid stored limits.' : 'Ready to save. The robot is disarmed and idle.');
    const current = values(), count = current.max_active_servos;
    const groups = Number.isInteger(count) && count > 0 && count < 4
      ? ` Each group of four is split into batches of ${count === 3 ? '3 + 1' : count === 2 ? '2 + 2' : '1 + 1 + 1 + 1'} by your servo limit. Set the limit to 4 to move all four matching joints together.`
      : ' With a servo limit of at least 4, all four matching joints move together.';
    $('servo-power-sequence-hint').textContent = current.body_sequence === 'joints'
      ? `End joints (knees) → shoulder joints → angle joints (hips).${groups} Walking keeps its single-leg swing order.`
      : 'Complete one leg’s movement before the next. Your concurrency and speed limits still apply.';
  }
  function markFlag(id, value, historical = false) {
    const element = $(id);
    element.textContent = value === true ? historical ? 'Recorded' : 'Detected' : value === false ? historical ? 'None recorded' : 'Clear' : 'Unknown';
    element.dataset.flag = value === true ? historical ? 'history' : 'active' : value === false ? 'clear' : 'unknown';
  }
  function render(next, applySettings = false) {
    state = next;
    const host = next.host || {}, cpu = host.cpu || {};
    $('power-cpu-current').textContent = frequency(cpu.current_mhz);
    $('power-cpu-max').textContent = frequency(cpu.max_mhz);
    $('power-cpu-min').textContent = frequency(cpu.min_mhz);
    $('power-cpu-governor').textContent = typeof cpu.governor === 'string' && cpu.governor ? cpu.governor : 'Unavailable';
    $('power-cpu-temperature').textContent = Number.isFinite(host.temperature_c) ? `${host.temperature_c.toFixed(1)} °C` : 'Unavailable';
    markFlag('power-undervoltage-now', host.under_voltage);
    markFlag('power-undervoltage-history', host.under_voltage_occurred, true);
    markFlag('power-throttled-now', host.throttled);
    markFlag('power-throttled-history', host.throttled_occurred, true);
    const currentProblem = host.under_voltage === true || host.throttled === true;
    const history = host.under_voltage_occurred === true || host.throttled_occurred === true;
    const complete = host.available === true && !host.error && typeof host.under_voltage === 'boolean' && typeof host.throttled === 'boolean';
    $('hardware-power-state').textContent = currentProblem ? 'POWER WARNING' : !complete ? 'PARTIAL TELEMETRY' : history ? 'PAST EVENT RECORDED' : 'LIVE';
    $('hardware-power-state').className = `tag ${currentProblem ? 'red' : history ? 'orange' : complete ? 'teal' : ''}`;
    section.classList.toggle('has-power-warning', currentProblem);
    $('power-host-error').textContent = host.error || '';
    $('power-host-error').hidden = !host.error;
    const stopped = next.last_motion_stop;
    $('power-last-stop').textContent = stopped?.reason ? `Last automatic motion stop: ${stopped.reason}` : '';
    $('power-last-stop').hidden = !stopped?.reason;
    const timestamp = Number(host.timestamp);
    const timestampValid = Number.isFinite(timestamp) && timestamp > 0;
    $('power-host-updated').textContent = timestampValid ? `Host telemetry ${new Date(timestamp * 1000).toLocaleTimeString()}. History flags remain set until the Pi restarts.` : 'History flags remain set until the Pi restarts.';
    const settings = next.servos;
    $('servo-power-error').textContent = settings?.error ? `Saved servo limits need attention: ${settings.error}. Save valid limits before arming.` : '';
    $('servo-power-error').hidden = !settings?.error;
    if (settings && Object.keys(limits).every(key => Number.isFinite(settings[key]))) {
      saved = {...Object.fromEntries(Object.keys(limits).map(key => [key, settings[key]])), body_sequence: settings.body_sequence || 'joints'};
      if (applySettings || !dirty) {
        for (const [key, input] of fields) input.value = saved[key];
        dirty = false;
      } else if (sameValues()) dirty = false;
    }
    controls();
  }
  function unavailable(error) {
    state = null;
    for (const id of ['power-cpu-current', 'power-cpu-max', 'power-cpu-min', 'power-cpu-governor', 'power-cpu-temperature']) $(id).textContent = 'Unavailable';
    for (const id of ['power-undervoltage-now', 'power-undervoltage-history', 'power-throttled-now', 'power-throttled-history']) markFlag(id, null);
    $('hardware-power-state').textContent = 'UNAVAILABLE';
    $('hardware-power-state').className = 'tag';
    section.classList.remove('has-power-warning');
    $('power-host-error').textContent = error?.message || 'Power telemetry is unavailable.';
    $('power-host-error').hidden = false;
    $('power-host-updated').textContent = 'Waiting for a fresh reading. Unsaved edits are preserved.';
    controls();
  }
  async function refresh() {
    if (loading || saving || !visible()) return;
    loading = true;
    const requested = generation;
    try {
      const next = await call('power_get', {}, {timeout: 5000});
      if (visible() && requested === generation) render(next);
    } catch (error) { if (visible() && requested === generation) unavailable(error); }
    finally { loading = false; }
  }
  function schedule() {
    clearTimeout(timer);
    if (!visible()) return;
    timer = setTimeout(async () => { await refresh(); schedule(); }, 2000);
  }
  function changed() { dirty = !sameValues(); controls(); }
  form.addEventListener('input', changed);
  form.addEventListener('change', changed);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    controls();
    if ($('servo-power-save').disabled) return;
    const submitted = values();
    const disarm = state.armed;
    saving = true; controls(); message('');
    // Discard any older read already in flight; it cannot replace this save result.
    generation++;
    try {
      const next = await call('servo_power_set', {...submitted, ...(disarm ? {disarm: true} : {})});
      const editedDuringSave = settingNames.some(key => values()[key] !== submitted[key]);
      render(next, !editedDuringSave);
      message(editedDuringSave ? 'Servo limits saved. Your newer edits have not been saved.' : 'Servo command limits saved.');
    } catch (error) {
      message(error.message, 'error');
      // A failed save may be caused by newly armed hardware; require a fresh state.
      state = null;
    } finally { saving = false; controls(); refresh(); }
  });
  $('servo-power-reset').addEventListener('click', () => {
    if (!saved || saving) return;
    for (const [key, input] of fields) input.value = saved[key];
    dirty = false; controls(); message('Unsaved changes discarded.');
  });
  $('hardware-power-refresh').addEventListener('click', refresh);
  document.addEventListener('visibilitychange', () => {
    generation++;
    clearTimeout(timer);
    if (visible()) { state = null; controls(); refresh(); schedule(); }
  });
  controls();
  return {
    refresh,
    setActive(value) {
      if (active === value) { if (visible()) { refresh(); schedule(); } return; }
      active = value; generation++; clearTimeout(timer); state = null; controls();
      if (visible()) { refresh(); schedule(); }
    },
  };
}
