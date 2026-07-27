# Knowledge Agent & Predictive Agent — Endpoint & Tool Specification

Coles IT Operations · Microsoft AI Foundry · v1.1 · July 2026
Aligned to the production architecture blueprint (principles P1–P6).

## 1. Communication model

Both agents expose REST APIs behind **Azure API Management (Premium v2, AI Gateway)**, fronted by **Azure Front Door Premium (+WAF)** and regional **Application Gateway WAF v2**. Any agent (Agent Assist, Voice, Conversational, RCA, DC Ops) or approved service can call them the same way — there are no point-to-point integrations.

```
Caller ─ Entra Agent ID token (OBO) ─> Front Door ─> App Gateway ─> APIM (AI Gateway) ─> De-identification tier ─> Foundry Agent Service
```

APIM policies on every call: Entra Agent ID authentication (per-agent principal, RBAC, Conditional Access), `llm-token-limit` (TPM per consumer), `llm-semantic-cache` (Managed Redis), `llm-content-safety` with `shield-prompt=true` (XPIA defense), `llm-emit-token-metric`, backend pool + circuit breaker, correlation-ID propagation, audit logging. Apps hold an APIM subscription key; APIM authenticates to Foundry with managed identity — no model keys in app code.

**De-identification tier (P2, mandatory):** between APIM and any model turn, request text passes Azure AI Language PII detection (Text/Conversation/Document + Coles custom recognisers), is pseudonymised with stable reversible tokens (`PERSON_1`, `EMP_0042`), and the token↔value map is written to an isolated CMK-encrypted Cosmos DB vault the agent identity cannot reach. Agents, indexes, caches, traces and eval sets contain pseudonyms only. Re-identification happens only at render time for entitled users (Purview label + EXTRACT right + Entra group).

### Required headers (all endpoints)

| Header | Purpose |
|---|---|
| `Authorization: Bearer <token>` | Entra Agent ID token (client-credentials or OBO for user-initiated calls) |
| `Ocp-Apim-Subscription-Key` | APIM subscription (per calling agent, enables per-agent quotas) |
| `x-correlation-id` | End-to-end trace ID; generated at first hop, propagated everywhere |
| `x-caller-agent` | Calling agent's ID, e.g. `agent-assist`, `rca-agent` |

### Standard error body

```json
{ "error": { "code": "NOT_GROUNDED", "message": "No approved source found; escalate to human.", "correlation_id": "corr-8871-01" } }
```

Common codes: `UNAUTHORIZED`, `FORBIDDEN_SCOPE`, `NOT_GROUNDED`, `RATE_LIMITED`, `VALIDATION_ERROR`.

---

## 2. Knowledge Agent

Base URL: `https://api.coles-itops.internal/agents/knowledge/v1`
Entra app role required: `Knowledge.Query` (read), `Knowledge.Curate` (ingest/draft)

### POST /query — grounded Q&A (primary A2A endpoint)

Used by Agent Assist, Conversational, Voice, RCA and DC Ops agents to get a grounded answer with sources. Never answers without a source — returns `NOT_GROUNDED` instead.

Request:
```json
{
  "question": "EFTPOS terminal offline at checkout, what are the triage steps?",
  "context": { "ticket_id": "INC-2026-08872", "system": "EFTPOS", "store_id": "0417" },
  "filters": { "doc_types": ["runbook", "sop", "known_issue"], "max_sources": 3 },
  "response_mode": "answer_with_steps"
}
```

Response:
```json
{
  "answer": "Follow the EFTPOS terminal fault triage runbook: 1) confirm terminal power and network LED state...",
  "confidence": 0.93,
  "sources": [
    {
      "doc_id": "SOP-EFTPOS-TERM-FAULT-001",
      "title": "EFTPOS Terminal Fault Triage Runbook",
      "section": "Triage steps",
      "last_updated": "2026-05-02",
      "url": "https://kb.coles-itops.internal/sop/SOP-EFTPOS-TERM-FAULT-001",
      "derived_from_incident": "INC-2026-08850"
    }
  ],
  "correlation_id": "corr-8872-04"
}
```

### POST /search — raw hybrid search

Returns ranked passages (vector + BM25 via Foundry IQ / Azure AI Search) without answer synthesis. For agents that do their own reasoning (e.g. RCA Agent evidence gathering).

```json
{ "query": "database performance degradation", "top": 5, "filters": { "doc_types": ["runbook"] } }
```

### POST /ingest — index new/updated content

Controlled pipeline: Document Intelligence chunks → embeddings → AI Search index. Also triggered async by Event Grid on doc-repo changes and by Service Bus `knowledge.ingest` topic (e.g. approved RCA → new article). Requires `Knowledge.Curate`.

```json
{ "source_uri": "adls://kb-corpus/sops/SOP-EFTPOS-TERM-FAULT-001.json", "doc_type": "sop", "approved_by": "kb-owner@coles.com.au" }
```

### GET /freshness/report — stale/duplicate/conflicting docs

Query params: `?older_than_days=180&include=duplicates,conflicts`. Returns list for knowledge owners.

### GET /gaps — unanswered-question backlog

