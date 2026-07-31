# Knowledge Agent & Predictive Agent — Azure Architecture Specification

Coles IT Operations · Microsoft Foundry on Azure · v1.0 · July 2026
Companion document to the two Whimsical architecture diagrams.

| Diagram | Link |
|---|---|
| **AgentOps — Combined System (all seven agents)** | **https://whimsical.com/XoDwbzi5VnQADZJu1uJJgR** |
| Knowledge Agent — Azure Architecture | https://whimsical.com/5xQdL3XX7LyV6TKudTQmNU |
| Predictive Agent — Azure Architecture | https://whimsical.com/2AKCBwFyHWwoabyS3ZM8gt |
| RCA Agent — Azure Architecture | https://whimsical.com/2bdQLKnukbUnYewenpPpeu |
| Conversational Agent — Azure Architecture | https://whimsical.com/ASWBxR1juQ97d5yb4ChjfU |
| Voice Agent — Azure Architecture | https://whimsical.com/JnUN8EQGZFeSARRSh1MxpM |
| Agent Assist — Azure Architecture | https://whimsical.com/LZqnydRsDVgVoJtqCq9DtW |
| DC Ops Agent — Azure Architecture | https://whimsical.com/5sKvZXcMFvmKiczmVDvhfG |

The diagrams are deliberately sparse — only the services that carry the design, with labeled directional flows and the trust boundaries that matter. Numbered arrows correspond to the numbered flow steps below; this document carries the full detail that is intentionally kept off the canvas.

---

## 0. The combined system — what makes seven agents one product

### How work moves between the agents

The seven are not peers doing the same job — they sit in tiers.

**Front line (Conversational + Voice).** Same capability, different channel: chat/portal versus PSTN/Teams. Both intake an issue, answer what they can from grounded knowledge, and raise a ticket when they can't. Neither escalates to a specialist directly — they hand to Agent Assist.

**Triage (Agent Assist).** The L1 co-pilot. Gathers service-catalog context, runbooks and similar incidents, ranks remediations, then routes on incident type:

- Network or database infrastructure → **DC Ops Agent**
- Application or backend code defect → **RCA Agent**
- Store hardware or identity request → stays in Agent Assist

**Specialists (RCA + DC Ops).** RCA does post-incident analysis and publishes an approved RCA back to the Knowledge Agent, so the fleet gets smarter. DC Ops inspects network and SQL telemetry and holds the fleet's only write authority.

**Cross-cutting (Knowledge + Predictive).** Knowledge grounds every agent above it — nobody else owns an index. Predictive reads the whole ticket estate rather than any single incident, and pushes risk context (recurrence, forecasts, weekly watchlist) to Agent Assist and DC Ops.

That tiering is why the fleet band in the diagram is a topology rather than a row: the escalation arrows are the product.

### The five shared things

Everything else is per-agent.

1. **One ingress rail.** Every request from every channel goes Front Door + WAF → APIM AI Gateway → de-identification tier. No agent has a private front door, so token limits, content safety, throttling and audit are enforced once rather than seven times.
2. **One contract.** Entra Agent ID token, `x-correlation-id`, APIM subscription key, pseudonymised payload. Adding an eighth agent means implementing this contract, not negotiating a new integration.
3. **One grounding hub.** The Knowledge Agent owns the index; the other six call `POST /query`. This is the single most important structural decision in the design — it means knowledge quality is fixed in one place, and an approved RCA improves every agent at once.
4. **One data, action and event plane.** Shared Cosmos, OneLake, Service Bus and Functions tool proxies. Agents emit intent; proxies act (P1).
5. **One control plane.** Identity, secrets, DLP, private networking, telemetry and evaluation are fleet-wide. One correlation ID spans channel → APIM → agent → tool → system of record.

### Findings from the AgentOps repository

The `AgentOps-agentops-integration` implementation confirms the hub pattern and surfaces three gaps against this target architecture.

