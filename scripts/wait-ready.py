#!/usr/bin/env python3
"""Read-only HTTP readiness probe used by systemd during service startup."""
import json
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def wait_ready(timeout=40, interval=.25, opener=None):
    opener = opener or build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open('http://127.0.0.1:8765/health', timeout=1) as response:
                state = json.loads(response.read(4096))
                if response.status == 200 and isinstance(state, dict) and state.get('status') == 'ok' and state.get('service') == 'PiBotPal' and isinstance(state.get('server_instance_id'), str) and state['server_instance_id']:
                    print('PiBotPal HTTP ready; instance ' + state['server_instance_id'], flush=True)
                    return True
        except (URLError, OSError, ValueError):
            pass
        time.sleep(interval)
    print('PiBotPal HTTP readiness timed out; see journalctl --user-unit=pibotpal.service', file=sys.stderr)
    return False


if __name__ == '__main__':
    raise SystemExit(0 if wait_ready() else 1)
