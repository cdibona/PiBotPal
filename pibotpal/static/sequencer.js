// SPDX-License-Identifier: MIT
// Browser-only sequence math and schematic thumbnails. These helpers never
// contact the controller, change arming, or submit physical movement.
const SVG_NS = 'http://www.w3.org/2000/svg';
const RESERVED_NAMES = new Set(['stop', 'arm', 'halt']);
const finitePose = angles => Array.isArray(angles) && angles.length === 12 && Array.from(angles).every(Number.isFinite);

/** Find a valid, unused name without changing the original saved action. */
export function uniqueActionName(sourceName, existingNames = []) {
  const source = (typeof sourceName === 'string' ? sourceName : 'action')
    .normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[^a-z0-9 _-]/g, ' ').replace(/\s+/g, ' ')
    .replace(/^[^a-z0-9]+/, '').trim() || 'action';
  const names = typeof existingNames === 'string' ? [existingNames] : existingNames || [];
  const used = new Set(Array.from(names).filter(name => typeof name === 'string').map(name => name.toLowerCase()));
  // Every suffix is distinct, so at most used.size + 1 attempts are needed,
  // including libraries containing all 128 custom actions and the built-ins.
  for (let number = 1; number <= used.size + 1; number++) {
    const suffix = number === 1 ? ' copy' : ` copy ${number}`;
    const candidate = source.slice(0, 64 - suffix.length).trimEnd() + suffix;
    if (!used.has(candidate) && !RESERVED_NAMES.has(candidate)) return candidate;
  }
  throw new Error('Could not create a unique action name');
}

/**
 * Ideal sequence time from the first known pose. Destination-frame speed sets
 * each transition; first and final holds count. Physical initial approach,
 * servo grouping, aggregate power caps and scheduler delays are not estimated.
 */
export function sequenceTiming(frames) {
  const invalid = () => ({duration: 0, frames: [], valid: false});
  if (!Array.isArray(frames) || !frames.length) return invalid();
  const result = [];
  let departure = 0;
  for (let index = 0; index < frames.length; index++) {
    const frame = frames[index];
    if (!frame || !finitePose(frame.angles) || !Number.isFinite(frame.speed_deg_s) || frame.speed_deg_s <= 0
        || !Number.isFinite(frame.hold_seconds) || frame.hold_seconds < 0) return invalid();
    const transition = index === 0 ? 0 : Math.max(...frame.angles.map((angle, joint) =>
      Math.abs(angle - frames[index - 1].angles[joint]))) / frame.speed_deg_s;
    const arrival = departure + transition;
    departure = arrival + frame.hold_seconds;
    if (!Number.isFinite(transition) || !Number.isFinite(departure)) return invalid();
    result.push({index, arrival, departure, transition, hold: frame.hold_seconds});
  }
  return {duration: departure, frames: result, valid: true};
}

/** Sample ideal playback; frame identifies the transition destination/held pose. */
export function sampleSequence(frames, timing, time) {
  const invalid = () => ({angles: null, frame: null, position: 0, phase: 'complete', time: 0});
  if (!timing?.valid || !Array.isArray(frames) || !frames.length
      || !Array.isArray(timing.frames) || timing.frames.length !== frames.length
      || !Number.isFinite(timing.duration) || timing.duration < 0) return invalid();
  const last = frames.length - 1;
  const elapsed = Math.max(0, Math.min(timing.duration, typeof time === 'number' && !Number.isNaN(time) ? time : 0));
  const atFrame = (index, phase) => finitePose(frames[index]?.angles)
    ? {angles: [...frames[index].angles], frame: index, position: index, phase, time: elapsed}
    : invalid();
  // Includes one-pose and zero-duration sequences, with no fabricated dwell.
  if (elapsed >= timing.duration) return atFrame(last, 'complete');
  for (let index = 0; index < frames.length; index++) {
    const span = timing.frames[index];
    if (!span || !Number.isFinite(span.arrival) || !Number.isFinite(span.departure)
        || !Number.isFinite(span.transition) || span.transition < 0 || span.departure < span.arrival) return invalid();
    if (index > 0 && elapsed < span.arrival) {
      if (!finitePose(frames[index - 1]?.angles) || !finitePose(frames[index]?.angles) || span.transition <= 0) return invalid();
      const weight = Math.max(0, Math.min(1, (elapsed - timing.frames[index - 1].departure) / span.transition));
      const angles = frames[index - 1].angles.map((angle, joint) => angle + (frames[index].angles[joint] - angle) * weight);
      return {angles, frame: index, position: index - 1 + weight, phase: 'transition', time: elapsed};
    }
    if (elapsed < span.departure) return atFrame(index, 'hold');
  }
  return atFrame(last, 'complete');
}