| # | Finding | Evidence |
|---|---|---|
| 1 | **The Knowledge Agent hub is real.** Conversational, Agent Assist and RCA all resolve `KNOWLEDGE_AGENT_URL` and call `/query` on port 8004. | `conversational_agent.py:47`, `agent_assist_agent.py:111`, `rca_agent.py:98` |
| 2 | **Predictive points at the wrong agent.** Its `KNOWLEDGE_AGENT_URL` defaults to `http://127.0.0.1:8001` — that is the **RCA Agent**. The Knowledge Agent is 8004. Same defect in the seed script. Predictive's Knowledge Cross-Referencer is calling RCA. | `predictive_agent/tools/tools.py:12`, `data/seed_knowledge_base.py:51` |
| 3 | **Agent Assist has no escalation tool.** `escalate_to_specialist_agent_tool` appears in the design graph but no such tool or outbound call to DC Ops (8006) / RCA (8001) exists in `agent_assist_agent.py`. Routing is currently classification only. | grep of `agent-agent_assist/` |
| 4 | **No shared ingress today.** Each backend binds `0.0.0.0` on its own port (8000–8006) with permissive CORS; the React frontend on 5173 is the only integrator. There is no APIM, no de-identification tier and no shared identity in the running system. | `README.md`, `frontend/src/hooks/*` |

Finding 4 is expected for a local dev fleet and is exactly the gap the target architecture closes. Findings 2 and 3 are defects worth fixing regardless of deployment model — 2 in particular will produce confusing behaviour rather than a clean failure, because port 8001 *does* answer.

---

## 1. Architecture goal

Deliver two production agents for Coles IT Operations on Microsoft Foundry:

- **Knowledge Agent** — grounded question answering over runbooks, SOPs, the KEDB, the RCA library and CMDB descriptions, plus an asynchronous "KB caretaker" loop that keeps the corpus fresh. It never answers without a source.
- **Predictive Agent** — a weekly (and on-demand) risk watchlist built from classical forecasting and anomaly models, with an LLM used only to explain and correlate. It is advisory only and changes nothing.

Both sit behind one shared REST rail so any agent or approved service calls them the same way. There are no point-to-point integrations.

## 2. Assumptions

These are design assumptions, not measured requirements. Confirm before build.

| # | Assumption |
|---|---|
| A1 | Australian data residency; primary Australia East, secondary Australia Southeast, active-active. |
| A2 | Interactive knowledge queries target sub-3s p95; the predictive watchlist is a batch workload with no interactive SLA. |
| A3 | RPO 15 min / RTO 1 h applies to **agent state** (Cosmos DB). The search index is rebuildable from source-of-truth, so it carries no independent RPO. |
| A4 | All incident and ticket text is treated as containing PII until proven otherwise. |
| A5 | Both agents launch at autonomy level 1 (advisory / draft-only) for every action type that changes state. |
| A6 | Interactive traffic runs on PTU reservation; batch and spillover run PAYG. |

## 3. Architecture style

- **Knowledge Agent** — retrieval-augmented generation with a paired critic, plus an event-driven asynchronous curation pipeline. Web-queue-worker for ingestion.
- **Predictive Agent** — hybrid: classical ML batch analytics + agentic orchestration. Durable Functions holds the long-running workflow so the agent itself stays stateless (P6).

The deliberate choice on the Predictive side is that **no forecasting or scoring is done by the LLM**. Azure ML and Fabric produce the numbers; the model only writes the explanation. This removes the largest source of false positives — a language model doing arithmetic.

## 4. Recommended Azure services

