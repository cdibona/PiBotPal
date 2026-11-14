// SPDX-License-Identifier: MIT
// Explicit controller arming also enables system motion; saved input preference
// alone never represents motion permission after a restart or system stop.
export function initControllerToggle({call, notice, onChanged}) {
  const button = document.getElementById('controller-toggle');
  let state = null, online = false, pending = false;
  const armed = () => typeof state?.armed === 'boolean' ? state.armed
    : Boolean(state?.enabled && state?.connected && state?.system_armed !== false);
  function render() {
    const known = online && typeof state?.enabled === 'boolean';
    const attached = state?.attached ?? Boolean(state?.devices?.length);
    const active = known && state.enabled && state.connected && armed();
    const label = !known ? 'USB controller unavailable — robot disconnected'
      : !attached ? 'No USB controller attached'
      : active ? 'Controller armed — press Start or click to disarm'
      : state.connected || !state.enabled ? 'Controller disarmed — press Start or click to arm controller and motion'
      : state.error ? 'USB controller unavailable — click to disable'
      : 'USB controller connecting — click to disable';
    button.disabled = !known || !attached || pending;
    button.dataset.state = !known ? 'unavailable' : !attached ? 'absent' : active ? 'on' : state.connected || !state.enabled ? 'off' : state.error ? 'error' : 'connecting';
    button.setAttribute('aria-pressed', String(Boolean(known && attached && active)));
    button.setAttribute('aria-busy', String(pending));
    button.setAttribute('aria-label', pending ? 'Updating controller arming' : label);
    const device = state?.devices?.find(device => device.id === state.active_device_id) || state?.devices?.[0];
    button.title = [label, device?.name, state?.error].filter(Boolean).join('\n');
  }
  button.addEventListener('click', async () => {
    if (button.disabled || pending) return;
    pending = true; render();
    try {
      const result = await call('controller_set_enabled', {enabled: state.connected ? !armed() : !state.enabled});
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
