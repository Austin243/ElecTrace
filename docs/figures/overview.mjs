// README banner: the HCN example with each column one frame, turned a quarter turn and drawn to scale.
// A ribbon's width at a frame is the density at its critical point, joined to the next frame by one
// straight-sided trapezoid, so nothing is implied between frames. An atom's area is its Bader population.
//
// `node docs/figures/overview.mjs` writes overview-light.svg and overview-dark.svg next to this file from
// hcn.json, which make_data.py writes by running ElecTrace. It needs no packages, and every color is a
// plain attribute, so the SVGs stay editable in Illustrator, Inkscape, or Figma.

import { readFileSync, writeFileSync } from 'node:fs';

const hcn = JSON.parse(readFileSync(new URL('hcn.json', import.meta.url)));

// A frame below reads a track's points by position, so every track needs a point in every frame.
for (const track of hcn.tracks) {
  if (track.points.length !== hcn.frames.length || track.points.some((point, frame) => point.frame !== frame)) {
    throw new Error('hcn.json has a track without a point in every frame');
  }
}

const SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";
const MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace";
const [ID, WIDTH, HEIGHT] = ['et-overview', 1200, 530];
const FRAME_PITCH = 165;
const PER_ANGSTROM = 118;
const PER_DENSITY = 120; // units per a.u. of density
const STRAND_DENSITY = 0.05; // rulings inside a ribbon, in a.u., so its grain doubles as a scale
const FRAME_LABEL_Y = 492;

// The dark theme uses the navy, cyan, and amber of the HTML report (src/electrace/report.py), and the
// light theme is its counterpart on a pale ground. Each bond type has a `mid` color for fills and a
// `hi` step of the same hue for edges and thin marks.
const THEMES = {
  light: {
    card: '#f5fafc',
    edge: '#d4e3ea',
    ink: '#0b2030',
    ink2: '#475f70',
    ink3: '#587385',
    rule: '#c9d9e1',
    ch: { mid: '#c5770f', hi: '#8f520d' },
    nh: { mid: '#008c9e', hi: '#007187' },
    cn: { mid: '#7d9ab1', hi: '#3c5a72' },
    fill: [0.62, 0.38],
    strand: 0.3,
    atom: '#14293a',
    atomInk: '#ffffff',
  },
  dark: {
    card: '#071019',
    edge: '#1d3142',
    ink: '#e9f2f8',
    ink2: '#a7bac8',
    ink3: '#7f96a7',
    rule: '#26404f',
    ch: { mid: '#c48506', hi: '#ffcc66' },
    nh: { mid: '#00a8b6', hi: '#56d7e4' },
    cn: { mid: '#46617a', hi: '#a9c1d4' },
    fill: [0.86, 0.6],
    strand: 0.32,
    atom: '#c9d8e3',
    atomInk: '#071019',
  },
};

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const r2 = (v) => (Number.isInteger(v) ? String(v) : String(+v.toFixed(2)));
const lerp = (a, b, t) => a + (b - a) * t;
const polyline = (points) => points.map(([x, y], i) => `${i ? 'L' : 'M'}${r2(x)} ${r2(y)}`).join('');

const criticalPoints = hcn.tracks.filter((track) => track.kind === 'bond_critical_point');
const moving = criticalPoints.find((track) => new Set(track.points.flatMap((p) => p.neighbors)).size === 3);
const fixed = criticalPoints.find((track) => track !== moving);
const populations = hcn.tracks.filter((track) => track.kind === 'atomic_charge');

const TITLE = 'ElecTrace: trace charges, critical points, and bonds across the frames of a reaction path.';
const ALT =
  'Seven frames of the HCN to HNC example in the repository, a hand-defined static scan, not an optimized path. Each column is one frame drawn to ' +
  'scale with the C–N axis vertical, and the area of each atom is its Bader electron population. Ribbons join the bond critical points frame by ' +
  `frame, and the width of a ribbon at a frame is the density there. The C–N ribbon stays near ${fixed.points[0].field.toFixed(2)} a.u. The other ribbon is ` +
  `one critical point on one track, tied to C and H through frame 02 and to N and H from frame 03, a candidate change of neighbors with confidence ${hcn.reassignment_confidence.toFixed(2)}.`;

class Figure {
  constructor(theme) {
    this.t = THEMES[theme];
    this.out = [];
    this.defs = [];
  }

