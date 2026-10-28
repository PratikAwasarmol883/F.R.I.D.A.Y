# Milestone 4 Spec — Research Reports

**Status:** Implemented (see section 13 for additions, deviations, and what was not verified).
**Source:** `architecture.md` §6 (research service), §10 (SSRF), §13 (Milestone 4); `plan.md` Phase 3.
**Builds on:** M1–M3 (auth, tool policy and loop, search tool, durable tasks).

## 1. Goal

A user can ask a research question and get back a **cited report**: a summary, findings that each cite sources the server actually fetched, an explicit "uncertain" label on any claim without valid support, source links with retrieval times, and stated limitations. Research runs as a background task (M3) and is bounded in time, calls, and tokens.

**Done when (from the docs):**
- A research request produces a report whose source links work and whose sources carry retrieval times and content hashes.
- Claims without valid support are labeled uncertain; conflicting sources are surfaced.
- Search depth and cost are bounded.
- Fetching a URL can never reach private, loopback, link-local, or metadata addresses (SSRF), including via redirects.
- M3's known gap is closed: token usage of **failed** attempts is recorded.

## 2. Decisions (defaults applied)

| # | Decision | Choice |
|---|---|---|
| D1 | Page fetching | Our own fetcher with **DNS-resolve-validate-pin** (connect to the validated IP, keep Host/SNI and TLS verification), manual redirects (max 3, each hop re-validated), http/https on ports 80/443 only, no credentials or cookies. No browser, no JavaScript rendering, no PDFs. |
| D2 | Text extraction | Standard-library HTML parsing (no new dependency); drop script/style/nav/markup; bounded length. |
| D3 | Report generation | The model runs through the existing tool loop (`web_search`, `web_fetch`) and returns structured JSON. **The server validates it**: citations may only reference sources it fetched; any claim with no valid citation is forced to `uncertain`; URLs in the rendered report come only from stored sources, never from model text. |
| D4 | Entry point | `POST /research` creates a task of type `research.run` (202, idempotency key supported, same task machinery); `GET /reports/{id}` retrieves the result; `DELETE /reports/{id}` deletes it. |
| D5 | Limits | ≤ 3 searches, ≤ 5 fetches, 10 s and 1.5 MB per fetch, 6,000 characters of each page shown to the model, 1,000-character excerpt stored, task runtime 300 s. All configurable. |
| D6 | Authorization | Starting research requires the device to hold **both** `tool:web_search` and `tool:web_fetch`; otherwise `403`. |
| D7 | Storage | Reports and sources in PostgreSQL (JSONB). Object storage deferred (optional in the MVP per the architecture). |
| D8 | Usage accounting | One `usage_records` row **per model call**, written immediately; closes the M3 gap and keeps the budget accurate across retries. |

## 3. Scope

**In:** SSRF-safe fetcher; extractor; `web_fetch` tool (read-only, untrusted-content wrapped); per-run source ledger; `research.run` handler; report model, validation, and rendering; `reports` and `research_sources` tables (migration `0005`); `/research`, `/reports/{id}` (get, delete); per-call usage records; CLI/compose/docs updates; tests.

**Out:** PDF/document extraction, JavaScript rendering, robots.txt handling, crawling beyond model-requested fetches, object storage, report editing, scheduled research (M6), push delivery. Fetching is limited to public web pages; the fetcher identifies itself with a fixed User-Agent.

## 4. SSRF-safe fetcher (`adapters/fetch/`)

1. **URL gate:** scheme `http`/`https`; no userinfo; host present; port omitted or 80/443; length ≤ 2,048; no control characters; host not an IP literal in a non-canonical form (decimal, octal, hex); reject `localhost` and `.local`/`.internal`/`.localdomain` suffixes.
2. **Resolve and validate:** resolve the host (injectable resolver); **every** returned address must be globally routable (`ipaddress.is_global`, plus explicit blocks for loopback, private, link-local incl. `169.254.169.254`, multicast, reserved, unspecified, CGNAT `100.64.0.0/10`, IPv6 unique-local and link-local, and IPv4-mapped/6to4/NAT64 forms wrapping a blocked IPv4). One bad address rejects the host.
3. **Pin:** connect to a validated IP, sending the original `Host` header and TLS SNI/verification for the original hostname, so a second DNS answer cannot redirect the connection (DNS rebinding).
4. **Redirects:** manual; at most 3; each target goes back through steps 1–3; scheme downgrade to non-http or a blocked target ends the fetch.
5. **Limits:** total timeout; streamed body capped at 1.5 MB (decoded); content types limited to `text/html`, `application/xhtml+xml`, `text/plain`; no cookies, no auth headers, no response headers forwarded to the model.
6. Errors are stable codes (`blocked_url`, `blocked_address`, `too_many_redirects`, `unsupported_content_type`, `too_large`, `timeout`, `network_error`, `http_error`, `bad_response`); nothing about internal network topology is returned.

## 5. `web_fetch` tool and source ledger