| Capability | Azure service | Reason |
|---|---|---|
| Global ingress + WAF | Azure Front Door Premium | OWASP rules, bot protection, geo-fencing to AU, single global entry point |
| Regional L7 ingress | Application Gateway WAF v2 | Zone-redundant regional termination, autoscale 2–10 |
| API gateway for AI | API Management Premium v2 (AI Gateway) | `llm-token-limit`, `llm-semantic-cache`, `llm-content-safety` with `shield-prompt`, token metrics, circuit breaker, managed identity to Foundry — no model keys in app code |
| PII detection | Azure AI Language | Text / Conversation / Document PII plus Coles custom recognisers |
| Pseudonymisation + token vault | Azure Functions + isolated Cosmos DB (CMK) | Reversible tokens; the map is stored where the agent identity cannot reach it |
| Agent runtime | Microsoft Foundry Agent Service | Session isolation, managed identity, built-in tracing; prompt agent (Knowledge) and hosted container agent (Predictive) |
| Models | Azure OpenAI via Foundry + model router | Reasoning tier for synthesis/narrative, mini/nano for decomposition and classification |
| Retrieval | Foundry IQ + Azure AI Search | SLA-backed retrieval endpoint over hybrid BM25 + vector with a semantic ranker; one index per corpus; security trimming by Entra group |
| Document processing | Azure AI Document Intelligence | Parse and chunk source documents before embedding |
| Forecasting / anomaly detection | Azure Machine Learning | Versioned, reproducible runs; every result carries a model version and run ID |
| Analytics store | Microsoft Fabric (OneLake) | Incident history, ticket trends, CI/service graph, long-range metrics |
| Streaming ingest | Azure Event Hubs → Container Apps (KEDA) | Continuous telemetry into the long-range store |
| Eventing | Event Grid | Doc-repo changes, Monitor alerts, ITSM webhooks |
| Messaging | Azure Service Bus (topics) | `knowledge.ingest` and `predictive.risk-alerts`; dedup and dead-letter queues |
| Workflow | Durable Functions | Weekly watchlist runs, curation sweeps, fan-out/fan-in, timers, retry with backoff |
| Agent state | Cosmos DB (multi-region write) | Thread state, run records, predictions, feedback |
| Object storage | ADLS Gen2 | Raw + redacted corpora, eval datasets, immutable signed run records under legal hold |
| Cache | Azure Managed Redis | APIM semantic cache, rate-limit counters, session cache |
| Identity | Microsoft Entra Agent ID + managed identities | Each agent is a first-class principal with RBAC and Conditional Access; OBO so an agent never exceeds the caller's entitlements |
| Secrets | Key Vault (HSM) | Keys, certs, CMK; zero stored credentials |
| Governance | Purview DSPM for AI | Sensitivity labels, EXTRACT rights, DLP on agent data access, unified AI audit log |
| Safety | Content Safety + Prompt Shields | At APIM *and* inside Foundry guardrails — defence in depth against XPIA from retrieved documents |
| Observability | App Insights / Azure Monitor (OTel GenAI) | One correlation ID spanning channel → APIM → agent → tool → ITSM |
| Quality | Foundry Evaluations + ASSERT, Agent Optimizer | Groundedness, relevance and safety on production traces; accuracy KPI |
| Networking | VNet, Private Endpoints, Private DNS, Azure Firewall | Private access to every PaaS service; Foundry BYO VNet; no public egress |

---

## 5. Knowledge Agent — end-to-end flow

**Serve loop (synchronous, read-only, auto-approved)**

1. A calling agent or engineer sends HTTPS with an Entra Agent ID token and an `x-correlation-id`.
2. Front Door applies WAF policy and routes to the healthy region.
3. Application Gateway terminates regionally and forwards over mTLS to APIM.
4. APIM authenticates the caller, applies token limits, semantic cache and content safety, then hands off to the de-identification tier. **No model turn happens before this step.**
5. Azure AI Language returns PII entity spans.
6. The pseudonymiser writes the token↔value map to the isolated CMK-encrypted vault. The agent identity has no path to this store.
7. The pseudonymised request continues to the agent's REST surface.
8. The Foundry Agent Service is invoked.
9. Model calls run against pseudonymised text only.
10. The agent emits intent; tools act (P1).
11. The Multi-Query Retriever calls Foundry IQ.
12. Foundry IQ runs hybrid BM25 + vector search against Azure AI Search, security-trimmed by the caller's Entra group.
13. Candidate chunks return.
14. Reranking, synthesis and citation building produce a draft answer bound to chunk IDs and versions.
15. The **Groundedness Critic** checks that every claim maps to a retrieved chunk. Grounded output proceeds; ungrounded output is **blocked** and returns `NOT_GROUNDED` with a structured handover — never a caveated answer (P3).
16. Re-identification happens at render time only, gated on Purview label + EXTRACT right + Entra group membership.
17. The response carries the answer, citations and correlation ID. A signed immutable run record goes to ADLS (P4).

