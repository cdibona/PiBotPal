// SPDX-License-Identifier: MIT
// The USB input connection is independent of permission to move the robot.
export function initControllerToggle({call, notice, onChanged}) {
  const button = document.getElementById('controller-toggle');
  let state = null, online = false, pending = false;
  function render() {
    const known = online && typeof state?.enabled === 'boolean';
    const attached = state?.attached ?? Boolean(state?.devices?.length);
    const active = known && state.enabled && state.connected;
    const label = !known ? 'USB controller unavailable — robot disconnected'
      : !attached ? 'No USB controller attached'
      : !state.enabled ? 'USB controller off — click to enable'
      : active ? 'USB controller on — click to disable'
      : state.error ? 'USB controller unavailable — click to disable'
      : 'USB controller connecting — click to disable';
    button.disabled = !known || !attached || pending;
    button.dataset.state = !known ? 'unavailable' : !attached ? 'absent' : !state.enabled ? 'off' : active ? 'on' : state.error ? 'error' : 'connecting';
    button.setAttribute('aria-pressed', String(Boolean(known && attached && state.enabled)));
    button.setAttribute('aria-busy', String(pending));
    button.setAttribute('aria-label', pending ? 'Updating USB controller connection' : label);
    const device = state?.devices?.find(device => device.id === state.active_device_id) || state?.devices?.[0];
    button.title = [label, device?.name, state?.error].filter(Boolean).join('\n');
  }
  button.addEventListener('click', async () => {
    if (button.disabled || pending) return;
    pending = true; render();
    try {
      const result = await call('controller_set_enabled', {enabled: !state.enabled});
      state = result;
      onChanged?.(result);
    } catch (error) { notice(error.message, 'error'); }
    finally { pending = false; render(); }
  });
  render();
  return {
    update(value, connected) {
      online = connected;
      // Do not overwrite the switch with a telemetry reply that began before
      // the user clicked; the next poll reconciles the shared server state.
      if (!pending) state = value;
      render();
    },
  };
}