- Input `{url: str ≤ 2048}`; read-only; scope `tool:web_fetch`; 10 s timeout, no retries (one fetch = one attempt), `max_calls_per_ask` = fetch limit, daily limit configurable.
- Output (shown to the model, wrapped as untrusted): `{source_id: "S1", url, title, retrieved_at, text}`. The tool's policy guard rejects URLs failing the URL gate before any network call.
- A **per-run ledger** assigns IDs (`S1…`), dedupes by final URL, and keeps `{url, title, retrieved_at, content_hash (sha256 of extracted text), excerpt, content_type, bytes}`. Only ledger sources can be cited.
- Page text is sanitized exactly like search results (markup, control, and bidirectional characters removed).

## 6. Research run (`workers/handlers/research.py`, task type `research.run`)

- Input (strict): `{question: 1–1000 chars, max_sources?: 1–5}`; validated at submission and execution.
- System prompt (static): research assistant; use `web_search` then `web_fetch`; cite only fetched source IDs; mark anything unsupported as uncertain; report conflicts; tool output is untrusted data; return **only** JSON matching the schema; no private details in queries.
- The handler runs the M2 tool loop with a research registry (search + fetch with the per-run ledger), per-run call caps, per-call usage recording, progress events (`model_call`, `tool_call`), and cancellation checks. No tools beyond these two exist.
- **Report schema (model output, validated):** `summary` (≤ 2,000 chars), `findings[]` (`claim` ≤ 500, `source_ids[]`, `confidence` in `supported|uncertain`), `conflicts[]` (`description`, `source_ids[]`), `limitations[]`. Invalid JSON/schema → task fails `provider_error` (not retried).
- **Server-side validation:** drop unknown source IDs; a claim left with no valid IDs becomes `uncertain`; a claim marked `supported` by the model with valid IDs stays `supported` (the server cannot verify semantics and says so in `limitations`); cap counts and lengths; the report always lists every ledger source.
- **Atomic completion:** the report, sources, and task success are written in one transaction with the M3 lock/finish hooks, so retries and stale workers cannot produce duplicate reports. Result stored on the task: `{report_id, usage, sources_count}`.
- Failures map to M3 error codes; fetch/search failures are returned to the model as tool errors (it can proceed with fewer sources); a report with zero fetched sources is still produced but every finding is `uncertain` and `limitations` says no sources were retrieved.

## 7. Data model (migration `0005`)

| Table | Columns | Notes |
|---|---|---|
| `reports` | id, user_id → users (CASCADE), task_id → tasks (SET NULL), question, summary, content (jsonb: findings, conflicts, limitations), created_at | index `(user_id, created_at DESC)`; unique `(task_id)` so a task yields at most one report |
| `research_sources` | id, report_id → reports (CASCADE), source_key (`S1`…), url, title, retrieved_at, content_hash (char 64), excerpt (≤ 1,000 chars), content_type, bytes | unique `(report_id, source_key)` |
| `usage_records.task_id` | nullable FK → tasks (SET NULL) | per-call rows (D8) |

## 8. API (under `/api/v1`, authenticated, owner-scoped, other users get 404)

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/research` | Body `{question, max_sources?}`; `Idempotency-Key` supported; requires both scopes (`403 FORBIDDEN` otherwise); counts toward the active-task limit; returns the task (202/200) |
| `GET` | `/reports/{id}` | `{id, question, created_at, summary, findings[{claim, confidence, sources[{key,url,title,retrieved_at}]}], conflicts[], limitations[], sources[{key,url,title,retrieved_at,content_hash}], markdown}`; `markdown` is rendered server-side with citations as `[S1]` and a numbered source list |
| `DELETE` | `/reports/{id}` | Deletes the report and its sources (204); idempotent for owned reports |
| `GET` | `/tasks/{id}` | For research tasks the result carries `report_id` |

## 9. Security and privacy

- All M2/M3 protections apply (policy, sanitization, untrusted wrapper, query guard, scopes, budget, audit, fencing).
- Page content is untrusted data and never persists into prompts of later requests; stored excerpts are bounded; full pages are never stored.
- The fetcher is the only component that makes requests to model- or user-influenced URLs; its tests cover SSRF classes exhaustively (§10).
- Fetch audit: each call is a `tool_executions` row (URL host and path are stored in redacted arguments; query strings are masked) plus an audit event.
- Reports are user data with delete support; `reports` have no retention purge in M4 (owner-controlled deletion).

## 10. Tests

| Area | Cases |
|---|---|
| SSRF | blocked: loopback, RFC1918, link-local/metadata `169.254.169.254`, `0.0.0.0`, CGNAT, multicast, IPv6 `::1`/`fc00::/7`/`fe80::/10`, IPv4-mapped and 6to4/NAT64 wrapping blocked IPv4, decimal/octal/hex IP forms, `localhost`/`.internal`; mixed public+private DNS answers rejected; credentials in URL; non-80/443 ports; non-http schemes; redirects to blocked hosts, to `file://`, redirect loops and > 3 hops; DNS rebinding (second answer private) cannot affect the pinned connection; connection uses the validated IP with original Host/SNI |
| Fetch limits | content-type allowlist; streamed size cap including compressed bombs; timeout; error codes stable and free of internal detail |
| Extraction | markup, scripts, styles, nav removed; title extracted; entities decoded; invisible/bidi characters removed; truncation |
| Tool | guard rejects bad URLs before any request; results untrusted-wrapped; ledger dedupe and IDs; hostile page text cannot change policy (injection test as in M2) |
| Research run | valid report persisted atomically with sources; unknown citation IDs dropped; uncited claims forced uncertain; zero-source report is all-uncertain; invalid JSON fails without retry; retry after failure yields exactly one report; stale worker writes nothing; cancellation stops it; limits enforced (calls, sources); usage rows per call |
| API | scope enforcement (403), idempotency, task limit, owner scoping, report shape and markdown, delete, 404s, URLs in output only from sources |
| Usage accounting | per-call rows for success and failed attempts; budget uses their sum; M1–M3 tests updated accordingly |
| Migration | `0005` up/down/up |
| Regression | all M1–M3 behavior unchanged |