Questions the agent couldn't ground, clustered into missing-article candidates.

### POST /articles/draft — draft KB article from an incident/RCA

```json
{ "source_type": "rca", "source_id": "RCA-2026-0142", "target_doc_type": "known_issue" }
```
Returns a draft queued for human review — never auto-published.

### GET /health — liveness/readiness (index freshness timestamp included)

### Knowledge Agent — expanded tool catalogue

Two loops per the blueprint (§3.5): **serve** (agentic retrieval) and **curate** (KB caretaker). All tools exposed via the MCP Toolbox; the agent emits intent, tools act (P1).

**Serve loop (synchronous, auto-approved — read-only):**

| # | Tool | Backing service | Input → Output | Guardrail |
|---|---|---|---|---|
| 1 | Query Decomposer | mini/nano model via model router | complex question → sub-queries | pseudonymised text only |
| 2 | Multi-Query Retriever | Foundry IQ → Azure AI Search (hybrid BM25+vector, one index per corpus: runbooks, SOPs, KEDB, RCA library, CMDB) | sub-queries → candidate chunks | security-trimmed by caller's Entra group (OBO — never exceeds caller's entitlements) |
| 3 | Reranker | AI Search semantic ranker | chunks → top-k ranked | — |
| 4 | Answer Synthesiser + Citation Builder | reasoning model | chunks → answer + citations (doc ID, section, version, last_updated) | schema-validated JSON output |
| 5 | **Groundedness Critic** | Foundry evaluations (paired critic config) | draft answer + chunks → verdict | every claim must map to a retrieved chunk; ungrounded output is **blocked** (`NOT_GROUNDED` + structured handover), never surfaced with a caveat (P3) |

**Curate loop (async — scheduled + event-driven; output is always a draft PR, human publishes):**

| # | Tool | Backing service | Input → Output | Guardrail |
|---|---|---|---|---|
| 6 | Content Ingestor | Functions + Document Intelligence: parse → chunk → **PII-redact at index time** → embed (text-embedding-3-large) → index | source doc → indexed chunks | index-time redaction means even successful prompt injection cannot exfiltrate PII that was never indexed |
| 7 | Freshness Checker | scheduled Functions scan vs source-of-truth | corpus → stale-doc report | report only |
| 8 | Duplicate/Conflict Detector | vector similarity + contradiction check across indexes | corpus → dup/conflict list | report only |
| 9 | KB-Gap Detector | unanswered-query log + RCA outputs | → missing-article backlog | report only |
| 10 | Article Drafter | reasoning model over resolved incidents / approved RCAs | source ID → **draft PR against the KB** | knowledge-owner review required; nothing self-publishes; merge triggers reindex |

---

## 3. Predictive Agent

Base URL: `https://api.coles-itops.internal/agents/predictive/v1`
Entra app role required: `Predictive.Read` (all reads), `Predictive.Feedback` (feedback writes)

**Advisory only (autonomy level 1 for all action types, P5):** no endpoint on this agent changes any live system; its Entra scope has no write access beyond its own Cosmos container and Service Bus topic.

**Design stance (blueprint §3.6):** deliberately *not* an LLM forecasting engine. Forecasting and anomaly detection run as classical, versioned models in Azure ML / Fabric — auditable and reproducible. The LLM only explains the signal, correlates it with change history, and drafts the recommendation. This stops false positives from a model that is bad at arithmetic. Runs as a hosted (containerised) Foundry agent orchestrated by Durable Functions (stateless agent, stateful workflow — P6).

### GET /risks — ranked current risks

`?top=5&domain=network|database|apps|all&min_score=0.5`

```json
{
  "generated_at": "2026-07-20T06:00:00+10:00",
  "risks": [
    {
      "risk_id": "RSK-2026-0713",
      "title": "Store 0417 EFTPOS incident recurrence — 4th occurrence this quarter",
      "likelihood": 0.82, "impact": "high", "score": 0.78,
      "evidence": ["INC-2026-08850", "INC-2026-08871", "INC-2026-08872"],
      "recommendation": "Check terminal firmware baseline against SOP-EFTPOS-TERM-FAULT-001; raise problem record.",
      "related_knowledge": { "doc_id": "SOP-EFTPOS-TERM-FAULT-001" }
    }
  ]
}
```

### GET /watchlist/weekly — the weekly watchlist

Same shape as /risks plus trend direction per item; also auto-published Mondays to Service Bus `predictive.risk-alerts` → Notification Sender → dashboards/Teams.

### POST /forecast — project a metric forward

```json
{ "metric": "disk_used_pct", "resource": "sql-prod-03", "horizon_days": 30 }
```
```json
{ "current": 81.2, "threshold": 95, "predicted_breach_date": "2026-07-29", "confidence": 0.88, "model": "azureml:capacity-forecast-v3" }
```

### POST /trends/analyze — ad-hoc trend question

```json
{ "scope": { "category": "refrigeration", "period_days": 90 }, "question": "Are refrigeration incidents increasing?" }
```

### POST /feedback — record prediction outcome (closes the loop)

