# CLAUDE.md — Coles Agentic AI Project

Context file for Claude sessions. Read this before doing any work in this folder.

## Project overview

Designing agentic AI enablement for **Coles IT Operations** on **Microsoft Foundry (Azure)**. The full programme is a 7-agent, 3-phase proposal (Agent Assist, Voice, RCA, Conversational, Knowledge, Predictive, DC Ops). **This project focuses on 2 of the 7:**

1. **Knowledge Agent** — retrieves grounded answers from runbooks, SOPs and previous incidents. Two loops: *serve* (agentic retrieval with citations) and *curate* (staleness/duplicate/gap detection, article drafting as draft PRs).
2. **Predictive Agent** — identifies operational trends and provides early risk warnings. Deliberately NOT an LLM forecasting engine: classical models (Azure ML / Fabric) do the maths; the LLM only explains, correlates with change history, and drafts recommendations. **Advisory only — zero write authority.**

## Architectural principles (non-negotiable, P1–P6)

- **P1** Agents reason; they never execute. State changes go through an Action API layer (idempotency keys, approvals, rollback). Agents emit intent, not effect.
- **P2** No raw PII reaches an LLM. Mandatory de-identification tier: Azure AI Language PII detect (+ Coles custom recognisers: employee number, store number, team-member ID, loyalty ID) → pseudonymise with stable reversible tokens (PERSON_1, EMP_0042 — never masking, it destroys coreference) → token vault (isolated Cosmos DB, CMK, TTL = ticket lifecycle, unreachable by agent identity). Re-identify only at render time (Purview label + EXTRACT right + Entra group). Redact at confidence ≥ 0.5, fail closed.
- **P3** No answer without evidence. Paired critic per agent; ungrounded output is BLOCKED (`NOT_GROUNDED` + structured handover), never surfaced with a caveat.
- **P4** Every action attributable. One correlation ID spans channel → APIM → Service Bus → Durable Functions → agent → tool → ITSM. Signed immutable run records to ADLS (legal hold).
- **P5** Progressive autonomy: advisory → suggest → approve-and-execute → auto-execute, per action type, promoted only on eval evidence. Both agents currently advisory/draft-only for anything mutating.
- **P6** Stateless agents, stateful workflows: long-running flows live in Durable Functions, not the agent.

## Key architecture decisions

- **A2A communication: REST via APIM** (user-chosen). Every agent/app calls the same routes — no point-to-point integrations. Chain: Front Door Premium+WAF → Application Gateway WAF v2 (regional) → APIM Premium v2 AI Gateway → de-identification tier → Foundry Agent Service.
- APIM policies: `llm-token-limit`, `llm-semantic-cache` (Managed Redis), `llm-content-safety shield-prompt=true`, `llm-emit-token-metric`, backend pool + circuit breaker, managed identity to Foundry (no model keys in apps).
- Base routes: `/agents/knowledge/v1` and `/agents/predictive/v1` at `https://api.coles-itops.internal`.
- Required headers on every call: `Authorization: Bearer <Entra Agent ID token>` (OBO for user-initiated), `Ocp-Apim-Subscription-Key`, `x-correlation-id`, `x-caller-agent`.
- Entra app roles: `Knowledge.Query`, `Knowledge.Curate`, `Predictive.Read`, `Predictive.Feedback`.
- Error shape: `{ "error": { "code", "message", "correlation_id" } }`; codes: UNAUTHORIZED, FORBIDDEN_SCOPE, NOT_GROUNDED, RATE_LIMITED, VALIDATION_ERROR.
- Knowledge Agent = Foundry **prompt agent**; Predictive Agent = Foundry **hosted (containerised) agent** orchestrated by Durable Functions.
- Async channels: Service Bus topics `knowledge.ingest` (approved RCA → KB candidate) and `predictive.risk-alerts` (watchlist fan-out → Notification Sender → dashboards/Teams); Event Grid for doc/ITSM/monitor events; Event Hubs for telemetry streaming to Predictive.
- Grounding: Foundry IQ → Azure AI Search (hybrid BM25+vector + semantic ranker; one index per corpus: runbooks, SOPs, KEDB, RCA library, CMDB descriptions; security-trimmed by Entra group). Ingestion: Functions + Document Intelligence → chunk → **PII-redact at index time** → embed (text-embedding-3-large) → index.
- Predictive tool set (10): Ticket Trend Reader, Metrics Trend Reader, Forecaster (Azure ML, versioned), Anomaly Detector, Change Correlator, Risk Scorer (deterministic, not LLM), Narrative Explainer (LLM + evidence critic), Knowledge Cross-Referencer (calls Knowledge `POST /query`), Watchlist Publisher, Feedback Recorder (Cosmos → accuracy KPIs → Agent Optimizer; accuracy is the currency for autonomy promotion).
- Knowledge serve loop (5 steps): Query Decomposer (mini model) → Multi-Query Retriever (Foundry IQ) → Reranker → Answer Synthesiser + Citation Builder → Groundedness Critic. Curate loop: Freshness Checker, Duplicate/Conflict Detector, KB-Gap Detector, Article Drafter → **draft PR only, human publishes**.
- Resilience: active-active Australia East / Australia Southeast; Cosmos multi-region write; AI Search replicas ≥3; sync paths degrade to retrieval-only under load; PTU for interactive agents, PAYG spillover for batch (Predictive); RPO 15 min / RTO 1 h for agent state (index rebuildable from source of truth).