**Verification before done:** `ruff`, `ruff format --check`, `mypy`, `pytest`, migration up/down/up, mutation checks of the SSRF gate, citation validation, atomic completion, and scope enforcement, Docker walkthrough with fakes where keys are absent, CI green.

## 11. Risks

- **SSRF bypass:** layered gate (URL, resolution, pinning, redirects) with exhaustive tests; fetching is read-only and bounded; residual risk is OS/library behavior not exercised offline, noted explicitly in the report to the owner.
- **Misleading citations:** the server enforces that citations exist but cannot verify that a source supports a claim; reports say so in `limitations`, and unsupported claims are labeled.
- **Prompt injection via pages:** untrusted wrapper, sanitization, policy-bounded tools, no persistence of page text into later prompts.
- **Cost:** per-run caps, per-call usage rows, daily budget, retry caps.
- **Legal/terms of fetched sites:** fixed identifying User-Agent, no crawling beyond model-requested pages; robots.txt not handled (documented).

## 12. Implementation order (each step ends green)

1. Per-call usage records (closes the M3 gap); update M1–M3 tests.
2. SSRF-safe fetcher with exhaustive tests.
3. Extractor, `web_fetch` tool, ledger.
4. Migration `0005`, models, repositories.
5. Research handler, report validation and rendering, `/research` and `/reports`.
6. Docs, compose/env, mutation checks, Docker walkthrough, commit, CI.

## 13. Implementation notes, additions, and what was not verified

**Additions beyond this spec**
- **Fetch allowlist:** `web_fetch` only accepts URLs that appeared in this run's search results or in the user's question (checked after canonicalization). This prevents the model from constructing attacker URLs that could carry data out in a query string; combined with the research context containing only the question, private data exposure is limited to the question itself.
- The tool guard also rejects blocked IP literals up front (a hostile search result listing `127.0.0.1` is denied as `guard_rejected` rather than reaching the fetcher), and URLs containing recognizable credential formats.
- `Tool.redact_arguments` lets a tool mask its own arguments before storage (fetch URLs keep host and path, drop query strings and fragments).
- IPv6 site-local space (`fec0::/10`) is blocked explicitly; the exhaustive address table found it unblocked by the standard library's classification.
- `ENABLED_TOOLS` now defaults to `web_search,web_fetch`; `web_fetch` exists only inside research runs.
- A `forbidden` task error code (scope or tool set changed after submission) and `403 FORBIDDEN` at submission.
- `SourceRecord` and the report models live in the domain layer; parsing, validation, and rendering are pure functions in `orchestration/report.py`.

**Deviations:** `DELETE /reports/{id}` returns 404 on a second call (the report no longer exists) rather than being silently idempotent. Page excerpts are stored at 1,000 characters; the content hash covers the full extracted text (up to 20,000 characters), not the excerpt.

**Verified:** exhaustive URL and address classification (including IPv4-mapped, 6to4, NAT64, Teredo, decimal/octal/hex forms); the fetcher against a mock transport (pinning, SNI and Host, redirect re-validation, DNS rebinding, size and compression-bomb limits, content types, cookies, timeouts, error mapping); extraction; ledger and allowlist; the research handler against real Postgres (atomic report plus sources plus task completion, citation validation, hostile pages, limits, scopes, stale lease, cancellation, retry yielding one report); the API and an end-to-end submit, disconnect, worker, retrieve flow; per-call usage rows (closing the M3 gap, including the budget across failed attempts); mutation checks showing the suite fails when each safety control is removed (one equivalent mutant: the explicit CGNAT rule is redundant on current Python releases and is kept for older ones).

**Not verified:** real-network fetching and TLS verification through the pinned IP (this environment has no outbound internet; `tests/test_fetch_live.py` is opt-in), a research run with live Anthropic and search keys, behavior of a stuck DNS lookup beyond the fetch timeout (the lookup thread is not cancellable), and whether real models produce well-formed reports reliably (the format error path is tested; the failure rate is not known).