  attrs(o) {
    return Object.entries(o)
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => `${k}="${typeof v === 'number' ? r2(v) : esc(v)}"`)
      .join(' ');
  }

  el(tag, o) {
    this.out.push(`<${tag} ${this.attrs(o)}/>`);
  }

  rect(x, y, w, h, o) {
    this.el('rect', { x, y, width: w, height: h, ...o });
  }

  line(x1, y1, x2, y2, stroke) {
    this.el('line', { x1, y1, x2, y2, stroke, 'stroke-width': 1 });
  }

  circle(cx, cy, r, o) {
    this.el('circle', { cx, cy, r, ...o });
  }

  path(d, o) {
    this.el('path', { d, fill: 'none', ...o });
  }

  polygon(points, o) {
    this.el('polygon', { points: points.map(([x, y]) => `${r2(x)},${r2(y)}`).join(' '), ...o });
  }

  /** Critical point marker, a diamond centered on (x, y). */
  diamond(x, y, r, o) {
    this.polygon([[x, y - r], [x + r, y], [x, y + r], [x - r, y]], o);
  }

  /** One line of text; y is the baseline. */
  text(x, y, s, o = {}) {
    const size = o.size ?? 24;
    const a = this.attrs({
      x,
      y,
      'font-size': size,
      'font-weight': o.weight,
      'font-family': o.mono ? MONO : undefined,
      'text-anchor': o.anchor,
      'letter-spacing': o.spacing,
      fill: o.fill ?? this.t.ink,
    });
    this.out.push(`<text ${a}>${esc(s)}</text>`);
  }

  /** A horizontal gradient in user coordinates; returns the paint for a fill or stroke. */
  gradient(key, x1, x2, stops) {
    const id = `${ID}-${key}`;
    const body = stops.map(([offset, color, opacity]) => `<stop offset="${offset}" stop-color="${color}" stop-opacity="${opacity}"/>`).join('');
    this.defs.push(`<linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="${r2(x1)}" y1="0" x2="${r2(x2)}" y2="0">${body}</linearGradient>`);
    return `url(#${id})`;
  }

  toString() {
    const t = this.t;
    return [
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${WIDTH} ${HEIGHT}" width="${WIDTH}" height="${HEIGHT}" role="img" aria-labelledby="${ID}-title ${ID}-desc" font-family="${SANS}">`,
      `<title id="${ID}-title">${esc(TITLE)}</title>`,
      `<desc id="${ID}-desc">${esc(ALT)}</desc>`,
      `<defs>${this.defs.join('')}</defs>`,
      `<rect x="0.5" y="0.5" width="${WIDTH - 1}" height="${HEIGHT - 1}" rx="18" fill="${t.card}" stroke="${t.edge}"/>`,
      ...this.out,
      '</svg>',
    ].join('\n') + '\n';
  }
}

/** Ribbon through { x, y, width, color } per frame: one trapezoid per step with rulings parallel to the center. */
function ribbon(fig, key, frames, spacing) {
  const t = fig.t;
  for (let i = 0; i < frames.length - 1; i++) {
    const [a, b] = [frames[i], frames[i + 1]];
    const same = a.color === b.color;
    const body = fig.gradient(`${key}-${i}`, a.x, b.x, same
      ? [[0, a.color.mid, t.fill[0]], [0.16, a.color.mid, t.fill[1]], [0.84, a.color.mid, t.fill[1]], [1, a.color.mid, t.fill[0]]]
      : [[0, a.color.mid, t.fill[0]], [1, b.color.mid, t.fill[0]]]);
    const edge = same ? a.color.hi : fig.gradient(`${key}-${i}-edge`, a.x, b.x, [[0, a.color.hi, 1], [1, b.color.hi, 1]]);
    fig.polygon([[a.x, a.y - a.width / 2], [b.x, b.y - b.width / 2], [b.x, b.y + b.width / 2], [a.x, a.y + a.width / 2]], { fill: body });
    // Rulings that the ribbon's edge cuts where it narrows.
    let strands = '';
    for (let k = 0; k <= Math.ceil(Math.max(a.width, b.width) / 2 / spacing); k++) {
      for (const sign of [-1, 1]) {
        const offset = sign * (k + 0.5) * spacing;
        const need = 2 * Math.abs(offset);
        if (a.width < need && b.width < need) continue;
        const start = a.width < need ? (need - a.width) / (b.width - a.width) : 0;
        const end = b.width < need ? (need - a.width) / (b.width - a.width) : 1;
        const at = (u) => [lerp(a.x, b.x, u), lerp(a.y, b.y, u) + offset];
        strands += polyline([at(start), at(end)]);
      }
    }
    if (strands) fig.path(strands, { stroke: edge, 'stroke-width': 0.9, 'stroke-opacity': t.strand });
    fig.path(polyline([[a.x, a.y - a.width / 2], [b.x, b.y - b.width / 2]]) + polyline([[a.x, a.y + a.width / 2], [b.x, b.y + b.width / 2]]), { stroke: edge, 'stroke-width': 1.3, 'stroke-opacity': 0.9 });
  }
  // The measured cross-section at each frame, on top of the ribbon.
  for (const p of frames) fig.rect(p.x - 1.7, p.y - p.width / 2, 3.4, p.width, { fill: p.color.hi });
}