```json
{ "risk_id": "RSK-2026-0713", "outcome": "confirmed", "notes": "Firmware mismatch found on 3 terminals", "reported_by": "dcops-agent" }
```
Stored in Cosmos DB; feeds Agent Optimizer and accuracy reporting ("earn scope honestly").

### GET /health

### Predictive Agent — expanded tool catalogue

All read-only against live systems; writes go only to its own stores.

| # | Tool | Backing service | Input → Output | Guardrail |
|---|---|---|---|---|
| 1 | Ticket Trend Reader | Fabric OneLake (incident history, ticket trends) — queried by tool, never SQL-generation into prod | scope + window → volumes, recurrence, seasonality | read-only semantic-model queries |
| 2 | Metrics Trend Reader | Event Hubs stream → Fabric long-range store | metric + resource + window → time series | read-only |
| 3 | Forecaster | Azure ML forecasting models (versioned) | metric + horizon → projection, breach date, confidence, **model version** | classical model does the maths; result carries run ID for reproducibility |
| 4 | Anomaly Detector | Azure ML anomaly endpoints | metric window → anomalies + seasonal baseline | classical, versioned |
| 5 | Change Correlator | ITSM change/release read API | signal window → changes in window, ranked by proximity | read-only |
| 6 | Risk Scorer | deterministic scoring function | signals + impact map → likelihood × business impact, ranked top-N | not LLM-scored — reproducible |
| 7 | Narrative Explainer (LLM) | reasoning model | scored risks + evidence → "what it noticed, why it matters, what to check" | **Evidence Critic** (paired): every sentence must bind to a classical-model output or change record; unsupported claims stripped (P3) |
| 8 | Knowledge Cross-Referencer | Knowledge Agent `POST /query` (A2A via APIM) | risk → relevant runbook/SOP citation attached | grounded via Knowledge Agent's own critic |
| 9 | Watchlist Publisher | Service Bus `predictive.risk-alerts` → Notification Sender | ranked risks → alerts/watchlist to dashboards & Teams | publish-only; humans decide what to act on |
| 10 | Feedback Recorder | Cosmos DB (predictions + outcomes) | risk ID + outcome → accuracy KPIs | feeds Agent Optimizer; accuracy record is the *evidence* for any autonomy promotion (P5) |

---

## 4. How the other agents use these endpoints

| Caller | Calls | Purpose |
|---|---|---|
| Agent Assist | `knowledge POST /query` | Runbook + similar-incident answer beside the ticket |
| Conversational | `knowledge POST /query` | Grounded self-service answers with source links |
| Voice | `knowledge POST /query` (response_mode: `voice_short`) | Short spoken-friendly answers |
| RCA Agent | `knowledge POST /search`; publishes approved RCA to `knowledge.ingest` topic | Evidence retrieval; knowledge compounding |
| DC Ops | `knowledge POST /query`; `predictive POST /forecast`, `POST /feedback` | Playbook lookup; capacity checks; confirm predictions |
| Ops teams/dashboards | `predictive GET /risks`, `GET /watchlist/weekly` | Weekly review |

### Async channels

| Channel | Direction | Payload |
|---|---|---|
| Service Bus topic `predictive.risk-alerts` | Predictive → subscribers (Notification Sender, dashboards, DC Ops) | Risk alert (same schema as /risks item) |
| Service Bus topic `knowledge.ingest` | Any agent → Knowledge Agent | Ingest request (RCA learnings, resolved-incident summaries) |
| Event Grid | Doc repo / ITSM / Monitor → agents | Doc-updated, ticket-created, alert-fired events |
| Event Hubs | Monitoring platforms → Predictive | Continuous metrics/telemetry stream |

---

## 5. Guardrails applied at the endpoint layer

Every principle is enforced in infrastructure, not in agent prompts: P1 — agents emit intent; tool proxies (Azure Functions) act, resolving pseudonyms to real identifiers only inside the proxy, never returning them to the model. P2 — de-identification tier before every model turn; token vault unreachable by agent identity; APIM egress policy scans outbound prompts and fails closed. P3 — paired critics block ungrounded output (`NOT_GROUNDED`) rather than caveating it. P4 — one correlation ID spans channel → APIM → Service Bus → Durable Functions → agent → tool → ITSM; every output is a signed, immutable run record in ADLS (inputs, chunk IDs + versions, model + prompt version, tool calls, critic verdict, human decision, outcome). P5 — autonomy is per-action-type config (advisory → suggest → approve-and-execute → auto-execute), promoted only on eval evidence; both agents currently sit at advisory/draft-only for anything that changes state. P6 — long-running flows (weekly watchlist runs, curation sweeps) live in Durable Functions, not in the agent.

## 6. Resilience & performance notes

Synchronous knowledge queries degrade to retrieval-only mode under load rather than failing; Predictive runs as batch on PAYG spillover while interactive agents hold PTU reservation; semantic cache hit ratio is a first-class KPI; active-active across Australia East / Australia Southeast with Cosmos multi-region write, AI Search replicas ≥3, and the knowledge index rebuildable from source-of-truth (RPO 15 min / RTO 1 h applies to agent state, not the index).
