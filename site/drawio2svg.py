#!/usr/bin/env python3
"""Minimal draw.io -> SVG converter for the Coles architecture diagrams.
Supports: rounded/plain rects, text-only labels, fill/stroke colours,
fontSize/fontStyle/align/verticalAlign/spacingLeft, dashed strokes,
orthogonal edges with waypoints + edge labels.
"""
import html as htmlmod
import re
import sys
from xml.etree import ElementTree as ET

CHAR_W = 0.545  # avg char width factor vs font size (Helvetica-ish)


def parse_style(s):
    d = {}
    if not s:
        return d
    for tok in s.split(';'):
        if not tok:
            continue
        if '=' in tok:
            k, v = tok.split('=', 1)
            d[k] = v
        else:
            d[tok] = '1'
    return d


def clean_text(v):
    if not v:
        return []
    v = v.replace('&nbsp;', ' ')
    v = re.sub(r'<br\s*/?>', '\n', v, flags=re.I)
    v = re.sub(r'<[^>]+>', '', v)
    v = htmlmod.unescape(v)
    return v.split('\n')


def wrap(lines, width, fs):
    if width <= 0:
        return lines
    maxc = max(4, int(width / (fs * CHAR_W)))
    out = []
    for ln in lines:
        if len(ln) <= maxc:
            out.append(ln)
            continue
        words, cur = ln.split(' '), ''
        for w in words:
            trial = (cur + ' ' + w).strip()
            if len(trial) <= maxc:
                cur = trial
            else:
                if cur:
                    out.append(cur)
                cur = w
        if cur:
            out.append(cur)
    return out