const frameX = (frame) => 118 + frame * FRAME_PITCH;
const place = (frame, x, y) => [frameX(frame) + (y - 8) * PER_ANGSTROM, 246 - (x - 8) * PER_ANGSTROM];
const radius = (population) => 6.6 * Math.sqrt(population);

function draw(fig) {
  const t = fig.t;
  const frames = hcn.frames;
  frames.forEach((frame, f) => {
    const top = Math.min(...frame.atoms.map((atom) => place(f, atom.x, atom.y)[1])) - 16;
    fig.line(frameX(f), Math.max(top, 156), frameX(f), FRAME_LABEL_Y - 26, t.rule);
  });

  fig.text(44, 80, 'ElecTrace', { size: 62, weight: 760, spacing: -1.2 });
  fig.text(46, 118, 'Trace charges, critical points, and bonds', { size: 26, fill: t.ink2 });
  fig.text(46, 150, 'across the frames of a reaction path.', { size: 26, fill: t.ink2 });

  const colorOf = (point) => (point.neighbors.includes(0) ? t.ch : t.nh);
  const sections = (track, color) =>
    track.points.map((point, f) => {
      const [x, y] = place(f, point.x, point.y);
      return { x, y, width: point.field * PER_DENSITY, color: color(point) };
    });
  const bond = sections(fixed, () => t.cn);
  const hydrogen = sections(moving, colorOf);
  ribbon(fig, 'cn', bond, STRAND_DENSITY * PER_DENSITY);
  ribbon(fig, 'h', hydrogen, STRAND_DENSITY * PER_DENSITY);

  // Each frame's molecule on top of the ribbons it generates.
  frames.forEach((frame, f) => {
    const [c, n, h] = frame.atoms.map((atom) => place(f, atom.x, atom.y));
    const point = moving.points[f];
    fig.path(polyline([c, [bond[f].x, bond[f].y], n]), { stroke: t.cn.hi, 'stroke-width': 2 });
    fig.path(polyline([h, [hydrogen[f].x, hydrogen[f].y], point.neighbors.includes(0) ? c : n]), { stroke: colorOf(point).hi, 'stroke-width': 2 });
    fig.diamond(bond[f].x, bond[f].y, 5.5, { fill: t.cn.hi, stroke: t.card, 'stroke-width': 1.5 });
    fig.diamond(hydrogen[f].x, hydrogen[f].y, 5.5, { fill: colorOf(point).hi, stroke: t.card, 'stroke-width': 1.5 });
    [[c, 'C'], [n, 'N'], [h, 'H']].forEach(([position, element], k) => {
      const r = radius(populations[k].points[f].population);
      fig.circle(position[0], position[1], r, { fill: t.atom, stroke: t.card, 'stroke-width': 1.5 });
      if (element !== 'H') fig.text(position[0], position[1] + r * 0.4, element, { size: r * 1.12, weight: 700, anchor: 'middle', fill: t.atomInk });
    });
    fig.text(frameX(f), FRAME_LABEL_Y, frame.label, { size: 23, mono: true, anchor: 'middle', fill: t.ink2 });
  });
  fig.text(44, FRAME_LABEL_Y, 'frame', { size: 22, fill: t.ink3 });

  fig.text(hydrogen[0].x + 34, hydrogen[0].y - 28, 'C–H', { weight: 650 });
  fig.text(bond[6].x - 60, bond[6].y - 44, 'C–N', { weight: 650 });
  fig.text(hydrogen[5].x - 92, hydrogen[5].y - 34, 'N–H', { weight: 650 });
}

for (const theme of Object.keys(THEMES)) {
  const fig = new Figure(theme);
  draw(fig);
  writeFileSync(new URL(`overview-${theme}.svg`, import.meta.url), fig.toString());
}
