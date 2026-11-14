"""Deterministic sequencer math and SVG geometry checks; no server or robot.

Run: .venv/bin/python tests/browser/check_sequencer_math.py
Every browser request is fulfilled from this fixture and local module source.
"""
import math
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pibotpal.kinematics import leg_geometry

HTML = '<!doctype html><html><head><meta charset="utf-8"></head><body><script type="module">import * as sequencer from "/sequencer.js"; window.sequencer = sequencer;</script></body></html>'

MATH_CHECKS = r"""() => {
  const {uniqueActionName, sequenceTiming, sampleSequence, createPoseThumbnail} = window.sequencer;
  let checks = 0;
  const assert = (condition, label) => { checks++; if (!condition) throw new Error(label); };
  const near = (actual, expected, label) => assert(Math.abs(actual - expected) < 1e-9, `${label}: ${actual} != ${expected}`);
  const pose = value => Array(12).fill(value);
  const frame = (angles = pose(0), speed = 10, hold = 0) => ({angles, speed_deg_s: speed, hold_seconds: hold});

  assert(uniqueActionName('wave', ['wave']) === 'wave copy', 'First copy name');
  assert(uniqueActionName('wave', ['wave', 'wave copy']) === 'wave copy 2', 'Second copy name');
  assert(uniqueActionName('wave', new Set(['wave copy', 'wave copy 3'])) === 'wave copy 2', 'First free suffix');
  assert(uniqueActionName('Wáve', []) === 'wave copy', 'Lowercase ASCII names');
  const used = ['wave', 'stand', 'sit'];
  for (let n = 1; n <= 144; n++) {
    const name = uniqueActionName('wave', used);
    assert(name === `wave copy${n === 1 ? '' : ' ' + n}`, 'Names cover full library without collisions');
    used.push(name);
  }
  const longNames = [];
  for (let n = 0; n < 130; n++) {
    const name = uniqueActionName('a'.repeat(64), longNames);
    assert(name.length <= 64 && !longNames.includes(name), 'Long names retain unique suffixes');
    longNames.push(name);
  }
  for (const source of ['stop', 'arm', 'halt', '', ' --- ', '<script>alert(1)</script>', '  Two    words  ', null]) {
    const result = uniqueActionName(source, []);
    assert(/^[a-z0-9][a-z0-9 _-]{0,63}$/.test(result) && !result.includes('  '), 'Generated names satisfy server syntax');
    assert(result === result.trim() && !['stop','arm','halt'].includes(result), 'Generated names avoid reserved controls');
  }

  const first = pose(0), second = pose(0), third = pose(0);
  second[0] = 20; third[0] = -10; third[11] = 40;
  const frames = [frame(first, 999, 2), frame(second, 10, 1), frame(third, 20), frame([...third], 144, 3)];
  const before = JSON.stringify(frames), timing = sequenceTiming(frames);
  assert(timing.valid, 'Valid sequence'); near(timing.duration, 10, 'First and final holds count');
  const expected = [
    {index:0,arrival:0,departure:2,transition:0,hold:2},
    {index:1,arrival:4,departure:5,transition:2,hold:1},
    {index:2,arrival:7,departure:7,transition:2,hold:0},
    {index:3,arrival:7,departure:10,transition:0,hold:3},
  ];
  assert(JSON.stringify(timing.frames) === JSON.stringify(expected), 'Destination speed determines transition spans');
  const samples = [
    [-1,0,0,'hold',0,0], [1,0,0,'hold',0,0], [2,1,0,'transition',0,0],
    [3,1,.5,'transition',10,0], [4,1,1,'hold',20,0],
    [5,2,1,'transition',20,0], [6,2,1.5,'transition',5,20],
    [7,3,3,'hold',-10,40], [9,3,3,'hold',-10,40],
    [10,3,3,'complete',-10,40], [999,3,3,'complete',-10,40],
  ];
  for (const [time,index,position,phase,a,b] of samples) {
    const sampled = sampleSequence(frames, timing, time);
    assert(sampled.frame === index && sampled.phase === phase, `Sample phase/frame at ${time}`);
    near(sampled.position, position, `Fractional cursor at ${time}`);
    near(sampled.angles[0], a, `First servo at ${time}`);
    near(sampled.angles[11], b, `Last servo at ${time}`);
    near(sampled.time, Math.max(0, Math.min(10,time)), 'Clamped time');
  }
  assert(sampleSequence(frames, timing, Infinity).phase === 'complete', 'Infinite time clamps to completion');
  assert(sampleSequence(frames, timing, NaN).time === 0, 'Invalid time does not produce NaN');
  sampleSequence(frames, timing, 4).angles[0] = 900;
  assert(JSON.stringify(frames) === before, 'Timing and samples never mutate source poses');

  const instantaneous = [frame(), frame(), frame()];
  const instantTiming = sequenceTiming(instantaneous);
  assert(instantTiming.valid && instantTiming.duration === 0, 'Identical poses have no fabricated minimum');
  const instant = sampleSequence(instantaneous, instantTiming, 0);
  assert(instant.frame === 2 && instant.position === 2 && instant.phase === 'complete', 'Zero-duration sequence ends stably');
  const single = [frame(pose(3), 30, 2)];
  assert(sampleSequence(single, sequenceTiming(single), 1).phase === 'hold', 'Single frame first hold');
  assert(sampleSequence(single, sequenceTiming(single), 2).phase === 'complete', 'Single frame final boundary');
  const tiny = [frame(), frame(pose(.001), 100)];
  near(sequenceTiming(tiny).duration, .00001, 'No minimum transition interval');

  const invalid = [null, [], Array(2), [frame(null)], [frame(Array(12))],
    [frame(pose(0).slice(1))], [frame(pose('0'))], [frame(pose(NaN))], [frame(pose(Infinity))],
    [frame(pose(0), 0)], [frame(pose(0), -1)], [frame(pose(0), true)], [frame(pose(0), '30')],
    [frame(pose(0), Infinity)], [frame(pose(0), NaN)], [frame(pose(0), 30, -1)],
    [frame(pose(0), 30, true)], [frame(pose(0), 30, NaN)], [frame(pose(0), 30, Infinity)],
    [frame(pose(-1e308)), frame(pose(1e308))], [frame(pose(0), 1, 1e308), frame(pose(0), 1, 1e308)]];
  for (const value of invalid) {
    const result = sequenceTiming(value);
    assert(!result.valid && result.duration === 0 && result.frames.length === 0, 'Invalid data fails cleanly');
    assert(sampleSequence(value, result, 0).angles === null, 'Invalid data cannot invent a pose');
  }
  assert(sampleSequence(frames, {...timing, frames:[]}, 0).angles === null, 'Mismatched timeline fails cleanly');
  const unavailable = createPoseThumbnail([null]);
  assert(unavailable.dataset.valid === 'false' && unavailable.querySelectorAll('.pose-thumbnail-leg').length === 0, 'Unknown angles do not invent leg positions');
  assert(!unavailable.querySelector('script,foreignObject'), 'SVG uses only safe native geometry');
  return checks;
}"""

