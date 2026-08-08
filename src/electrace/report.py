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
    .empty { color: var(--muted); padding: 2rem 0; text-align: center; }
    .timeline { display: grid; gap: .45rem; }
    .event { display: grid; grid-template-columns: 6.5rem 7rem minmax(0, 1fr) 5.5rem; gap: .7rem; align-items: center; border-top: 1px solid var(--line); padding: .75rem .2rem; }
    .event:first-child { border-top: 0; }
    .badge { border: 1px solid currentColor; border-radius: 999px; padding: .2rem .55rem; width: fit-content; font: 700 .7rem ui-monospace, monospace; text-transform: uppercase; }
    .birth { color: var(--green); } .death { color: var(--red); } .split, .merge { color: var(--violet); } .change, .descriptor_change { color: var(--amber); }
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
    <div class="grid">
      <section><h2>Energy profile</h2><div id="energy"></div></section>
      <section><h2>Event composition</h2><div id="composition"></div></section>
    </div>
    <section><h2>Electronic event timeline</h2><div class="timeline" id="timeline"></div></section>
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

    function energyChart() {
      const points = frames.map((f,i) => ({x:i, y:f.energy == null ? null : Number(f.energy), label:f.label ?? String(i)})).filter(p => Number.isFinite(p.y));
      if (!points.length) return '<div class="empty">No energies were discovered.</div>';
      const width=720, height=270, left=56, right=18, top=18, bottom=42;
      const minY=Math.min(...points.map(p=>p.y)), maxY=Math.max(...points.map(p=>p.y));
      const spanY=Math.max(maxY-minY, 1e-9), maxX=Math.max(frames.length-1,1);
      const X=p=>left+(p.x/maxX)*(width-left-right), Y=p=>top+(maxY-p.y)/spanY*(height-top-bottom);
      const path=points.map((p,i)=>`${i?'L':'M'} ${X(p).toFixed(1)} ${Y(p).toFixed(1)}`).join(' ');
      const dots=points.map(p=>`<circle class="energy-point" cx="${X(p)}" cy="${Y(p)}" r="4"><title>${esc(p.label)}: ${fmt(p.y,6)}</title></circle>`).join('');
      return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Energy profile"><line class="axis" x1="${left}" y1="${height-bottom}" x2="${width-right}" y2="${height-bottom}"/><line class="axis" x1="${left}" y1="${top}" x2="${left}" y2="${height-bottom}"/><text class="chart-label" x="4" y="${top+6}">${fmt(maxY)}</text><text class="chart-label" x="4" y="${height-bottom}">${fmt(minY)}</text><text class="chart-label" x="${left}" y="${height-10}">first frame</text><text class="chart-label" text-anchor="end" x="${width-right}" y="${height-10}">last frame</text><path class="energy-line" d="${path}"/>${dots}</svg>`;
    }
    document.getElementById("energy").innerHTML = energyChart();

    const counts = events.reduce((acc,e)=>{const k=String(e.type||"unknown").toLowerCase();acc[k]=(acc[k]||0)+1;return acc;},{});
    const maxCount = Math.max(1,...Object.values(counts));
    document.getElementById("composition").innerHTML = Object.keys(counts).length ? Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([k,n])=>`<div style="margin:.6rem 0"><span class="badge ${esc(k)}">${esc(k)}</span><div class="confidence"><i style="width:${100*n/maxCount}%"></i></div><small class="muted">${n} event${n===1?'':'s'}</small></div>`).join('') : '<div class="empty">No events met the configured thresholds.</div>';

    document.getElementById("timeline").innerHTML = events.length ? events.map(e=>{
      const type=String(e.type||"change").toLowerCase(), confidence=Math.max(0,Math.min(1,Number(e.confidence||0)));
      const confidenceLabel=String(e.confidence_label||"unclassified").toLowerCase();
      const span=e.end_frame!=null && e.end_frame!==e.start_frame ? `${e.start_frame}→${e.end_frame}` : String(e.start_frame ?? "—");
      return `<div class="event"><span class="badge ${esc(type)}">${esc(type)}</span><span class="muted">frame ${esc(span)}</span><div><div>${esc(e.description || `${type} event`)}</div><div class="confidence"><i style="width:${confidence*100}%"></i></div></div><span>${esc(confidenceLabel)} ${fmt(confidence,2)}</span></div>`;
    }).join('') : '<div class="empty">No persistent electronic events were detected.</div>';

    document.getElementById("frames").innerHTML = frames.length ? `<table><thead><tr><th>Frame</th><th>Energy</th><th>Atoms</th><th>Features</th><th>Cell volume</th></tr></thead><tbody>${frames.map((f,i)=>`<tr><td>${esc(f.label ?? i)}</td><td>${fmt(f.energy,6)}</td><td>${esc(f.atom_count ?? (f.atoms||[]).length)}</td><td>${esc(f.feature_count ?? (f.features||[]).length)}</td><td>${fmt(f.cell_volume,3)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">No frame records.</div>';

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
