"""Interactive SVG graph for the Version Evolution Explorer.

A Streamlit Components v2 component (built into Streamlit 1.63: no extra
dependency, no CDN, no network). Python computes every node position
(stable grid, see qch.explorer.graph) and hands over a JSON payload
(evolution_explorer_ui.build_graph_payload); the JavaScript below only draws
it and reports clicks back:

    click a node     -> {"type": "node",   "id": <version_id>}
    click an edge    -> {"type": "edge",   "id": <edge_id>}     (wide invisible hit area)
    double-click     -> {"type": "expand", "id": <version_id>}  (node)
    Enter on a focused node or edge selects it too (keyboard access).

All labels are written with textContent / setAttribute -- never innerHTML --
because node text comes from the database and is untrusted data.
The component code itself is fixed, trusted text in this file.
"""

from __future__ import annotations

from typing import Any, Callable

import streamlit as st
from streamlit.components.v2 import get_bidi_component_manager

CSS = """
.vee-root { font-family: var(--st-font, system-ui, sans-serif); color: var(--st-text-color, #24292f); }
.vee-legend { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 16px; margin: 0 0 8px 0; align-items: center; }
.vee-legend span.item { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.vee-swatch { width: 14px; height: 14px; border-radius: 3px; border: 2px solid; box-sizing: border-box; display: inline-block; }
.vee-scroll { overflow: auto; max-height: 680px; border: 1px solid rgba(128,128,128,.35); border-radius: 6px; background: var(--st-background-color, transparent); }
svg.vee-svg { display: block; margin: 0 auto; }
.vee-node { cursor: pointer; outline: none; }
.vee-node:focus .vee-node-box, .vee-node:hover .vee-node-box { stroke-width: 3.5; }
.vee-node text { pointer-events: none; fill: var(--st-text-color, #24292f); }
.vee-edge { cursor: pointer; outline: none; }
.vee-edge:hover .vee-edge-line, .vee-edge:focus .vee-edge-line { stroke-width: 3.6; }
.vee-empty { padding: 18px; font-size: 16px; color: #3a424a; }
"""