**Curate loop (asynchronous, scheduled + event-driven, draft-only)**

18. Event Grid raises doc-repo change events; Service Bus `knowledge.ingest` carries approved RCAs.
19. The Durable Functions curation scheduler fans out.
20. The Content Ingestor parses, chunks, **redacts PII at index time**, embeds and indexes. Index-time redaction means a successful prompt injection cannot exfiltrate PII that was never indexed.
21. Freshness, duplicate/conflict and KB-gap detectors feed the Article Drafter.
22. Output is always a **draft PR** against the KB. A knowledge owner reviews; merge triggers reindex. Nothing self-publishes.

## 6. Predictive Agent — end-to-end flow

1–5. Same ingress rail and de-identification tier as the Knowledge Agent, on route `/agents/predictive/v1`.

6. Event Grid triggers the Durable Functions orchestrator (weekly watchlist run, or on-demand), which fans out to the signal readers.
7. The agent is invoked and emits intent.
8–15. Signals flow in from the read-only plane: OneLake supplies incident history and recurrence; Fabric supplies long-range metric series; **Azure ML supplies the forecast and the anomalies**; the ITSM read API supplies changes in the window. Every model result carries a version and run ID.
16. The **Risk Scorer** is a deterministic function — likelihood × business impact, ranked top-N. Not LLM-scored, therefore reproducible.
17–18. The Narrative Explainer (LLM) writes what it noticed, why it matters and what to check; the Knowledge Cross-Referencer calls the Knowledge Agent's `POST /query` (A2A via APIM) to attach a grounded runbook or SOP.
19–20. The **Evidence Critic** requires every sentence to bind to a classical-model output or a change record. Unsupported claims are stripped before publishing (P3).
21–24. The watchlist publishes to Service Bus `predictive.risk-alerts`; the Notification Sender delivers to Teams/email/SMS; dashboards present the weekly review. Humans decide what to act on.
25–27. Outcomes come back through `POST /feedback` into Cosmos DB.
28. Accuracy KPIs feed Foundry Evaluations and the Agent Optimizer. **The accuracy record is the only evidence that unlocks more autonomy (P5).**

---

## 6a. RCA Agent — node graph and end-to-end flow

The RCA Agent is the third agent on the same rail. Its distinguishing property is that its output is **the input to the Knowledge Agent** — an approved RCA becomes an indexed article every other agent can ground on. That loop is what makes RCA work pay compounding returns rather than producing documents nobody reads.

**Node → Azure service mapping**

| Node | Azure service | Role |
|---|---|---|
| Correlate | Azure Monitor / Log Analytics | Builds a unified incident timeline from telemetry, alerts and change records |
| Research | **Grounding with Bing Search** (Foundry tool) | Public web context — vendor advisories, known CVEs, provider status |
| KB | Cosmos DB (RCA library) + Knowledge Agent `POST /search` | Prior cases, runbooks and past RCAs; feeds every downstream node |
| Analysis | Azure OpenAI (reasoning tier) | Generates candidate hypotheses from the unified evidence set |
| Narrative | Azure OpenAI | Writes the "what happened" account |
| Recommendations | Azure OpenAI | Drafts corrective and preventive actions |
| Evidence critic | Foundry evaluations (paired critic) | Every claim must bind to a timeline entry, a source or a change record |
| Report | ADLS Gen2 + Service Bus `knowledge.ingest` | Immutable signed RCA record; publishes the approved article for indexing |

**Flow**

1–6. A major incident raises an Event Grid / ITSM trigger; the request crosses the same rail — Front Door + WAF, APIM AI Gateway, de-identification — and Durable Functions orchestrates the RCA run so the agent stays stateless.

7–12. Evidence gathering, all read-only: the Correlate node builds the timeline, the Research node adds public web context, and the KB node contributes prior cases and runbooks via the Knowledge Agent. These converge into a single unified evidence set.