def esc(t):
    return (t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def text_block(lines, x, y, w, h, st, default_fs=12):
    fs = float(st.get('fontSize', default_fs))
    fstyle = int(st.get('fontStyle', 0) or 0)
    weight = 'bold' if fstyle & 1 else 'normal'
    italic = 'italic' if fstyle & 2 else 'normal'
    align = st.get('align', 'center')
    valign = st.get('verticalAlign', 'middle')
    spl = float(st.get('spacingLeft', 0) or 0)
    color = st.get('fontColor', '#1f2430')
    lines = wrap(lines, w - 12 - spl, fs)
    lh = fs * 1.25
    total = lh * len(lines)
    if valign == 'top':
        ty = y + 6 + fs
    elif valign == 'bottom':
        ty = y + h - total + fs
    else:
        ty = y + h / 2 - total / 2 + fs * 0.95
    if align == 'left':
        tx, anchor = x + 6 + spl, 'start'
    elif align == 'right':
        tx, anchor = x + w - 6, 'end'
    else:
        tx, anchor = x + w / 2 + spl / 2, 'middle'
    out = []
    for i, ln in enumerate(lines):
        out.append(
            f'<text x="{tx:.1f}" y="{ty + i * lh:.1f}" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="{fs}" font-weight="{weight}" font-style="{italic}" fill="{color}" '
            f'text-anchor="{anchor}">{esc(ln)}</text>')
    return out


def anchor_pair(a, b):
    """Return (start, end) points on box edges facing each other."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    acx, acy = ax + aw / 2, ay + ah / 2
    bcx, bcy = bx + bw / 2, by + bh / 2
    ox = min(ax + aw, bx + bw) - max(ax, bx)
    oy = min(ay + ah, by + bh) - max(ay, by)
    if oy > 0 and ox <= 0:  # side by side
        if bcx > acx:
            return (ax + aw, acy), (bx, bcy)
        return (ax, acy), (bx + bw, bcy)
    if bcy > acy:
        return (acx, ay + ah), (bcx, by)
    return (acx, ay), (bcx, by + bh)


def route(p0, p1, pts):
    path = [p0]
    cur = p0
    for p in pts:
        # orthogonal step to waypoint
        if abs(p[0] - cur[0]) > 0.5 and abs(p[1] - cur[1]) > 0.5:
            path.append((cur[0], p[1]))
        path.append(p)
        cur = p
    if abs(p1[0] - cur[0]) > 0.5 and abs(p1[1] - cur[1]) > 0.5:
        if pts:
            path.append((p1[0], cur[1]))
        else:
            path.append((cur[0], (cur[1] + p1[1]) / 2))
            path.append((p1[0], (cur[1] + p1[1]) / 2))
    path.append(p1)
    return path


def rounded_path(pts, r=8):
    if len(pts) < 3:
        return 'M ' + ' L '.join(f'{x:.1f} {y:.1f}' for x, y in pts)
    d = [f'M {pts[0][0]:.1f} {pts[0][1]:.1f}']
    for i in range(1, len(pts) - 1):
        px, py = pts[i - 1]
        cx, cy = pts[i]
        nx, ny = pts[i + 1]
        d1 = ((cx - px) ** 2 + (cy - py) ** 2) ** .5
        d2 = ((nx - cx) ** 2 + (ny - cy) ** 2) ** .5
        rr = min(r, d1 / 2, d2 / 2)
        if rr < 1:
            d.append(f'L {cx:.1f} {cy:.1f}')
            continue
        ux, uy = (cx - px) / (d1 or 1), (cy - py) / (d1 or 1)
        vx, vy = (nx - cx) / (d2 or 1), (ny - cy) / (d2 or 1)
        d.append(f'L {cx - ux * rr:.1f} {cy - uy * rr:.1f}')
        d.append(f'Q {cx:.1f} {cy:.1f} {cx + vx * rr:.1f} {cy + vy * rr:.1f}')
    d.append(f'L {pts[-1][0]:.1f} {pts[-1][1]:.1f}')
    return ' '.join(d)


def convert(path):
    root = ET.parse(path).getroot()
    model = root.iter('mxGraphModel').__next__()
    cells = {}
    order = []
    for c in model.iter('mxCell'):
        cid = c.get('id')
        cells[cid] = c
        order.append(cid)

    def geom(c):
        g = c.find('mxGeometry')
        if g is None:
            return None
        return (float(g.get('x', 0)), float(g.get('y', 0)),
                float(g.get('width', 0)), float(g.get('height', 0)))

    verts, edges = [], []
    for cid in order:
        c = cells[cid]
        if c.get('vertex') == '1':
            verts.append(c)
        elif c.get('edge') == '1':
            edges.append(c)

    xs, ys = [], []
    for c in verts:
        g = geom(c)
        if g:
            xs += [g[0], g[0] + g[2]]
            ys += [g[1], g[1] + g[3]]
    pad = 20
    minx, miny = min(xs) - pad, min(ys) - pad
    W, H = max(xs) - minx + pad, max(ys) - miny + pad

    body = []
    # container-ish vertices (large, verticalAlign=top) drawn first
    def area(c):
        g = geom(c) or (0, 0, 0, 0)
        return g[2] * g[3]
    verts_sorted = sorted(verts, key=lambda c: -area(c))

    for c in verts_sorted:
        st = parse_style(c.get('style'))
        g = geom(c)
        if not g:
            continue
        x, y, w, h = g[0] - minx, g[1] - miny, g[2], g[3]
        lines = clean_text(c.get('value'))
        if st.get('shape') == 'note' and not ''.join(lines).strip():
            continue  # empty sticky note left in the source file
        if 'text' in st and 'fillColor' not in st:
            body += text_block(lines, x, y, w, h, st)
            continue
        fill = st.get('fillColor', '#ffffff')
        if fill == 'none':
            fill = 'none'
        stroke = st.get('strokeColor', '#8a94a6')
        rx = 8 if st.get('rounded') == '1' else 0
        dash = ' stroke-dasharray="6 4"' if st.get('dashed') == '1' else ''
        body.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"{dash}/>')
        body += text_block(lines, x, y, w, h, st)

    for e in edges:
        st = parse_style(e.get('style'))
        s = cells.get(e.get('source'))
        t = cells.get(e.get('target'))
        if s is None or t is None:
            continue
        gs, gt = geom(s), geom(t)
        if not gs or not gt:
            continue
        a = (gs[0] - minx, gs[1] - miny, gs[2], gs[3])
        b = (gt[0] - minx, gt[1] - miny, gt[2], gt[3])
        g = e.find('mxGeometry')
        pts = []
        if g is not None:
            arr = g.find('Array')
            if arr is not None:
                for p in arr.findall('mxPoint'):
                    if p.get('x') is not None and p.get('y') is not None:
                        pts.append((float(p.get('x')) - minx, float(p.get('y')) - miny))
        p0, p1 = anchor_pair(a, b)
        if pts:
            # re-anchor toward first/last waypoint
            def face(box, p):
                bx, by, bw, bh = box
                cx, cy = bx + bw / 2, by + bh / 2
                if abs(p[0] - cx) > abs(p[1] - cy) * 1.2:
                    return (bx + bw, cy) if p[0] > cx else (bx, cy)
                return (cx, by + bh) if p[1] > cy else (cx, by)
            p0 = face(a, pts[0])
            p1 = face(b, pts[-1])
        poly = route(p0, p1, pts)
        stroke = st.get('strokeColor', '#666666')
        dash = ' stroke-dasharray="5 4"' if st.get('dashed') == '1' else ''
        body.append(
            f'<path d="{rounded_path(poly)}" fill="none" stroke="{stroke}" '
            f'stroke-width="1.6"{dash} marker-end="url(#ah-{stroke.lstrip("#")})"/>')
        lbl = clean_text(e.get('value'))
        if lbl and lbl[0].strip():
            mid = poly[len(poly) // 2]
            fs = float(st.get('fontSize', 10))
            txt = ' '.join(lbl)
            tw = len(txt) * fs * CHAR_W + 8
            body.append(
                f'<rect x="{mid[0] - tw / 2:.1f}" y="{mid[1] - fs * 0.85:.1f}" width="{tw:.1f}" '
                f'height="{fs * 1.7:.1f}" rx="3" fill="#ffffff" fill-opacity="0.92"/>')
            body.append(
                f'<text x="{mid[0]:.1f}" y="{mid[1] + fs * 0.36:.1f}" font-family="Helvetica, Arial, sans-serif" '
                f'font-size="{fs}" fill="{stroke}" text-anchor="middle">{esc(txt)}</text>')

    strokes = set()
    for e in edges:
        strokes.add(parse_style(e.get('style')).get('strokeColor', '#666666'))
    defs = ''.join(
        f'<marker id="ah-{c.lstrip("#")}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="{c}"/></marker>'
        for c in strokes)

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" '
           f'width="{W:.0f}" height="{H:.0f}" font-family="Helvetica, Arial, sans-serif">'
           f'<defs>{defs}</defs>'
           f'<rect width="100%" height="100%" fill="#ffffff"/>' + ''.join(body) + '</svg>')
    return svg


if __name__ == '__main__':
    for f in sys.argv[1:]:
        out = f.rsplit('.', 1)[0] + '.svg'
        open(out, 'w').write(convert(f))
        print('wrote', out)