JS = r"""
export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const SVGNS = 'http://www.w3.org/2000/svg';
  const root = parentElement.querySelector('.vee-root');
  if (!root) { return; }
  const old = root.querySelector('.vee-scroll');
  const keep = old ? { left: old.scrollLeft, top: old.scrollTop } : null;   // a rerun must not jump the viewport
  root.textContent = '';

  const el = (tag, attrs, text) => {
    const node = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) { node.setAttribute(k, String(v)); }
    if (text !== undefined) { node.textContent = text; }
    return node;
  };
  const html = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  };
  const DASH = { solid: '', dashed: '9 6', dotted: '2 5', dashdot: '10 4 2 4' };

  // -- legend (HTML, text only) ----------------------------------------------------
  const legend = html('div', 'vee-legend');
  legend.setAttribute('aria-label', 'Graph legend');
  for (const s of data.legend.statuses) {
    const item = html('span', 'item');
    const sw = html('span', 'vee-swatch');
    sw.style.borderColor = s.color; sw.style.background = s.color + '33';
    item.appendChild(sw); item.appendChild(document.createTextNode(s.text));
    legend.appendChild(item);
  }
  const origin = html('span', 'item');
  const osw = html('span', 'vee-swatch'); osw.style.borderColor = 'currentColor'; osw.style.outline = '2px solid currentColor'; osw.style.outlineOffset = '1px';
  origin.appendChild(osw); origin.appendChild(document.createTextNode(data.legend.origin));
  legend.appendChild(origin);
  for (const r of data.legend.relations) {
    const item = html('span', 'item');
    const line = document.createElementNS(SVGNS, 'svg');
    line.setAttribute('width', '34'); line.setAttribute('height', '12'); line.setAttribute('aria-hidden', 'true');
    const path = el('line', { x1: 1, y1: 6, x2: 27, y2: 6, 'stroke-width': 2.5 });
    path.style.stroke = r.color; if (DASH[r.dash]) { path.setAttribute('stroke-dasharray', DASH[r.dash]); }
    line.appendChild(path);
    const head = el('polygon', { points: '27,2 34,6 27,10' }); head.style.fill = r.color;
    line.appendChild(head);
    item.appendChild(line); item.appendChild(document.createTextNode(r.label));
    legend.appendChild(item);
  }
  root.appendChild(legend);

  if (!data.nodes.length) { root.appendChild(html('div', 'vee-empty', 'Nothing to draw.')); return; }

  // -- canvas ------------------------------------------------------------------------
  const scroll = html('div', 'vee-scroll');
  const svg = el('svg', { class: 'vee-svg', width: data.width, height: data.height, viewBox: `0 0 ${data.width} ${data.height}`, role: 'group', 'aria-label': 'Version neighborhood graph' });
  const defs = el('defs');
  const markerIds = {};
  const marker = (color) => {
    if (!markerIds[color]) {
      const id = 'veeArrow' + Object.keys(markerIds).length;
      const m = el('marker', { id, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, orient: 'auto-start-reverse' });
      const p = el('path', { d: 'M 0 0 L 10 5 L 0 10 z' }); p.style.fill = color;
      m.appendChild(p); defs.appendChild(m); markerIds[color] = id;
    }
    return markerIds[color];
  };
  svg.appendChild(defs);

  const W = data.node_w, H = data.node_h;
  const byId = {};
  for (const n of data.nodes) { byId[n.id] = n; }
  const center = (n) => ({ x: n.x + W / 2, y: n.y + H / 2 });
  const clip = (c, toward) => {           // where the segment from c toward `toward` leaves the node rectangle
    const dx = toward.x - c.x, dy = toward.y - c.y;
    if (dx === 0 && dy === 0) { return c; }
    const t = Math.min((W / 2) / Math.abs(dx || 1e-9), (H / 2) / Math.abs(dy || 1e-9));
    return { x: c.x + dx * t, y: c.y + dy * t };
  };
  const send = (type, id) => setTriggerValue('selection', { type, id, nonce: Date.now() });

  // edges first (under the nodes)
  for (const e of data.edges) {
    const a = byId[e.source], b = byId[e.target];
    if (!a || !b) { continue; }
    const g = el('g', { class: 'vee-edge', tabindex: 0, role: 'button', 'aria-label': `Edge ${e.relation} from ${a.label} to ${b.label}` });
    g.appendChild(el('title', {}, e.title));
    let d, mid;
    if (e.source === e.target) {                                   // self-link: a small loop above the node
      const c = center(a);
      d = `M ${c.x - 14} ${a.y} C ${c.x - 40} ${a.y - 44}, ${c.x + 40} ${a.y - 44}, ${c.x + 14} ${a.y}`;
    } else {
      const ca = center(a), cb = center(b);
      const offset = (e.slot - (e.slots - 1) / 2) * 34;           // parallel edges between the same pair fan out
      const len = Math.hypot(cb.x - ca.x, cb.y - ca.y) || 1;
      const nx = -(cb.y - ca.y) / len, ny = (cb.x - ca.x) / len;
      const cx = (ca.x + cb.x) / 2 + nx * offset * 2, cy = (ca.y + cb.y) / 2 + ny * offset * 2;
      const p1 = clip(ca, { x: cx, y: cy }), p2 = clip(cb, { x: cx, y: cy });
      d = e.slots === 1 ? `M ${p1.x} ${p1.y} L ${p2.x} ${p2.y}` : `M ${p1.x} ${p1.y} Q ${cx} ${cy} ${p2.x} ${p2.y}`;
    }
    const hit = el('path', { d, fill: 'none', stroke: 'transparent', 'stroke-width': 24 });
    const line = el('path', { d, fill: 'none', class: 'vee-edge-line', 'stroke-width': e.selected ? 4 : 2.2, 'marker-end': `url(#${marker(e.color)})` });
    line.style.stroke = e.selected ? '#0969da' : e.color;
    if (DASH[e.dash]) { line.setAttribute('stroke-dasharray', DASH[e.dash]); }
    g.appendChild(hit); g.appendChild(line);
    g.addEventListener('click', (ev) => { ev.stopPropagation(); send('edge', e.id); });
    g.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); send('edge', e.id); } });
    svg.appendChild(g);
  }

  for (const n of data.nodes) {
    const g = el('g', { class: 'vee-node', 'data-node-id': n.id, tabindex: 0, role: 'button', transform: `translate(${n.x} ${n.y})`, 'aria-label': `Version ${n.label}, ${n.status}${n.origin ? ', searched version' : ''}${n.selected ? ', selected' : ''}` });
    g.appendChild(el('title', {}, n.title));
    if (n.origin) {                                                // searched version: double outline
      const ring = el('rect', { x: -5, y: -5, width: W + 10, height: H + 10, rx: 12, fill: 'none', 'stroke-width': 2 });
      ring.style.stroke = 'var(--st-text-color, #24292f)';
      g.appendChild(ring);
    }
    const box = el('rect', { class: 'vee-node-box', x: 0, y: 0, width: W, height: H, rx: 8, 'stroke-width': n.selected ? 4 : 2.5 });
    box.style.stroke = n.selected ? '#0969da' : n.color; box.style.fill = n.color; box.style.fillOpacity = n.selected ? '0.32' : '0.17';
    g.appendChild(box);
    g.appendChild(el('text', { x: W / 2, y: 25, 'text-anchor': 'middle', 'font-size': 17, 'font-weight': 600 }, n.label));
    g.appendChild(el('text', { x: W / 2, y: 47, 'text-anchor': 'middle', 'font-size': 16 }, n.status));
    g.addEventListener('click', (ev) => { ev.stopPropagation(); send('node', n.id); });
    g.addEventListener('dblclick', (ev) => { ev.stopPropagation(); send('expand', n.id); });
    g.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); send('node', n.id); } });
    svg.appendChild(g);
  }
  scroll.appendChild(svg);
  root.appendChild(scroll);
  if (keep) { scroll.scrollLeft = keep.left; scroll.scrollTop = keep.top; }
  // bring a requested node into view, once per request (focus.seq changes only when Python asks, e.g. after an expansion)
  const focus = data.focus;
  if (focus && root.dataset.focusSeq !== String(focus.seq)) {
    root.dataset.focusSeq = String(focus.seq);
    const target = byId[focus.id];
    if (target) {
      requestAnimationFrame(() => {
        scroll.scrollLeft = Math.max(0, target.x + W / 2 - scroll.clientWidth / 2);
        scroll.scrollTop = Math.max(0, target.y + H / 2 - scroll.clientHeight / 2);
      });
    }
  }
}
"""

HTML = '<div class="vee-root" role="region" aria-label="Version evolution graph"></div>'

COMPONENT_NAME = "qch_version_evolution_graph"
_renderer = None


def _mount():
    """The component's mount command. Registered on demand: a component lives in the registry of the Streamlit
    RUNTIME, so a second runtime in the same process (a test run, a reload) has an empty registry even though this
    module is already imported. The check keeps a normal server at exactly one registration."""
    global _renderer
    if _renderer is None or get_bidi_component_manager().get(COMPONENT_NAME) is None:
        _renderer = st.components.v2.component(COMPONENT_NAME, html=HTML, css=CSS, js=JS)
    return _renderer


def version_graph(payload: dict[str, Any], *, key: str, on_selection_change: Callable[[], None]) -> Any:
    """Mounts the graph. The last click is available as `st.session_state[key].selection`
    (a dict with 'type', 'id' and 'nonce') inside `on_selection_change`, which runs before the next script run."""
    return _mount()(data=payload, key=key, on_selection_change=on_selection_change)