13–18. The Analysis node produces hypotheses, which fan out to the Narrative and Recommendations nodes. Both outputs pass the **Evidence critic** — any claim not bound to evidence is stripped. The result is a *draft*; a human reviews and signs off. Nothing self-publishes.

19–24. The approved RCA is written to ADLS Gen2 as an immutable signed record, published to Service Bus `knowledge.ingest`, indexed by the Knowledge Agent, and written back to the RCA library. Evaluations track RCA quality and repeat-incident rate — the metric that tells you whether RCAs are actually preventing recurrence.

**Two design notes**

*Bing Search API is retired.* The v7 Bing Search APIs were retired on 11 August 2025 and legacy keys now return HTTP 410. The supported path is Grounding with Bing Search as a tool inside Foundry Agent Service. Two consequences: it is materially more expensive than the old API, and it only works from inside a Foundry agent — an external service cannot call it as a standalone tool. Also note Foundry Agents *classic* is deprecated with retirement announced for 31 March 2027, so target the current Foundry Agent Service, not the classic surface.

*External web search is a data-egress decision.* The Research node is the only component in any of the three agents that sends text outside the Azure tenant boundary. It must sit behind the de-identification tier like every other model call, and the query itself should be constrained to product, vendor and error-signature terms — never incident narrative, never store or staff identifiers. This is worth an explicit control rather than an assumption.

---

## 6b. Conversational Agent — node graph and end-to-end flow

The self-service front door. Its architectural job is **containment**: resolve what it can from grounded knowledge, capture what it can't as a well-formed ticket, and hand over cleanly when neither applies.

**Node → Azure service mapping**

| Node | Azure service | Role |
|---|---|---|
| Channels | Teams · web chat · self-service portal | Entry points; all traverse the same REST rail |
| Session state | Cosmos DB | Multi-turn thread state; this is what makes `collect_more → END (wait)` work |
| intent_detection | Azure OpenAI mini/nano tier | Cheap classifier; only the knowledge and ticket-summary branches ever invoke a reasoning model |
| knowledge_retrieval / faq_response | Knowledge Agent `POST /query` (A2A via APIM) | Grounded answer with citations — no separate index for this agent |
| issue_intake | Foundry Agent Service + Cosmos state | Slot filling across turns until required fields are complete |
| ticket_summary | Azure OpenAI + Functions tool proxy → Service Bus → ITSM | Agent emits intent; the proxy writes the ticket (P1) |
| greeting / general_chat | Templated or mini-tier model + Content Safety | Bounded small talk; no tool access |
| exit | ADLS Gen2 | Session closed, transcript archived |

**Flow**

1–6. A user opens a channel; the request crosses Front Door + WAF, APIM AI Gateway and the de-identification tier before reaching the agent. Session state lands in Cosmos DB so the conversation can pause between turns.

7–13. `intent_detection_node` classifies with a mini/nano model and routes to exactly one branch. Cost discipline lives here: a greeting must never cost a reasoning-model call.

14–19. **Knowledge path** — `knowledge_retrieval_node` calls the Knowledge Agent over A2A; `faq_response_node` returns answer plus citations; the groundedness gate blocks anything unsourced and routes it to human handover. Greeting and general chat return directly. Exit archives the transcript.

20–25. **Issue path** — `issue_intake_node` fills slots; if fields are missing the graph ends the turn and waits (session persists in Cosmos); once complete, `ticket_summary_node` produces the summary, the Functions tool proxy writes to ITSM via Service Bus, and the ticket reference returns to the user.

**Two gaps in the supplied graph, both added to the diagram**

*No fallback branch.* The original graph routes to five intents with no path for low-confidence classification. In production the classifier will be uncertain regularly, and with no fallback the agent either guesses a branch or dead-ends. A `fallback_node` routing to human handover is added — and its firing rate is a useful early quality signal.

*No escalation path.* Every branch in the original terminates at END. A conversational agent facing colleagues needs a warm-transfer route that carries the conversation context with it, otherwise the user re-explains everything to a human and the agent has produced negative value. The groundedness gate and the fallback node both feed it.