## Endpoint summary

Knowledge (`/agents/knowledge/v1`): `POST /query` (primary A2A — grounded Q&A + citations; `response_mode: voice_short` for Voice), `POST /search` (raw passages, used by RCA), `POST /ingest`, `GET /freshness/report`, `GET /gaps`, `POST /articles/draft`, `GET /health`.

Predictive (`/agents/predictive/v1`): `GET /risks?top=N&domain=&min_score=`, `GET /watchlist/weekly`, `POST /forecast` (metric/resource/horizon → breach date + confidence + model version), `POST /trends/analyze`, `POST /feedback` (outcome: confirmed/false alarm/prevented), `GET /health`.

## Mock data (referenced in specs/docs — not yet present as files in this folder)

- `tickets/tickets.json` — 8 mock tickets: password reset (REQ-2026-10231, REQ-2026-10245), access requests (REQ-2026-10298 pending, REQ-2026-10312 approved), EFTPOS incidents (INC-2026-08871, INC-2026-08872, historical INC-2026-08850), refrigeration status/escalation (INC-2026-08790).
- `knowledge_base/SOP-EFTPOS-TERM-FAULT-001.json` — EFTPOS terminal triage runbook (linked to the 3 EFTPOS incidents).
- `knowledge_base/SOP-VOICE-IDENTITY-ACCESS-POLICY-002.json` — identity-verification/action-scoping policy (why password resets auto-execute but access requests don't).
- Recurring worked example across all deliverables: EFTPOS fault at store 0417, SOP-EFTPOS-TERM-FAULT-001, risk RSK-2026-0713 ("4th occurrence this quarter"), forecast example sql-prod-03 disk breach 2026-07-29.

## Files in this folder (all under `architecture/`)

| File | What it is |
|---|---|
| `knowledge-predictive-architecture.drawio` | Combined overview diagram — both agents on the shared platform layer |
| `knowledge-agent-architecture.drawio` | Detailed Knowledge Agent diagram (ingress, PII tier, serve/curate loops, critic, ingestion, trust rail) |
| `predictive-agent-architecture.drawio` | Detailed Predictive Agent diagram (signal sources, classical ML plane, Durable Functions, tools, delivery) |
| `agent-endpoints-spec.md` | Endpoint & tool specification v1.1 — routes, headers, payloads, expanded per-tool tables, P1–P6 enforcement |
| `knowledge-agent-explained.docx` | Plain-language doc for Azure/Foundry newcomers — glossary with analogies + 4 task walkthroughs |
| `predictive-agent-explained.docx` | Same format for the Predictive Agent |
| `rest-endpoint-reference.docx` | Plain-language REST endpoint reference (both agents, shared rules, traffic map, async channels) |

Plus, at the folder root:

| File | What it is |
|---|---|
| `index.html` | **The website** — single self-contained exec-facing site covering all 7 agents (problem / solution + P1–P6 / arch layers + data flow / 8 diagram slots / agent detail + coverage table / 3 phases / benefits). Generated — do not hand-edit. |
| `site/template.html` | Source template. Edit this, not `index.html`. Contains `__DIAGRAM_TABS__` and `__DIAGRAM_PANES__` placeholders; everything else is literal. |
| `site/drawio2svg.py` | Minimal draw.io → SVG converter. **No longer used by the build** — kept for reference only. |
| `site/build_site.py` | Rebuilds `index.html`. **Run `python3 site/build_site.py` after editing any diagram or the template.** |

**Diagrams are images, not draw.io.** As of the latest revision the site is **image-only** — `build_site.py` does NOT render `.drawio` files. Diagrams are authored in **Whimsical** (layered bands, official Azure icons, numbered teal step badges, red hexagon = critic/blocking gate, yellow = human review step, purple = LLM step, dashed line = async) and exported as PNG.

`build_site.py` holds a `DIAGRAMS` list of 8 slots — combined overview + one per agent. For each it looks in `architecture/` for `<slug>.png`, `.svg`, `.jpg`. If found it base64-embeds it and the tab shows a green dot; if not it renders a "slot reserved" placeholder with a grey dot. The section opens on the first tab that actually has a diagram.

Present: `knowledge-agent-architecture.png`, `predictive-agent-architecture.png` (uploaded July 2026; flattened onto white and resized to 2400px wide by the build prep — originals were 3412px RGBA with a transparent surround).

Pending slugs: `knowledge-predictive-architecture` (combined overview), `agent-assist-architecture`, `voice-agent-architecture`, `conversational-agent-architecture`, `rca-agent-architecture`, `dcops-agent-architecture`.
Export from Whimsical, name it with the slug, drop it in `architecture/`, re-run the build — no template edit needed.

`index.html` is ~2.1 MB because the PNGs are embedded. That's expected; it keeps the file self-contained.

Text content for all 7 agents is already on the page. Knowledge and Predictive are detailed; Voice is grounded in `SOP-VOICE-IDENTITY-ACCESS-POLICY-002` and the mock tickets; Agent Assist, Conversational, RCA and DC Ops are written at capability level from the spec's §4 caller table (role, how it calls, autonomy, endpoints consumed).

The diagram viewer is CSS-first: SVGs render at `width:100%` with no JS. JS only adjusts the `.zoomer` width % for zoom, plus drag-pan and the full-screen lightbox. Do not reintroduce a JS-computed initial scale — that was the bug that made diagrams invisible.

The user may edit the .drawio files in draw.io between sessions — treat the files on disk as source of truth, not earlier generated versions.

## Conventions & preferences

- User (nkkkk) is **new to Azure and Foundry** — explain services in plain language when writing docs; use analogies; avoid unexplained jargon.
- User prefers concise, direct responses.
- Diagram format: draw.io (.drawio). Docs: .docx for deliverables, .md for specs.
- Diagram palette: blue #DAE8FC consumers/sources, green #D5E8D4 ingress/classical ML, orange #FFE6CC Knowledge, purple #E1D5E7 Predictive, yellow #FFF2CC async/delivery, grey #F5F5F5 data, red #F8CECC trust/PII/critic.
- Validate .drawio XML after generating (parse check + no dangling edge refs).
- Full Azure service list and the 7-agent designs are in the two blueprint documents the user pasted in conversation (July 2026); the spec and diagrams reflect them — check `agent-endpoints-spec.md` first when details are needed.

## Likely next steps

- OpenAPI YAML version of the endpoint spec for APIM import.
- Sequence diagrams per scenario (e.g. Voice → Knowledge → ticket for the EFTPOS case).
- Adding the mock data files (`tickets/`, `knowledge_base/`) into this folder.
- Diagrams/docs for the remaining 5 agents if scope expands.
