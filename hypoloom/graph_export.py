"""Offline, non-authoritative graph views of accepted research and controls."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

from .observation_api import build_product_observation


def graph_html(run_id: str, graph: dict, observed_at: str) -> str:
    def escape(value):
        return html.escape(str(value), quote=True)
    nodes, edges = graph["nodes"], graph["edges"]
    roles = sorted({str(node.get("proposer_role") or "unknown") for node in nodes})
    colors = ("#267a67", "#3462a5", "#8b54a1", "#a86229", "#9c4457", "#4c7884")
    palette = {role: colors[index % len(colors)] for index, role in enumerate(roles)}
    positions, counts = {}, dict.fromkeys(roles, 0)
    for node in nodes:
        role = str(node.get("proposer_role") or "unknown")
        positions[node["hypothesis_id"]] = (40 + roles.index(role) * 340, 85 + counts[role] * 130)
        counts[role] += 1
    width, height = max(720, len(roles) * 340 + 40), max(230, max(counts.values(), default=0) * 130 + 100)
    parts = [f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f'<title>Research graph — {escape(run_id)}</title><style>'
        'body{font:15px system-ui;background:#f5f3ed;color:#26342e;margin:24px}h1{font-size:25px}'
        'section{overflow:auto;background:white;border:1px solid #d7ddd7;border-radius:12px}'
        'svg text{font:13px system-ui}table{border-collapse:collapse;width:100%;margin-top:24px}'
        'td,th{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #d7ddd7}'
        'td{overflow-wrap:anywhere}small{color:#53675b}</style>'
        f'<h1>Research graph · {escape(run_id)}</h1><p>{len(nodes)} hypotheses · {len(edges)} relations</p>'
        f'<p><small>Observed {escape(observed_at)}. Offline derived view; not workflow authority or a liveness claim.</small></p>'
        f'<section aria-label="Hypothesis graph"><svg width="{width}" height="{height}" role="img" '
        'aria-label="Expert columns with hypothesis states and directed relationships">'
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M0 0 L10 5 L0 10 z" fill="#61756a"/></marker></defs>']
    for index, role in enumerate(roles):
        parts.append(f'<text x="{40 + index * 340}" y="40" fill="{palette[role]}">{escape(role)}</text>')
    for edge in edges:
        if edge["source"] not in positions or edge["target"] not in positions: continue
        x1, y1 = positions[edge["source"]]; x2, y2 = positions[edge["target"]]
        parts.append(f'<path d="M{x1 + 140} {y1 + 90} L{x2 + 140} {y2}" stroke="#61756a" fill="none" marker-end="url(#arrow)">'
                     f'<title>{escape(edge["source"])} → {escape(edge["target"])}: {escape(edge["relation"])}</title></path>')
    for node in nodes:
        x, y = positions[node["hypothesis_id"]]
        role = str(node.get("proposer_role") or "unknown")
        state = str(node.get("state") or "PROPOSED")
        dash = ' stroke-dasharray="6 4"' if state in {"REFUTED", "PRUNED", "INCONCLUSIVE"} else ""
        parts.append(f'<g><title>{escape(node.get("claim", ""))}</title>'
            f'<rect x="{x}" y="{y}" width="280" height="90" rx="10" fill="#fafcf9" stroke="{palette[role]}" stroke-width="2"{dash}/>'
            f'<text x="{x + 12}" y="{y + 25}">{escape(node["hypothesis_id"][:32])}</text>'
            f'<text x="{x + 12}" y="{y + 49}" fill="{palette[role]}">{escape(state)}</text>'
            f'<text x="{x + 12}" y="{y + 72}">{escape(str(node.get("claim", ""))[:32])}</text></g>')
    if not nodes: parts.append('<text x="40" y="100">No accepted hypotheses yet.</text>')
    parts.append('</svg></section><h2>Exact records</h2><table><thead><tr><th>Hypothesis</th><th>Expert / state</th><th>Claim</th></tr></thead><tbody>')
    for node in nodes:
        parts.append(f'<tr><td>{escape(node["hypothesis_id"])}</td><td>{escape(node.get("proposer_role"))} / {escape(node.get("state"))}</td>'
                     f'<td>{escape(node.get("claim", ""))}</td></tr>')
    parts.append('</tbody></table><h2>Relations</h2><ul>')
    parts.extend(f'<li>{escape(edge["source"])} → {escape(edge["target"])} ({escape(edge["relation"])})</li>' for edge in edges)
    return "".join(parts) + '</ul></html>\n'


def export_graph(state_root: str | Path, run_id: str, destination: str | Path) -> dict:
    root, target = Path(state_root).resolve(), Path(destination).absolute()
    if target.resolve().is_relative_to(root):
        raise ValueError("graph export must be outside the state root")
    if target.suffix.lower() not in {".json", ".html"}:
        raise ValueError("graph export extension must be .html or .json")
    observation = build_product_observation(root, run_id)
    graph = observation["graph"]
    value = {"run_id": run_id, "observed_at": observation["observed_at"], "graph": graph, "non_authoritative": True}
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n" if target.suffix.lower() == ".json" else graph_html(run_id, graph, observation["observed_at"])
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    return {"run_id": run_id, "path": str(target), "nodes": len(graph["nodes"]), "edges": len(graph["edges"]), "non_authoritative": True}