The metric that matters for this agent is **containment rate against handover rate** — resolved without a human, versus escalated. Tracked in Foundry evaluations alongside the usual groundedness checks.

---

## 6c. Voice Agent — architecture and the speech-pipeline decision

The Voice Agent puts the same reasoning stack behind a phone line. Everything that makes the other agents safe still applies, but voice adds two constraints: latency budget is unforgiving, and **speech is a continuous stream with no natural place to inspect text** unless you design one in.

### The decision that shapes everything: Voice Live vs. discrete pipeline

The supplied graph specifies ACS "Call Automation + **Voice Live API**" *and* separate Speech-to-Text and Neural TTS nodes with media flowing ACS ↔ STT → Gateway → TTS → ACS. Those are two alternative architectures, not two layers of one.

| | Voice Live API | Discrete STT → reason → TTS |
|---|---|---|
| Shape | One unified speech-to-speech interface: STT, model and TTS behind a single API | Separate services stitched together by your orchestrator |
| Turn-taking | Azure Semantic VAD detects speech boundaries by meaning, strips filler words — materially better barge-in and fewer false endpoints | You build turn detection yourself; this is the hard part of voice UX |
| Latency | Lower — fewer hops, no round-trip through your own gateway between hearing and speaking | Higher — every hop is yours to optimise |
| **Text checkpoint before the model** | **None.** Audio goes in, audio comes out | **Yes.** Transcript exists as text before any model call |

**The de-identification mandate (P2) decides it.** If no raw PII may reach the model, there must be a text checkpoint where the PII Guard can pseudonymise between transcription and inference. Voice Live's integrated speech-to-speech path removes exactly that checkpoint. So the diagram is drawn as the **discrete pipeline**, and the Voice Live label is dropped from ACS.

That trade is real and worth stating plainly: this design accepts worse turn-taking and higher latency in exchange for keeping the PII boundary intact. If someone later argues for Voice Live on call-quality grounds, they are implicitly arguing to relax P2 for voice — that is a governance decision, not a performance tuning one.

### Flow

1–6. The caller reaches Azure Communication Services over PSTN, mobile or Teams. Media streams to Azure AI Speech for real-time transcription; the Session Gateway on Container Apps holds the WebSocket session; Neural TTS renders the spoken reply. Event Grid carries call lifecycle events to the gateway.

7–12. **Identity is deterministic and never an LLM decision** — Entra ID verifies the caller, Conditional Access and MFA gate sensitive actions. The PII Guard combines Azure AI Language detection with a deterministic regex layer; pseudonyms are session-scoped and the map lives in a CMK-encrypted Cosmos DB token vault.

13–19. The Agent Orchestrator (LangGraph on Foundry Agent Service) runs intent → slots → policy → action → knowledge → reply, reasoning on pseudonyms only. Azure OpenAI does intent and slot extraction with Content Safety filtering injection and jailbreak attempts; the Knowledge Agent retrieves grounded SOPs from AI Search. The **Policy Engine is deterministic** — a closed action set classifying every request as auto-execute, approval-required or out-of-scope.

20–25. The Tool Proxy Function is the only boundary that resolves a pseudonym back to a real identifier. Actions queue through Service Bus with retry and dead-lettering, the ITSM connector writes the ticket to Cosmos DB, and the reference returns to the caller as spoken confirmation.

### Two further notes

*The regex layer is doing more work than it looks.* On a voice channel the PII Guard sees ASR output, not typed text — transcription errors will defeat pure ML PII detection on exactly the high-risk tokens (card numbers, employee IDs, addresses) because those are read aloud digit by digit. Keeping the deterministic regex layer alongside AI Language is the right call; consider also constraining the ASR with phrase lists for known identifier formats.

*Confirmation before action is not optional on voice.* There is no screen to review a draft. Any `auto_execute` action should be read back and verbally confirmed before the tool proxy fires, and that confirmation belongs in the run record alongside the correlation ID.

---

## 6d. Agent Assist — flow and design notes

The L1 co-pilot beside the ticket. It resolves what it can with grounded guidance and routes the rest, producing both a human-readable brief and structured context for whichever specialist agent picks it up.