// Same command geometry as kinematics.py: body side 77 mm, links C/A/B
// 33/48/78 mm, RF/LF/LR/RR mirroring, logical knee/shoulder/hip angles.
const SIGNS = [[1, 1], [-1, 1], [-1, -1], [1, -1]];
const RAD = Math.PI / 180;
function legPoints(angles, leg) {
  const [knee, shoulder, hip] = angles.slice(leg * 3, leg * 3 + 3).map(angle => angle * RAD);
  const heading = Math.PI / 4 - hip, upper = Math.PI / 2 - shoulder, lower = upper + knee - Math.PI / 2;
  const planar = [[0, 0], [33, 0], [33 + 48 * Math.cos(upper), 48 * Math.sin(upper)]];
  planar.push([planar[2][0] + 78 * Math.cos(lower), planar[2][1] + 78 * Math.sin(lower)]);
  const [sx, sy] = SIGNS[leg];
  return planar.map(([radial, z]) => [sx * (radial * Math.cos(heading) + 38.5), sy * (radial * Math.sin(heading) + 38.5), z]);
}

// Fixed orthographic camera: 45° around the body, 30° above its plane.
// Identical framing across thumbnails makes pose changes directly comparable.
function project([x, y, z]) {
  return [(x - y) * Math.SQRT1_2, -(x + y) * Math.SQRT1_2 * .5 - z * Math.sqrt(3) / 2];
}

function svgElement(tag, attributes = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Full four-leg command geometry; schematic projection, never measured pose. */
export function createPoseThumbnail(angles) {
  const svg = svgElement('svg', {class: 'pose-thumbnail', viewBox: '-230 -190 460 380',
    role: 'img', 'aria-label': 'Four-leg robot pose from commanded servo angles', focusable: 'false'});
  svg.append(svgElement('title', {}, 'Schematic commanded pose, not measured position'));
  if (!finitePose(angles)) {
    svg.setAttribute('data-valid', 'false');
    svg.append(svgElement('text', {x: 0, y: 0, 'text-anchor': 'middle', 'font-size': 22,
      fill: 'var(--muted, #8e9fa9)'}, 'Pose unavailable'));
    return svg;
  }
  svg.setAttribute('data-valid', 'true');
  const drawLeg = leg => {
    const points = legPoints(angles, leg).map(project);
    const group = svgElement('g', {class: 'pose-thumbnail-leg', 'data-leg': leg});
    for (let link = 0; link < 3; link++) {
      group.append(svgElement('line', {class: 'pose-thumbnail-link', 'data-link': link,
        x1: points[link][0], y1: points[link][1], x2: points[link + 1][0], y2: points[link + 1][1],
        stroke: 'var(--pose-limb, var(--teal, #6ddfc2))', 'stroke-width': 7, 'stroke-linecap': 'round'}));
    }
    points.forEach(([cx, cy], joint) => {
      const attributes = {cx, cy, r: joint === 3 ? 5 : 6, 'data-joint': joint,
        class: joint === 3 ? 'pose-thumbnail-foot' : 'pose-thumbnail-joint',
        fill: joint === 3 ? 'var(--pose-foot, var(--text, #e8eff0))' : 'var(--pose-joint, var(--orange, #f0ac72))',
        stroke: 'var(--panel, #131c23)', 'stroke-width': 2};
      if (joint < 3) attributes['data-servo'] = leg * 3 + [2, 1, 0][joint];
      group.append(svgElement('circle', attributes));
    });
    svg.append(group);
  };
  // Draw far legs, body, then the near leg; no perspective or per-pose zoom.
  [2, 1, 3].forEach(drawLeg);
  const body = SIGNS.map(([sx, sy]) => project([sx * 38.5, sy * 38.5, 0]));
  svg.append(svgElement('polygon', {class: 'pose-thumbnail-body', points: body.map(point => point.join(',')).join(' '),
    fill: 'var(--pose-body, var(--panel-2, #18242c))', stroke: 'var(--muted, #8e9fa9)', 'stroke-width': 3}));
  drawLeg(0);
  const front = [[0, 29, 2], [-8, 12, 2], [8, 12, 2]].map(project);
  svg.append(svgElement('polygon', {class: 'pose-thumbnail-front', points: front.map(point => point.join(',')).join(' '),
    fill: 'var(--pose-front, var(--text, #e8eff0))'}));
  return svg;
}
