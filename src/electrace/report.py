"""Self-contained interactive HTML reporting for ElecTrace analyses."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from electrace._files import atomic_write_text
from electrace.export import _strict_json_dumps

_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #071019;
      --panel: #0d1a26;
      --panel-2: #122333;
      --line: #284053;
      --text: #e9f2f8;
      --muted: #9db1c0;
      --cyan: #56d7e4;
      --amber: #ffcc66;
      --green: #64d98b;
      --red: #ff7b7b;
      --violet: #b895ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: radial-gradient(circle at 12% -10%, #16354a 0, transparent 30rem), var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }
    header, main { width: min(1180px, calc(100% - 2rem)); margin: 0 auto; }
    header { padding: 3rem 0 1.5rem; }
    h1 { font-size: clamp(2rem, 5vw, 4.25rem); line-height: .95; letter-spacing: -.055em; margin: 0; }
    h1 span { color: var(--cyan); }
    .kicker { color: var(--cyan); font: 700 .74rem/1.2 ui-monospace, monospace; letter-spacing: .18em; text-transform: uppercase; margin-bottom: .8rem; }
    .subtitle { max-width: 60rem; color: var(--muted); font-size: 1.05rem; }
    .meta { display: flex; gap: .7rem; flex-wrap: wrap; color: var(--muted); font: .8rem ui-monospace, monospace; }
    .meta span { border: 1px solid var(--line); border-radius: 999px; padding: .35rem .7rem; }
    main { padding-bottom: 5rem; }
    .cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: .8rem; margin: 1.2rem 0; }
    .card, section { background: linear-gradient(145deg, rgba(18,35,51,.93), rgba(10,24,36,.93)); border: 1px solid var(--line); border-radius: 14px; box-shadow: 0 18px 40px rgba(0,0,0,.16); }
    .card { padding: 1rem; min-height: 105px; }
    .card b { display: block; font-size: 2rem; letter-spacing: -.04em; }
    .card small { color: var(--muted); text-transform: uppercase; letter-spacing: .08em; }
    section { padding: 1.2rem; margin: .8rem 0; overflow: hidden; }
    section h2 { margin: 0 0 .8rem; font-size: 1rem; letter-spacing: .03em; }
    .grid { display: grid; grid-template-columns: 1.55fr 1fr; gap: .8rem; }
    svg { width: 100%; height: 280px; display: block; overflow: visible; }
    .axis { stroke: var(--line); stroke-width: 1; }
    .energy-line { fill: none; stroke: var(--cyan); stroke-width: 2.5; }
    .energy-point { fill: var(--panel); stroke: var(--cyan); stroke-width: 2; }
    .chart-label { fill: var(--muted); font: 11px ui-monospace, monospace; }
    .peak-band { fill: rgba(255,204,102,.08); }
    .peak-line { stroke: var(--amber); stroke-width: 1; stroke-dasharray: 4 4; }
    .step rect { fill: var(--panel-2); stroke: var(--cyan); }
    .step.spanning rect { stroke: var(--amber); }
    .step text { fill: var(--text); font: 10px ui-monospace, monospace; }
    .chart-note { font-size: .82rem; margin: .6rem 0 0; }
    .empty { color: var(--muted); padding: 2rem 0; text-align: center; }
    .timeline { display: grid; gap: .45rem; }
    .event { display: grid; grid-template-columns: 6.5rem 7rem minmax(0, 1fr) 5.5rem; gap: .7rem; align-items: center; border-top: 1px solid var(--line); padding: .75rem .2rem; }
    .event:first-child { border-top: 0; }
    .badge { border: 1px solid currentColor; border-radius: 999px; padding: .2rem .55rem; width: fit-content; font: 700 .7rem ui-monospace, monospace; text-transform: uppercase; }
    .birth { color: var(--green); } .death { color: var(--red); } .split, .merge { color: var(--violet); } .change, .descriptor_change, .connectivity_change { color: var(--amber); }
    .confidence { height: 5px; background: #1b3040; border-radius: 10px; overflow: hidden; margin-top: .25rem; }
    .confidence i { display:block; height:100%; background: var(--cyan); }
    .muted { color: var(--muted); }
    table { width: 100%; border-collapse: collapse; font-size: .88rem; }
    th, td { padding: .65rem .5rem; border-top: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); font: 700 .7rem ui-monospace, monospace; text-transform: uppercase; letter-spacing: .07em; }
    tbody tr:hover { background: rgba(86,215,228,.035); }
    details { border-top: 1px solid var(--line); padding: .7rem 0; }
    details:first-of-type { border-top: 0; }
    summary { cursor: pointer; color: var(--cyan); }
    pre { overflow: auto; background: #07131d; border: 1px solid var(--line); padding: .8rem; border-radius: 8px; font-size: .74rem; }
    .scroll { overflow-x: auto; }
    footer { color: var(--muted); text-align: center; font-size: .78rem; padding: 1.5rem; }
    @media (max-width: 800px) { .cards { grid-template-columns: 1fr 1fr; } .grid { grid-template-columns: 1fr; } .event { grid-template-columns: 5.5rem 5rem 1fr; } .event > :last-child { display:none; } }
  </style>
</head>
<body>
  <header>
    <div class="kicker">ElecTrace · Electronic autopsy</div>
    <h1>__HEADING__ <span>through time.</span></h1>
    <p class="subtitle" id="subtitle"></p>
    <div class="meta" id="meta"></div>
  </header>
  <main>
    <div class="cards" id="cards"></div>
    <div id="focus"></div>
    <div class="grid">
      <section><h2>Energy profile</h2><div id="energy"></div></section>
      <section><h2>Event composition</h2><div id="composition"></div></section>
    </div>
    <section><h2>Electronic event timeline</h2><div class="timeline" id="timeline"></div></section>
    <div id="noise"></div>
    <section><h2>Whole-path track changes</h2><div id="summary"></div></section>
    <div id="groups"></div>
    <section><h2>Per-bond view</h2><div id="bonds"></div></section>
    <section><h2>Frame inventory</h2><div id="frames"></div></section>
    <section><h2>Provenance and audit trail</h2><div id="audit"></div></section>
  </main>
  <footer>Generated by ElecTrace. Matching is evidence, not a unique chemical truth.</footer>
  <script id="analysis-data" type="application/json">__DATA__</script>
  <script>
    "use strict";
    const data = JSON.parse(document.getElementById("analysis-data").textContent);
    const frames = data.frames || [];
    const events = data.events || [];
    const tracks = data.tracks || [];
    const esc = value => String(value ?? "").replace(/[&<>\"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
    const fmt = (value, digits=3) => value == null || Number.isNaN(Number(value)) ? "—" : Number(value).toFixed(digits);
    const project = data.project || {};
    document.getElementById("subtitle").textContent = project.description || "Deformation-aware correspondence of atoms and non-nuclear electronic features across an ordered periodic sequence.";
    const meta = [
      `schema ${data.schema_version || "0.1"}`,
      `${project.series_kind || "ordered"} series`,
      data.generated_at || "timestamp unavailable",
      data.software_version ? `ElecTrace ${data.software_version}` : null,
    ].filter(Boolean);
    document.getElementById("meta").innerHTML = meta.map(x => `<span>${esc(x)}</span>`).join("");
    const persistent = events.filter(e => e.persistent !== false).length;
    const high = events.filter(e => String(e.confidence_label || "").toLowerCase() === "high").length;
    const cards = [[frames.length,"Frames"],[tracks.length,"Feature observations"],[events.length,"Events"],[`${high}/${persistent}`,"High confidence / persistent"]];
    document.getElementById("cards").innerHTML = cards.map(([n,l]) => `<div class="card"><b>${esc(n)}</b><small>${esc(l)}</small></div>`).join("");
    const focus = data.focus;
    if (focus) {
      const ruleNames = {bond: "bond change", spin: "spin change", bcp: "BCP connectivity change"};
      const atoms = (focus.atoms || []).map(a => `${a.element}[${a.index}]${a.rules ? ` (${a.rules.map(r => ruleNames[r] || r).join(", ")})` : ""}`).join(", ");
      const part = (c, noun) => `${c?.reported ?? "—"} of ${c?.total ?? "—"} ${noun}`;
      const auto = focus.selection === "auto";
      const parts = [part(focus.tracks, "tracks"), part(focus.observations, "feature observations"), part(focus.events, "events")];
      if (focus.bonds) parts.push(part(focus.bonds, "bond rows"));
      const listed = `${parts.slice(0, -1).join(", ")}, and ${parts[parts.length - 1]}`;
      const text = auto && !(focus.atoms || []).length ? "Automatic focus selected no atoms, so every feature is reported." : `${auto ? "Automatic focus" : "Focus"} on ${atoms}: reporting ${listed}. Matching and warnings cover every atom and feature.`;
      const skipped = Object.entries(focus.rules || {}).filter(([, r]) => r.applied === false).map(([k, r]) => ` The ${ruleNames[k] || k} rule did not run: ${r.reason}`).join("");
      document.getElementById("focus").innerHTML = `<p class="muted">${esc(text + skipped)}</p>`;
    }

    const frameName = index => frames.find(f => f.index === index)?.label ?? index ?? "—";
    const position = index => frames.findIndex(f => f.index === index);
    const barrier = data.barrier || {};
    const peaks = Array.isArray(barrier.frames) ? barrier.frames : [];
    const placeLabels = {before: "before barrier", spanning: "spans barrier", after: "after barrier"};
    function energyChart() {
      const points = frames.map((f,i) => ({x:i, y:f.energy == null ? null : Number(f.energy), label:f.label ?? String(i)})).filter(p => Number.isFinite(p.y));
      if (!points.length) return '<div class="empty">No energies were discovered.</div>';
      const width=720, height=270, left=56, right=18, top=18, bottom=42;
      const minY=Math.min(...points.map(p=>p.y)), maxY=Math.max(...points.map(p=>p.y));
      const spanY=Math.max(maxY-minY, 1e-9), maxX=Math.max(frames.length-1,1);
      const X=x=>left+(x/maxX)*(width-left-right), Y=p=>top+(maxY-p.y)/spanY*(height-top-bottom);
      const path=points.map((p,i)=>`${i?'L':'M'} ${X(p.x).toFixed(1)} ${Y(p).toFixed(1)}`).join(' ');
      const dots=points.map(p=>`<circle class="energy-point" cx="${X(p.x)}" cy="${Y(p)}" r="4"><title>${esc(p.label)}: ${fmt(p.y,6)}</title></circle>`).join('');
      const peakX=peaks.map(position).filter(i => i >= 0).map(X);
      const low=Math.min(...peakX), high=Math.max(...peakX), middle=(low+high)/2;
      const anchor=middle < left+50 ? 'start' : middle > width-right-50 ? 'end' : 'middle';
      const labelX=anchor === 'start' ? low : anchor === 'end' ? high : middle;
      const peak=peakX.length ? `<rect class="peak-band" x="${low}" y="${top}" width="${high-low}" height="${height-top-bottom}"/>${peakX.map(x=>`<line class="peak-line" x1="${x}" y1="${top}" x2="${x}" y2="${height-bottom}"/>`).join('')}<text class="chart-label" text-anchor="${anchor}" x="${labelX}" y="${top-6}">highest energy</text>` : '';
      // One marker per step: a large cell can put hundreds of events on one step.
      const steps=new Map();
      for (const e of events) {
        const key=`${e.frame_before}:${e.frame_after}`;
        if (!steps.has(key)) steps.set(key, {e, n:0, types:{}});
        const s=steps.get(key), type=String(e.type||"unknown");
        s.n += 1; s.types[type]=(s.types[type]||0)+1;
      }
      const markers=[...steps.values()].map(({e, n, types}) => {
        const a=position(e.frame_before), b=position(e.frame_after);
        if (a < 0 || b < 0) return '';
        const x=(X(a)+X(b))/2, w=12+7*String(n).length, y=height-bottom+5;
        const place=placeLabels[e.barrier_position] ? `; ${placeLabels[e.barrier_position]}` : '';
        const tip=`${frameName(e.frame_before)}→${frameName(e.frame_after)}: ${n} event${n===1?'':'s'} (${Object.entries(types).map(([k,c])=>`${c} ${k}`).join(', ')})${place}`;
        return `<g class="step ${esc(e.barrier_position || '')}"><title>${esc(tip)}</title><rect x="${x-w/2}" y="${y}" width="${w}" height="15" rx="7"/><text x="${x}" y="${y+11}" text-anchor="middle">${n}</text></g>`;
      }).join('');
      let note='';
      if (peaks.length) {
        const where=peaks.length > 1 ? `shared by frames ${peaks.map(frameName).join(", ")}` : `at frame ${frameName(peaks[0])}`;
        const endpoint=peaks.includes(frames[0]?.index) || peaks.includes(frames[frames.length-1]?.index) ? " An endpoint has the highest energy, so no interior frame stands above both ends." : "";
        note=`Highest energy ${where}: ${fmt(barrier.rise_from_first)} eV above the first frame and ${fmt(barrier.rise_from_last)} eV above the last.${endpoint} Each marker counts the events between two frames; amber markers touch a highest-energy frame.`;
      } else if (barrier.reason) {
        note=barrier.reason;
      }
      return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Energy profile"><line class="axis" x1="${left}" y1="${height-bottom}" x2="${width-right}" y2="${height-bottom}"/><line class="axis" x1="${left}" y1="${top}" x2="${left}" y2="${height-bottom}"/><text class="chart-label" x="4" y="${top+6}">${fmt(maxY)}</text><text class="chart-label" x="4" y="${height-bottom}">${fmt(minY)}</text><text class="chart-label" x="${left}" y="${height-10}">first frame</text><text class="chart-label" text-anchor="end" x="${width-right}" y="${height-10}">last frame</text>${peak}<path class="energy-line" d="${path}"/>${dots}${markers}</svg>${note ? `<p class="muted chart-note">${esc(note)}</p>` : ''}`;
    }
    document.getElementById("energy").innerHTML = energyChart();

    const counts = events.reduce((acc,e)=>{const k=String(e.type||"unknown").toLowerCase();acc[k]=(acc[k]||0)+1;return acc;},{});
    const maxCount = Math.max(1,...Object.values(counts));
    document.getElementById("composition").innerHTML = Object.keys(counts).length ? Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([k,n])=>`<div style="margin:.6rem 0"><span class="badge ${esc(k)}">${esc(k)}</span><div class="confidence"><i style="width:${100*n/maxCount}%"></i></div><small class="muted">${n} event${n===1?'':'s'}</small></div>`).join('') : '<div class="empty">No events met the configured thresholds.</div>';

    function connectivityEvidence(e) {
      const evidence=e.evidence||{}, feature=evidence.feature_match||{}, details=feature.evidence||{};
      const atoms=evidence.atom_identity||[], anchors=details.shared_neighbor_motion||[];
      const atomRows=atoms.map(a=>`<li>${esc(a.element)}[${esc(a.source_atom_index)}] → ${esc(a.target_element)}[${esc(a.target_atom_index)}]: ${esc(a.same_element_count_before)} / ${esc(a.same_element_count_after)} same-element atoms in the two frames; displacement ${fmt(a.displacement_angstrom)} Å; ${a.motion_similarity == null ? "identity fixed by atom order" : `motion similarity ${fmt(a.motion_similarity)}`}</li>`).join('');
      const anchorRows=anchors.map(a=>`<li>Relative to ${esc(a.element)}[${esc(a.source_atom_index)}] → [${esc(a.target_atom_index)}]: displacement ${fmt(a.relative_displacement_angstrom)} Å; separations ${fmt(a.distance_before_angstrom)} → ${fmt(a.distance_after_angstrom)} Å</li>`).join('');
      return `<details open><summary>Match evidence</summary><p>Critical-point displacement ${fmt(details.distance)} Å; match similarity ${fmt(feature.confidence)}.</p><ul>${atomRows}</ul><p>Critical-point motion relative to shared matched neighbors:</p>${anchorRows?`<ul>${anchorRows}</ul>`:'<p class="muted">No shared matched neighbor available.</p>'}<p class="muted">Similarities are heuristic, not probabilities. Relative motion uses a strain-factored, minimum-image metric and is correlated with the selected atom match; it does not establish continuity between frames.</p></details>`;
    }
    // Critical points below events.critical_point_min_field: say what was left out.
    const suppressed = data.suppressed_events;
    const readable = name => String(name).replace(/_/g, " ");
    const suppressedNote = suppressed ? (() => {
      const cutoffs = Object.entries(suppressed.critical_point_min_field || {}).map(([kind, value]) => `${fmt(value)} for ${readable(kind)}s`).join(", ");
      const parts = Object.entries(suppressed.counts || {}).map(([kind, counts]) => `${Object.entries(counts).map(([type, n]) => `${n} ${readable(type)}${n === 1 ? "" : "s"}`).join(", ")} of ${readable(kind)}s`);
      const scope = data.focus ? " The counts cover every atom, not only the focus." : "";
      return `<p class="muted">${esc(parts.length ? `Not listed: ${parts.join("; ")}, whose density stays below the cutoff (${cutoffs}).${scope}` : `No critical-point event fell below the density cutoff (${cutoffs}).`)}</p>`;
    })() : "";
    // critic2 counts cover every critical point, before the density cutoff and any focus.
    const cpCounts = data.critical_point_counts || [];
    const sumText = value => value == null ? "—" : `${value > 0 ? "+" : ""}${value}`;
    const cpNote = cpCounts.length ? (() => {
      const known = cpCounts.filter(c => c.morse_sum != null);
      const off = known.filter(c => c.morse_sum !== c.expected_morse_sum);
      const unknown = cpCounts.filter(c => c.morse_sum == null);
      const plural = items => items.length === 1 ? "frame" : "frames";
      const label = c => esc(c.frame_label ?? frameName(c.frame));
      const morse = "n &minus; b + r &minus; c";
      const parts = [];
      if (off.length) parts.push(`critic2 searches look incomplete in ${plural(off)} ${off.map(c => `${label(c)} (${esc(sumText(c.morse_sum))})`).join(", ")}: a complete search has ${morse} = 0 in a periodic cell (1 for a molecule), so critical points were missed there or removed by DISCARD, and critical-point births, deaths, and connectivity changes at ${off.length === 1 ? "that frame" : "those frames"} may not be chemistry.`);
      else if (known.length) parts.push(`Each critic2 search with counts has the Morse sum ${morse} of a complete search, although a missed pair of critical points would leave it unchanged.`);
      if (unknown.length) parts.push(`The sum is unknown in ${plural(unknown)} ${unknown.map(c => `${label(c)} (${esc(c.reason)})`).join(", ")}.`);
      parts.push("The counts use every critical point, before any density cutoff or focus.");
      return `<p class="muted">${parts.join(" ")}</p>`;
    })() : "";
    document.getElementById("timeline").innerHTML = cpNote + suppressedNote + (events.length ? events.map(e=>{
      const type=String(e.type||"change").toLowerCase(), confidence=Math.max(0,Math.min(1,Number(e.confidence||0)));
      const confidenceLabel=String(e.confidence_label||"unclassified").toLowerCase();
      const frameLabel = index => frames.find(f => f.index === index)?.label ?? index ?? "—";
      const span=type === "connectivity_change" ? `${frameLabel(e.frame_before)}→${frameLabel(e.frame_after)}` : (e.end_frame!=null && e.end_frame!==e.start_frame ? `${e.start_frame}→${e.end_frame}` : String(e.start_frame ?? "—"));
      const badge=type === "connectivity_change" ? "connectivity" : type;
      const evidence=type === "connectivity_change" ? connectivityEvidence(e) : `<div class="confidence"><i style="width:${confidence*100}%"></i></div>`;
      const status=type === "connectivity_change" ? "review" : `${esc(confidenceLabel)} ${fmt(confidence,2)}`;
      const place=placeLabels[e.barrier_position] ? `<br><small>${esc(placeLabels[e.barrier_position])}</small>` : "";
      return `<div class="event"><span class="badge ${esc(type)}">${esc(badge)}</span><span class="muted">frame ${esc(span)}${place}</span><div><div>${esc(e.description || `${type} event`)}</div>${evidence}${noiseNote(e)}</div><span>${status}</span></div>`;
    }).join('') : '<div class="empty">No persistent electronic events were detected.</div>');

    function noiseNote(e) {
      const n = e.noise_floor;
      if (!n) return '';
      const parts = Object.entries(n.changes || {}).map(([q, c]) => {
        const ratio = c.ratio_to_floor?.p99;
        return `${q} ${Number(c.delta) > 0 ? "+" : ""}${fmt(c.delta)}: ${ratio == null ? `the ${n.element} spectator 99th percentile is zero` : `${fmt(ratio, 1)} times the ${n.element} spectator 99th percentile`}`;
      });
      return `<small class="muted">${esc(parts.join("; "))}</small>`;
    }
    function noiseFloor(entries) {
      if (!entries.length) return '<div class="empty">No spectator atom charges, volumes, or spins to compare.</div>';
      const rows = entries.map(e => `<tr><td>${esc(e.element)}</td><td>${esc(e.quantity)}</td><td>${esc(e.producer ?? "—")}</td><td>${esc(e.atoms)}</td><td>${esc(e.steps)}</td><td>${fmt(e.median, 4)}</td><td>${fmt(e.p95, 4)}</td><td>${fmt(e.p99, 4)}</td></tr>`).join('');
      return `<p class="muted">Adjacent-frame |Δ| over atom-charge tracks that never touch a focus atom, in each source tool's units. Reporting only: event thresholds are unchanged. Each atom event above gives its change as a multiple of the 99th percentile for its element.</p><div class="scroll"><table><thead><tr><th>Element</th><th>Quantity</th><th>Source</th><th>Atoms</th><th>Steps</th><th>Median</th><th>95th</th><th>99th</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }
    // Spectators exist only outside a focus, so an analysis without one has no floor.
    if (Array.isArray(data.noise_floor)) document.getElementById("noise").innerHTML = `<section><h2>Spectator noise floor</h2>${noiseFloor(data.noise_floor)}</section>`;

    const summary = data.track_summary || [];
    const signed = value => value == null || Number.isNaN(Number(value)) ? "—" : `${Number(value) > 0 ? "+" : ""}${fmt(value)}`;
    const observed = c => c && c.observed > 0;
    function trackSummary() {
      if (!summary.length) return '<div class="empty">No track summary in this analysis.</div>';
      const quantities = [...new Set(summary.flatMap(t => Object.keys(t.changes || {})))];
      const tables = quantities.map(name => {
        // Units differ between quantities, so tracks are ranked only within one.
        // The sort is stable: equal ranges keep track order, and null ranges go last.
        const rows = summary.map(t => [t, (t.changes || {})[name]]).filter(([, c]) => observed(c)).sort((a, b) => (b[1].range ?? -1) - (a[1].range ?? -1));
        if (!rows.length) return '';
        const body = rows.map(([t, c]) => {
          const s = c.largest_step;
          const step = s ? `${signed(s.delta)} <small class="muted">${esc(frameName(s.frame_before))}→${esc(frameName(s.frame_after))}</small>` : "—";
          return `<tr><td>${esc(t.track_id)}</td><td>${esc(t.kind)}</td><td>${esc(frameName(t.first_frame))}→${esc(frameName(t.last_frame))}<br><small class="muted">${t.spans_path ? "whole path" : "partial path"}</small></td><td>${signed(c.net_change)}</td><td>${fmt(c.range)}</td><td>${step}</td></tr>`;
        }).join('');
        return `<details open><summary>${esc(name)} · ${rows.length} track${rows.length === 1 ? '' : 's'}</summary><div class="scroll"><table><thead><tr><th>Track</th><th>Kind</th><th>Frames</th><th>Net change</th><th>Max &minus; min</th><th>Largest step</th></tr></thead><tbody>${body}</tbody></table></div></details>`;
      }).join('');
      const empty = summary.filter(t => !Object.values(t.changes || {}).some(observed)).length;
      const note = empty ? `<p class="muted">${empty} track${empty === 1 ? ' has' : 's have'} no charge, volume, or configured descriptor values.</p>` : '';
      return `<p class="muted">Reporting only, in each source tool's units; no thresholds apply. Net change compares a track's first and last frames; max &minus; min and the largest single step use the frames where the value exists. A change spread over many frames can appear here without any event. Each quantity lists its tracks by max &minus; min, largest first; quantities are not ranked against each other because their units differ.</p>${tables}${note}`;
    }
    document.getElementById("summary").innerHTML = trackSummary();

    // Atom groups sum charges and spins over sets of atoms, against a floor from
    // spectator molecules; without atom_groups or molecule_groups there is no section.
    function groupSection(g) {
      const m = g.molecules || {};
      const counts = items => Object.entries(items || {}).map(([f, n]) => `${n} ${f}`).join(", ") || "none";
      const molecules = m.count != null
        ? `<p class="muted">${esc(`Frame ${frameName(m.frame)} has ${m.count} molecule${m.count === 1 ? "" : "s"} (${counts(m.formulas)}) under the bond cutoffs of the focus rule; ${m.spectators} keep every bond and hold no focus atom (${counts(m.spectator_formulas)}).${m.reason ? ` ${m.reason}` : ""}`)}</p>`
        : `<p class="muted">${esc(m.reason || "No molecules were found.")}</p>`;
      const floor = g.noise_floor || [];
      const floorTable = floor.length ? `<div class="scroll"><table><thead><tr><th>Quantity</th><th>Source</th><th>Molecules</th><th>Steps</th><th>Median</th><th>95th</th><th>99th</th></tr></thead><tbody>${floor.map(e => `<tr><td>${esc(readable(e.quantity))}</td><td>${esc(e.producer ?? "—")}</td><td>${esc(e.molecules)}</td><td>${esc(e.steps)}</td><td>${fmt(e.median, 4)}</td><td>${fmt(e.p95, 4)}</td><td>${fmt(e.p99, 4)}</td></tr>`).join("")}</tbody></table></div>` : "";
      const groups = g.groups || [];
      if (!groups.length) return `${molecules}${floorTable}<div class="empty">No atom group was reported.</div>`;
      const quantity = groups.some(x => observed((x.changes || {}).net_charge)) ? "net_charge" : "charge";
      const ends = x => x.values.filter(v => v[quantity] != null);
      const rows = groups.map(x => {
        const c = (x.changes || {})[quantity];
        const s = c?.largest_step;
        const ratio = s?.ratio_to_floor?.p99;
        const step = s ? `${signed(s.delta)} <small class="muted">${esc(frameName(s.frame_before))}→${esc(frameName(s.frame_after))}${ratio != null ? `, ${fmt(ratio, 1)} times the spectator 99th percentile` : ""}</small>` : "—";
        const spin = (x.changes || {}).spin;
        const cut = x.cut_bonds?.length ? `<br><small class="muted">cuts ${x.cut_bonds.length} bond${x.cut_bonds.length === 1 ? "" : "s"}</small>` : "";
        const values = ends(x);
        return `<tr><td>${esc(x.group_id)}${cut}</td><td>${esc(x.formula)}</td><td>${esc(x.producer ?? "—")}</td><td>${fmt(values[0]?.[quantity])} → ${fmt(values.at(-1)?.[quantity])}</td><td>${signed(c?.net_change)}</td><td>${fmt(c?.range)}</td><td>${step}</td><td>${spin ? `${signed(spin.net_change)} <small class="muted">max &minus; min ${fmt(spin.range)}</small>` : "—"}</td></tr>`;
      }).join("");
      const label = quantity === "net_charge" ? "Net charge" : "Charge";
      return `<p class="muted">Sums of atom charges and spins over each group, in each source tool's units. A sum does not depend on the partition surfaces inside the group, only on those around it, so whole molecules move far less between frames than single atoms. Each group's largest step is compared with the 99th percentile of the spectator molecules' steps; a group that cuts a covalent bond keeps that bond's surface, whose noise is closer to single atoms'. Reporting only.</p>${molecules}${floorTable}<div class="scroll"><table><thead><tr><th>Group</th><th>Formula</th><th>Source</th><th>${label}, first → last</th><th>Net change</th><th>Max &minus; min</th><th>Largest step</th><th>Spin net change</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }
    if (data.atom_groups) document.getElementById("groups").innerHTML = `<section><h2>Atom groups</h2>${groupSection(data.atom_groups)}</section>`;

    const bonds = data.bonds || [];
    function bondTable() {
      if (!bonds.length) return '<div class="empty">No feature ties exactly two atoms in this analysis.</div>';
      const heads = {field: "Field", laplacian: "Laplacian", icohp: "ICOHP (eV)", icobi: "ICOBI"};
      const shown = Object.keys(heads).filter(q => bonds.some(b => b[q] != null));
      // A row begins or ends where the neighboring frame has no row for that bond.
      const present = new Set(bonds.map(b => `${b.bond_id}@${position(b.frame)}`));
      const last = frames.length - 1;
      const body = bonds.map(b => {
        const i = position(b.frame);
        const marks = [i > 0 && !present.has(`${b.bond_id}@${i-1}`) ? "begins" : "", i < last && !present.has(`${b.bond_id}@${i+1}`) ? "ends" : ""].filter(Boolean).join(", ");
        return `<tr><td>${esc(b.element_a)}[${esc(b.atom_a)}]–${esc(b.element_b)}[${esc(b.atom_b)}] <small class="muted">${esc(b.bond_id)}</small></td><td>${esc(frameName(b.frame))}${marks ? ` <small class="muted">${marks}</small>` : ""}</td><td>${fmt(b.distance)}</td>${shown.map(q => `<td>${fmt(b[q], 4)}</td>`).join("")}<td>${esc((b.track_ids || []).join(", "))}</td></tr>`;
      }).join("");
      return `<p class="muted">One row per atom pair, periodic image, and frame for each feature tied to exactly two atoms, such as a critic2 bond critical point or a LOBSTER bond; features on one pair and image are joined. Atoms are 0-based POSCAR indices. Distance is the minimum image unless the feature names a lattice translation. Field and Laplacian are critic2's values at the critical point in its units; ICOHP is in eV. Rows begin or end where no feature ties a pair, so a bond that forms or breaks, or a critical point that moves to another pair, starts or stops rows; rows are never joined across pairs, although one feature track can cross several.</p><div class="scroll"><table><thead><tr><th>Atoms</th><th>Frame</th><th>Distance (Å)</th>${shown.map(q => `<th>${heads[q]}</th>`).join("")}<th>Tracks</th></tr></thead><tbody>${body}</tbody></table></div>`;
    }
    document.getElementById("bonds").innerHTML = bondTable();

    const netSums = {};
    for (const d of (data.provenance || {}).descriptor_documents || []) {
      const sums = (d.metadata || {}).net_charge_sum;
      if (sums && d.frame) netSums[d.frame.index] = sums;
    }
    const hasNet = Object.keys(netSums).length > 0;
    const netCell = f => hasNet ? `<td>${Object.entries(netSums[f.index] || {}).map(([p, v]) => `${esc(p)} ${Number(v) > 0 ? "+" : ""}${fmt(v, 6)}`).join(", ") || "—"}</td>` : "";
    const cpCell = f => cpCounts.length ? `<td>${cpCounts.filter(c => c.frame === f.index).map(c => esc(c.n == null ? "—" : `${c.n}/${c.b}/${c.r}/${c.c}${c.unclassified ? ` + ${c.unclassified} unclassified` : ""} (${sumText(c.morse_sum)})`)).join(", ") || "—"}</td>` : "";
    document.getElementById("frames").innerHTML = frames.length ? `<table><thead><tr><th>Frame</th><th>Energy</th><th>Atoms</th><th>Features</th><th>Cell volume</th>${hasNet ? "<th>Net charge sum</th>" : ""}${cpCounts.length ? "<th>critic2 n/b/r/c (sum)</th>" : ""}</tr></thead><tbody>${frames.map((f,i)=>`<tr><td>${esc(f.label ?? i)}</td><td>${fmt(f.energy,6)}</td><td>${esc(f.atom_count ?? (f.atoms||[]).length)}</td><td>${esc(f.feature_count ?? (f.features||[]).length)}</td><td>${fmt(f.cell_volume,3)}</td>${netCell(f)}${cpCell(f)}</tr>`).join('')}</tbody></table>` : '<div class="empty">No frame records.</div>';

    const audit = {configuration:data.configuration||{}, provenance:data.provenance||{}, warnings:data.warnings||[]};
    document.getElementById("audit").innerHTML = `<details open><summary>Warnings (${audit.warnings.length})</summary>${audit.warnings.length?`<ul>${audit.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:'<p class="muted">No pipeline warnings.</p>'}</details><details><summary>Configuration</summary><pre>${esc(JSON.stringify(audit.configuration,null,2))}</pre></details><details><summary>Provenance</summary><pre>${esc(JSON.stringify(audit.provenance,null,2))}</pre></details><details><summary>Canonical analysis JSON</summary><pre>${esc(JSON.stringify(data,null,2))}</pre></details>`;
  </script>
</body>
</html>
"""


def render_html_report(
    analysis: Mapping[str, Any],
    output_path: str | Path,
    *,
    title: str | None = None,
) -> Path:
    """Render *analysis* as a portable HTML electronic-autopsy report.

    The output has no CDN or JavaScript package dependencies, which makes it
    suitable for offline clusters and long-term computational provenance.
    """

    project = analysis.get("project", {})
    project_name = (
        str(project.get("name", "ElecTrace analysis"))
        if isinstance(project, Mapping)
        else "ElecTrace analysis"
    )
    page_title = title or f"{project_name} · ElecTrace"
    payload = _strict_json_dumps(
        analysis,
        context="HTML report analysis JSON",
        separators=(",", ":"),
        sort_keys=True,
    ).replace("<", "\\u003c")
    document = (
        _HTML.replace("__TITLE__", _escape_html(page_title))
        .replace("__HEADING__", _escape_html(project_name))
        .replace("__DATA__", payload)
    )
    output = Path(output_path)
    atomic_write_text(output, document)
    return output


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