**Flow.** Parse incident context → service catalog lookup (owner + dependencies) → runbook retrieval via the Knowledge Agent → similar historical incidents from Fabric OneLake → ranked remediation options → **groundedness critic** → deterministic categorisation → route to DC Ops, RCA, or handle locally → `generate_assist_response_tool` → `assist_guidance.md` + `agent_context_out.json` → handover summary and draft closure notes.

**Three notes on the supplied graph**

*The evidence lookups are serialised but independent.* Service catalog, runbook retrieval and similar-incident search have no data dependency on each other — all three only need the parsed context. Chained sequentially they add three round trips to a latency budget an engineer is watching in real time, mid-incident. Fan them out in parallel and join before remediation. This is the single highest-value change to the graph.

*There is no groundedness gate.* The output promises "remediations + runbooks + **confidence**", but nothing in the graph blocks a remediation that no runbook supports. Every other agent in the fleet has a paired critic (P3); Agent Assist needs one more than most, because its output is read by an L1 engineer under time pressure who is least equipped to catch a plausible-sounding but unsupported instruction. Added to the diagram between remediation and routing.

*Categorisation should not be an LLM call.* Routing to DC Ops versus RCA is decidable from the service catalog and CI class — data already fetched two steps earlier. Deterministic routing is reproducible, auditable and free; an LLM classifier here adds cost and a failure mode for no benefit.

The metric to instrument is **remediation acceptance rate** — how often the engineer actually follows the top-ranked suggestion. That is the honest measure of whether this agent helps.

---

## 6e. DC Ops Agent — flow and design notes

**This is the only agent in the fleet with write authority to a production system.** `apply_force_last_good_plan` executes a query-plan rollback against Azure SQL without a human in the loop. Everything else in the blueprint is advisory or draft-only. That asymmetry deserves to be the loudest thing in the diagram, and it is.

**Flow.** Network Watcher alerts → SQL tuning recommendations → plan-rollback decision → **change-window and approval guard** → apply → **post-apply verification** → runbook grounding per alert category (loops) → root-cause synthesis → `generate_dcops_response_tool` → `agent_context_out` → completion check → deterministic fallback if synthesis failed → human publish gate → Knowledge Base.

**What the graph gets right**

The **deterministic fallback** is genuinely good design and rare to see specified. When final synthesis fails the agent still returns everything it gathered, flagged `COMPLETED_FALLBACK`, instead of erroring out. An on-call engineer at 3am gets partial diagnostics rather than a stack trace. Keep it, and make sure the status distinction is visible in the UI so nobody mistakes fallback output for full analysis.

The **human publish gate** before writing to the KB is also correct — it prevents an unreviewed diagnostic guess from becoming grounding data for every other agent.

**Two gaps, both added to the diagram**

*No post-apply verification.* The graph applies the plan rollback and moves straight to collecting alert categories. It never confirms the change had the intended effect. A write action without a verification step and a rollback path is not a safe automation — it is an unattended one. Verify, and roll back automatically if the metric that triggered the action has not recovered.

*No change-window or approval guard.* "Pre-approved" describes the *action type*, not the *moment*. Applying a query-plan rollback to a production database during peak trade is a materially different risk from applying it at 2am on a Tuesday, even when the action itself is on the approved list. The guard should check change freeze status and blast radius before the apply fires.

**Governance implication.** Because this agent holds a write role, its Entra scope, its change records and its rollback history are the highest-value audit surface in the whole fleet. Every apply should emit an immutable change record to ADLS Gen2 carrying the same correlation ID as the triggering alert — shown on the diagram as a dashed line from the apply step.

---

## 7. Security and networking

**Identity.** Every agent is an Entra Agent ID principal with its own RBAC assignments and Conditional Access policy. User-initiated calls use on-behalf-of so an agent can never exceed the caller's entitlements. Azure-to-Azure authentication uses managed identities throughout; there are no stored credentials.

