#!/usr/bin/env python3
"""Rebuild index.html from site/template.html + the diagram images in architecture/.

Run after adding a diagram or editing the template:
    python3 site/build_site.py

Adding a diagram for one of the pending agents:
    export it as PNG (or SVG/JPG), name it with the slug listed in DIAGRAMS below,
    drop it into architecture/, e.g.
        architecture/agent-assist-architecture.png
    then re-run this script. The tab turns green automatically.

Images only — .drawio files are NOT rendered. The converter in drawio2svg.py is kept
in the folder for reference but is no longer wired into the build.
"""
import base64
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCH = os.path.join(ROOT, 'architecture')

# (tab label, file slug, caption shown in the viewer bar)
DIAGRAMS = [
    ('Combined overview', 'knowledge-predictive-architecture',
     'All agents on the shared platform layer — ingress, PII tier, grounding, trust rail'),
    ('Knowledge Agent', 'knowledge-agent-architecture',
     'Ingress, de-identification tier, serve &amp; curate loops, groundedness critic, ingestion'),
    ('Predictive Agent', 'predictive-agent-architecture',
     'Signal sources, classical ML plane, Durable Functions, tool set, delivery'),
    ('Agent Assist', 'agent-assist-architecture',
     'ITSM console integration, ticket-context query, citation rendering'),
    ('Voice Agent', 'voice-agent-architecture',
     'ACS voice channel, deterministic identity verification, closed-set action policy'),
    ('Conversational Agent', 'conversational-agent-architecture',
     'Teams / portal self-service, grounded answers with source links, ticket fallback'),
    ('RCA Agent', 'rca-agent-architecture',
     'Evidence gathering, timeline &amp; change correlation, approved RCA → knowledge.ingest'),
    ('DC Ops Agent', 'dcops-agent-architecture',
     'DC playbook lookup, capacity forecasting, prediction outcome feedback'),
]

# Coverage table rows. The Diagram column is filled in from the files actually
# present, so the table can never contradict the diagram viewer.
# (agent, slug, phase, detailed spec, endpoints, autonomy today)
COVERAGE = [
    ('Knowledge', 'knowledge-agent-architecture', '1–2', 'yes', 'yes',
     'Advisory / draft-only'),
    ('Predictive', 'predictive-agent-architecture', '2', 'yes', 'yes',
     'Advisory (level 1, all actions)'),
    ('Agent Assist', 'agent-assist-architecture', '1', '—', '✓ consumer',
     'Suggest'),
    ('Voice', 'voice-agent-architecture', '2', '✓ policy', '✓ consumer',
     'Auto-execute, closed set'),
    ('Conversational', 'conversational-agent-architecture', '2', '—', '✓ consumer',
     'Advisory'),
    ('RCA', 'rca-agent-architecture', '3', '—', '✓ consumer', 'Draft only'),
    ('DC Ops', 'dcops-agent-architecture', '3', '—', '✓ consumer', 'Advisory'),
]

EXTS = ('.png', '.svg', '.jpg', '.jpeg')


def find(slug):
    """Return (path, ext) for the first matching image, or (None, None)."""
    for ext in EXTS:
        p = os.path.join(ARCH, slug + ext)
        if os.path.exists(p):
            return p, ext
    return None, None


def graphic(path, ext):
    """Return inline HTML for the diagram, embedded so index.html stays self-contained."""
    if ext == '.svg':
        svg = open(path, encoding='utf-8').read()
        svg = re.sub(r'<\?xml[^>]*\?>', '', svg)
        svg = re.sub(r'<!DOCTYPE[^>]*>', '', svg)
        return re.sub(r'(<svg[^>]*?)\swidth="[\d.]+(?:px)?"\sheight="[\d.]+(?:px)?"',
                      r'\1', svg, count=1).strip()
    mime = 'image/png' if ext == '.png' else 'image/jpeg'
    b64 = base64.b64encode(open(path, 'rb').read()).decode()
    return f'<img alt="architecture diagram" src="data:{mime};base64,{b64}">'


