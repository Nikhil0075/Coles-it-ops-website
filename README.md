# Coles IT Ops — Agentic AI Website

Internal working repo for the Coles IT Operations agentic AI programme (Microsoft AI Foundry).
Seven agents, three delivery phases. Knowledge and Predictive are designed in detail; the other
five are specified at capability level.

## The site

`index.html` is a single self-contained page — all CSS, JS and diagram images are inlined, so it
opens by double-clicking with no server and no build step.

It covers: problem statement → solution and the P1–P6 principles → architecture layers and data
flow → architecture diagrams → all seven agents plus a coverage table → the three delivery phases
→ benefits.

## Rebuilding

`index.html` is **generated**. Don't hand-edit it.

```bash
python3 site/build_site.py
```

| Path | What it is |
|---|---|
| `site/template.html` | Source template — edit this for any content or styling change |
| `site/build_site.py` | Build script: embeds the diagrams, writes `index.html` |
| `site/drawio2svg.py` | Old draw.io → SVG converter. **Not used by the build.** Kept for reference |

## Adding a diagram

Diagrams are authored in Whimsical and exported as PNG. The build looks in `architecture/` for a
file matching each slot's slug; if it's there the tab shows a green dot, if not you get a
"slot reserved" placeholder with a grey dot.

```
architecture/knowledge-predictive-architecture.png   # combined overview   — pending
architecture/knowledge-agent-architecture.png        # ✓ in
architecture/predictive-agent-architecture.png       # ✓ in
architecture/rca-agent-architecture.png              # ✓ in
architecture/agent-assist-architecture.png           # pending
architecture/voice-agent-architecture.png            # pending
architecture/conversational-agent-architecture.png   # pending
architecture/dcops-agent-architecture.png            # pending
```

Drop the file in, re-run the build, commit. No template edit needed.

Keep exports at roughly 2400px wide and flattened onto white — the diagrams are base64-embedded,
so oversized PNGs bloat `index.html` fast.

## Deployment

`.github/workflows/deploy.yml` publishes to GitHub Pages on every push to `main` that touches
`index.html`. It can also be triggered manually from the Actions tab.

Live site: **https://nikhil0075.github.io/Coles-it-ops-website/**

Setup, once: **Settings → Pages → Source → GitHub Actions**.

**The workflow publishes only `index.html`** — not the endpoint spec, the `.docx` deliverables or
the mock ticket data. Those stay in the repo but aren't served as part of the site.

> **This repository is public.** GitHub Pages only builds from public repositories on a free
> personal account. Everything committed here — the endpoint spec, the architecture diagrams, the
> `.docx` deliverables and the mock ticket data — is therefore visible to anyone. The mock data is
> pseudonymised and contains no real team-member records, and `api.coles-itops.internal` is a
> placeholder hostname, not a reachable endpoint. Keep it that way: **do not commit real ticket
> exports, real hostnames, connection strings, subscription IDs or keys.**

## Other contents

| Path | What it is |
|---|---|
| `architecture/agent-endpoints-spec.md` | Endpoint & tool specification v1.1 |
| `architecture/*.docx` | Plain-language explainers and the REST endpoint reference |
| `architecture/*.drawio` | Original draw.io diagrams — superseded by the Whimsical PNGs |
| `mock_data/` | Mock tickets and SOP/KB documents used in the worked examples |
| `CLAUDE.md` | Project context and conventions |
