// SPDX-License-Identifier: MIT
// One explicit playback request at a time, shared by both planner editors.
export function initPlayback({call, getStatus, onStatus}) {
  let flight = null, stopping = null, epoch = 0;
  const listeners = new Set();
  const notify = () => { for (const listener of listeners) listener(); };
  return {
    get pending() { return Boolean(flight || stopping); },
    get stopping() { return Boolean(stopping); },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    async play(tool, args) {
      if (flight || stopping) throw new Error('Wait for the playback request to finish.');
      const robot = getStatus() || {};
      if (typeof robot.armed !== 'boolean' || typeof robot.busy !== 'boolean') throw new Error('Reconnect to the robot before playing.');
      if (robot.busy || robot.plan?.running || (robot.examples?.running && !robot.examples.permits_manual_motion)) throw new Error('Stop the current movement, routine or test before playing.');
      if (!['move', 'plan_run'].includes(tool)) throw new Error('Unsupported playback command.');
      const request = ++epoch;
      const task = (async () => {
        const result = await call(tool, {...args, arm_motion: !robot.armed});
        if (request !== epoch) throw new Error('Playback was stopped.');
        try {
          const state = await call('get_status');
          if (request === epoch) onStatus(state);
        } catch (error) {
          if (request === epoch) onStatus({});
          throw error;
        }
        if (request !== epoch) throw new Error('Playback was stopped.');
        return result;
      })();
      flight = task; notify();
      try { return await task; }
      finally { if (flight === task) flight = null; notify(); }
    },
    stop() {
      if (stopping) return stopping;
      const pending = flight;
      ++epoch;
      const task = (async () => {
        let failure;
        try { onStatus(await call('stop')); } catch (error) { failure = error; }
        if (pending) {
          // Stop immediately, then stop again after an outstanding Play reply.
          // A delayed response must never leave a newly armed action running.
          await pending.catch(() => {});
          try { onStatus(await call('stop')); failure = null; } catch (error) { failure = error; }
        }
        if (failure) { onStatus({}); throw failure; }
      })();
      stopping = task; notify();
      return task.finally(() => { if (stopping === task) stopping = null; notify(); });
    },
  };
}
