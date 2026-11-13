// SPDX-License-Identifier: MIT
const $ = id => document.getElementById(id);

export function initControllers({call}) {
  const section = $('admin-controllers');
  section.innerHTML = `
    <div class="admin-intro admin-section-intro"><div><h2>External controllers</h2><p>Check connected receivers and test their buttons and sticks.</p></div><button id="controller-refresh" class="outline">Refresh controllers</button></div>
    <article class="panel controller-monitor">
      <div class="panel-heading"><h2>USB gamepad</h2><span id="controller-status" class="tag">NOT CHECKED</span></div>
      <div class="panel-body">
        <p id="controller-note" class="caption">Connect a USB gamepad or its wireless receiver to the Pi.</p>
        <div class="controller-summary"><div><span>Input activity</span><strong id="controller-activity">Waiting for input</strong></div><div><span>Control mode</span><strong>Input monitor</strong></div></div>
        <div class="controller-input-grid"><div><h3>Sticks & axes</h3><div id="controller-axes"><p class="caption">No axes available.</p></div></div><div><h3>Buttons</h3><div id="controller-buttons" class="controller-buttons"><p class="caption">No buttons available.</p></div></div></div>
        <p class="controller-hint">Motion mapping is not configured. Controller inputs are displayed here and do not move the robot.</p>
        <p id="controller-error" class="notice error" role="status" hidden></p>
        <details class="controller-device-details"><summary>Receiver details</summary><div id="controller-devices" class="controller-devices"><p class="caption">No receiver checked yet.</p></div><p id="controller-driver-note" class="caption"></p></details>
      </div>
    </article>`;
  let active = false, loading = false, deviceSignature = '', inputSignature = '';
  const axes = [], buttons = [];
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  function render(data) {
    $('controller-status').textContent = data.connected ? 'RECEIVER CONNECTED' : 'NOT CONNECTED';
    $('controller-status').className = 'tag ' + (data.connected ? 'teal' : '');
    $('controller-note').textContent = data.connected ? 'USB input is available. Watch the controls below to verify that your sticks and buttons respond.' : 'Connect a USB gamepad or its wireless receiver to the Pi.';
    $('controller-driver-note').textContent = data.note || 'A detected USB receiver does not confirm that its wireless handset is paired.';
    const events = Number(data.events_seen) || 0;
    $('controller-activity').textContent = !data.connected ? 'No controller connected' : events ? `${events} input event${events === 1 ? '' : 's'} received` : 'Move a stick or press a button';
    const devices = data.devices || [];
    const nextDevices = JSON.stringify(devices);
    if (nextDevices !== deviceSignature) {
      deviceSignature = nextDevices;
      $('controller-devices').replaceChildren();
      for (const device of devices) {
        const row = node('div', 'controller-device');
        row.append(node('strong', '', device.name || 'USB gamepad'));
        row.append(node('p', 'caption', `USB ${device.vendor_id || '?'}:${device.product_id || '?'}`));
        row.append(node('code', '', device.stable_path || device.path || device.id));
        if (device.readable === false) row.append(node('p', 'caption', 'Input device cannot be read.'));
        $('controller-devices').append(row);
      }
      if (!devices.length) $('controller-devices').append(node('p', 'caption', 'No USB joystick input device detected.'));
    }
    const nextInputs = JSON.stringify([data.active_device_id, data.axis_names, data.button_names, data.axes?.length, data.buttons?.length]);
    if (nextInputs !== inputSignature) {
      inputSignature = nextInputs;
      axes.length = 0; buttons.length = 0;
      $('controller-axes').replaceChildren(); $('controller-buttons').replaceChildren();
      (data.axes || []).forEach((value, index) => {
        const row = node('label', 'controller-axis');
        const name = data.axis_names?.[index] || `Axis ${index + 1}`;
        const meter = node('meter'); meter.min = -1; meter.max = 1; meter.setAttribute('aria-label', name);
        const output = node('output'); row.append(node('span', '', name), meter, output);
        $('controller-axes').append(row); axes.push({meter, output});
      });
      (data.buttons || []).forEach((pressed, index) => {
        const button = node('span', 'tag', data.button_names?.[index] || `Button ${index + 1}`);
        $('controller-buttons').append(button); buttons.push(button);
      });
      if (!axes.length) $('controller-axes').append(node('p', 'caption', 'No axes available.'));
      if (!buttons.length) $('controller-buttons').append(node('p', 'caption', 'No buttons available.'));
    }
    (data.axes || []).forEach((value, index) => { axes[index].meter.value = value; axes[index].output.textContent = Number(value).toFixed(2); });
    (data.buttons || []).forEach((pressed, index) => {
      buttons[index].className = 'tag ' + (pressed ? 'teal' : '');
      buttons[index].setAttribute('aria-label', `${buttons[index].textContent}: ${pressed ? 'pressed' : 'released'}`);
    });
    $('controller-error').textContent = data.error || '';
    $('controller-error').hidden = !data.error;
  }
  async function refresh() {
    if (loading || !active || document.hidden) return;
    loading = true;
    try { render(await call('controller_get')); }
    catch (error) {
      $('controller-status').textContent = 'UNAVAILABLE'; $('controller-status').className = 'tag';
      $('controller-error').textContent = error.message; $('controller-error').hidden = false;
    } finally { loading = false; }
  }
  $('controller-refresh').addEventListener('click', refresh);
  setInterval(refresh, 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  return {setActive(value) {active = value; if (active) refresh();}, refresh};
}