**The de-identification tier is the central control (P2).** The LLM is treated as an untrusted third party. Request text is pseudonymised before every model turn, with reversible tokens. The token↔value map lives in an isolated Cosmos DB account encrypted with a customer-managed key in Key Vault HSM, with a TTL tied to the ticket lifecycle, reachable only by the app-tier managed identity. Indexes, caches, traces and eval datasets contain pseudonyms only. Re-identification happens at render time for entitled users. The pseudonymiser fails closed.

**Injection defence in depth.** Content Safety and Prompt Shields run at APIM *and* inside Foundry guardrails, because retrieved documents are themselves an injection vector (XPIA). The stronger control is architectural: PII is redacted at index time, so injection cannot exfiltrate what was never indexed. APIM's egress policy scans outbound prompts and fails closed.

**Grounding as a security property.** Both agents use paired critics that **block** rather than caveat. `NOT_GROUNDED` with a structured handover is the correct output when evidence is absent.

**Least privilege on the Predictive Agent.** Its Entra scope has no write access beyond its own Cosmos container and its own Service Bus topic. No tool in its set can mutate a live system. This is enforced by role assignment, not by prompt instruction.

**Network.** VNet integration with Private Endpoints on every PaaS service, Private DNS for resolution, NSGs on subnets, Azure Firewall with an outbound allow-list, and Foundry BYO VNet with no public egress.

**Governance.** Purview DSPM for AI provides sensitivity labels, EXTRACT rights, DLP on agent data access and a unified AI audit log. Azure Policy enforces configuration. Defender for Cloud covers posture.

## 8. Reliability and operations

| Concern | Design |
|---|---|
| Multi-region | Active-active across Australia East / Australia Southeast; Front Door health probes drive failover |
| Data durability | Cosmos DB multi-region write; AI Search replicas ≥ 3; index rebuildable from source-of-truth |
| Graceful degradation | Synchronous knowledge queries fall back to retrieval-only mode under load rather than failing |
| Capacity isolation | Interactive agents hold PTU reservation; the Predictive batch workload runs PAYG spillover so it cannot starve interactive traffic |
| Async resilience | Service Bus dedup + dead-letter queues; Durable Functions retry with exponential backoff and jitter; idempotent message processing |
| Cost / performance KPI | Semantic cache hit ratio is a first-class metric, tracked in APIM token metrics |
| Traceability (P4) | One correlation ID spans channel → APIM → Service Bus → Durable Functions → agent → tool → ITSM. Every output is a signed immutable run record in ADLS: inputs, chunk IDs and versions, model and prompt version, tool calls, critic verdict, human decision, outcome |
| Quality regression | Foundry Evaluations + ASSERT run groundedness, relevance and safety against production traces; Agent Optimizer drives improvement |

## 9. Trade-offs and alternatives

**Classical models instead of LLM forecasting (Predictive).** Costs an extra Azure ML plane to build and maintain, and the agent cannot answer forecasting questions no model was trained for. Bought in exchange: reproducibility, auditability, and far fewer false positives. For an ops watchlist that humans must trust weekly, this is the right trade.

**Blocking instead of caveating.** A blocked answer is a worse user experience than a hedged one, and will generate "why didn't it answer" complaints early on. But a caveated wrong answer in an incident is materially more expensive than a handover. The `/gaps` endpoint turns each block into a KB backlog item, so blocks compound into coverage.

**Draft-PR-only curation.** Slower knowledge freshness and ongoing knowledge-owner review load. The alternative — self-publishing — puts unreviewed model output into the corpus that grounds every other agent, where a single bad article propagates. Not worth it.

**De-identification on every model turn.** Adds latency and a hard dependency on the PII tier, which fails closed. The alternative is trusting the model boundary with raw customer and staff data, which the design explicitly rejects.

**Foundry IQ as the retrieval layer.** An SLA-backed managed endpoint over calling Azure AI Search directly; less control over the retrieval pipeline in exchange for less code to own. Direct AI Search calls remain available for tools that need custom ranking.

**Advisory-first autonomy.** The Predictive Agent produces no operational leverage until humans act on it. Promotion to higher autonomy is gated on the accuracy record rather than on a launch date, which is slower but means scope is earned on evidence (P5).