base = [-40, 40, 0] * 4
poses = [base]
for servo in range(12):
    changed = base.copy()
    changed[servo] += 7
    poses.append(changed)
for knee in (-90, 90):
    for shoulder in (-10, 90):
        for hip in (-60, 60):
            poses.append([knee, shoulder, hip] * 4)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(request):
        if request.request.url == 'http://sequencer.test/':
            request.fulfill(content_type='text/html', body=HTML)
        elif request.request.url == 'http://sequencer.test/sequencer.js':
            request.fulfill(content_type='text/javascript', path=str(ROOT / 'pibotpal/static/sequencer.js'))
        else:
            raise AssertionError('Unexpected network request: ' + request.request.url)

    page.route('**/*', route)
    page.goto('http://sequencer.test/', wait_until='domcontentloaded')
    page.wait_for_function('Boolean(window.sequencer)')
    count = page.evaluate(MATH_CHECKS)
    rendered = page.evaluate("""poses => poses.map(angles => {
      const svg = window.sequencer.createPoseThumbnail(angles);
      document.body.append(svg);
      return {viewBox:svg.getAttribute('viewBox'), valid:svg.dataset.valid,
        body:svg.querySelector('.pose-thumbnail-body').getAttribute('points'),
        front:svg.querySelector('.pose-thumbnail-front').getAttribute('points'),
        legs:[0,1,2,3].map(leg => {
          const group=svg.querySelector(`[data-leg="${leg}"]`);
          return {links:group.querySelectorAll('.pose-thumbnail-link').length,
            points:[0,1,2,3].map(joint => {
              const node=group.querySelector(`[data-joint="${joint}"]`);
              return [+node.getAttribute('cx'),+node.getAttribute('cy')];
            }), servos:[...group.querySelectorAll('[data-servo]')].map(node=>+node.dataset.servo)};
        })};
    })""", poses)
    for angles, drawing in zip(poses, rendered):
        assert drawing['valid'] == 'true'
        assert drawing['viewBox'] == '-230 -190 460 380', 'Framing must not change between poses'
        assert drawing['body'] == rendered[0]['body'] and drawing['front'] == rendered[0]['front']
        for leg in range(4):
            actual = drawing['legs'][leg]
            assert actual['links'] == 3
            assert actual['servos'] == [leg * 3 + 2, leg * 3 + 1, leg * 3]
            for actual_point, (x, y, z) in zip(actual['points'], leg_geometry(leg, angles[leg * 3:leg * 3 + 3])['joints_mm']):
                expected = [(x - y) / math.sqrt(2), -(x + y) / math.sqrt(2) * .5 - z * math.sqrt(3) / 2]
                assert all(abs(a - b) < 1e-9 for a, b in zip(actual_point, expected)), (leg, angles, actual_point, expected)
                assert -230 < actual_point[0] < 230 and -190 < actual_point[1] < 190, 'A valid leg was clipped'
    for servo in range(12):
        changed = rendered[servo + 1]
        for leg in range(4):
            if leg == servo // 3:
                assert changed['legs'][leg]['points'] != rendered[0]['legs'][leg]['points'], f'Servo {servo} did not affect geometry'
            else:
                assert changed['legs'][leg] == rendered[0]['legs'][leg], f'Servo {servo} affected another leg'
    assert not errors, errors
    browser.close()

print(f'PASS {count} deterministic sequencer assertions; {len(poses)} four-leg geometries match Python kinematics; all twelve servos affect their own leg; no service or hardware contacted.', flush=True)