def pending_pane(label, slug):
    return f'''<div class="pending">
        <span class="badge">Diagram slot reserved</span>
        <h4>{label} — architecture diagram not added yet</h4>
        <p>Everything else for this agent is on the page: its role, autonomy level, the endpoints
           it calls and where it lands in the phase plan. Only the picture is outstanding.</p>
        <div class="drop">
          <b># drop the exported image here</b><br>
          architecture/<u>{slug}</u>.png&nbsp;&nbsp;<span style="opacity:.55"># or .svg / .jpg</span><br>
          <b># then rebuild</b><br>
          python3 site/build_site.py
        </div>
      </div>'''


def main():
    resolved = [(label, slug, caption) + find(slug) for label, slug, caption in DIAGRAMS]

    # open on the first tab that actually has a diagram, so the section never
    # loads showing an empty placeholder
    default = next((i for i, r in enumerate(resolved) if r[3]), 0)

    tabs, panes = [], []
    found = 0
    for i, (label, slug, caption, path, ext) in enumerate(resolved):
        pid = f'd{i}'
        on = ' on' if i == default else ''
        bul = 'bul' if path else 'bul pend'
        tabs.append(f'<button class="tab{on}" data-p="{pid}">'
                    f'<span class="{bul}"></span>{label}</button>')

        if path:
            found += 1
            body = f'<div class="stage"><div class="zoomer">{graphic(path, ext)}</div></div>'
            controls = ('<span class="sp">'
                        '<button class="vb" data-z="-" title="Zoom out">−</button>'
                        '<button class="vb w" data-z="fit">Fit</button>'
                        '<button class="vb" data-z="+" title="Zoom in">+</button>'
                        '<button class="vb w" data-z="one">1:1</button>'
                        '<button class="vb w" data-z="full">Full screen</button></span>')
            print(f'  [x] {label:24s} <- {os.path.basename(path)}')
        else:
            body = pending_pane(label, slug)
            controls = '<span class="sp"><span class="tag">Pending</span></span>'
            print(f'  [ ] {label:24s} -- no file, placeholder rendered')

        panes.append(
            f'<div class="dpane{on}" id="{pid}"><div class="viewer">'
            f'<div class="vbar"><span class="t">{caption}</span>{controls}</div>'
            f'{body}</div></div>')

    # coverage table — Diagram column derived from the files on disk
    def cell(v):
        if v == 'yes':
            return '<td class="yes">✓</td>'
        if v == 'no':
            return '<td class="no">pending</td>'
        cls = ' class="yes"' if v.startswith('✓') else ' class="no"'
        return f'<td{cls}>{v}</td>'

    rows = []
    for agent, slug, phase, spec, eps, autonomy in COVERAGE:
        has = 'yes' if find(slug)[0] else 'no'
        rows.append(f'<tr><td>{agent}</td><td>{phase}</td>{cell(spec)}{cell(eps)}'
                    f'{cell(has)}<td>{autonomy}</td></tr>')

    html = open(os.path.join(ROOT, 'site', 'template.html'), encoding='utf-8').read()
    html = html.replace('__DIAGRAM_TABS__', '\n      '.join(tabs))
    html = html.replace('__DIAGRAM_PANES__', '\n    '.join(panes))
    html = html.replace('__COVERAGE_ROWS__', '\n      '.join(rows))
    html = html.replace('__DIAGRAM_COUNT__', f'{found} of {len(DIAGRAMS)}')
    assert '__DIAGRAM' not in html and '__COVERAGE' not in html, 'unreplaced placeholder'

    out = os.path.join(ROOT, 'index.html')
    open(out, 'w', encoding='utf-8').write(html)
    print(f'\n{found}/{len(DIAGRAMS)} diagrams present')
    print(f'wrote {out} ({len(html):,} bytes)')


if __name__ == '__main__':
    main()
