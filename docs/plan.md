# FRIDAY --- Personal AI Assistant

## Product Plan, Use Cases, Architecture, and Roadmap

**Status:** Initial planning document\
**Project name:** FRIDAY\
**Primary goal:** Build a personal, voice-first AI assistant with a
cloud-hosted brain that can research, remember, plan, and execute
approved tasks across a MacBook and phone.

------------------------------------------------------------------------

## 1. Vision

FRIDAY is a personal AI agent inspired by fictional assistants such as
Tony Stark's FRIDAY. The practical goal is not to recreate science
fiction, but to build a reliable assistant that can:

1.  Understand natural-language and voice requests.
2.  Plan multi-step tasks.
3.  Use real tools and APIs rather than guessing.
4.  Gather and verify evidence.
5.  Remember user-approved preferences and project context.
6.  Run scheduled and event-driven jobs in the cloud.
7.  Report what it did, what it found, and what still needs attention.
8.  Ask for approval before consequential or destructive actions.

**Core interaction loop:** Understand → Plan → Execute tools → Verify →
Report.

## 2. Product principles

-   **Useful over flashy:** Prioritize workflows that save time or solve
    real problems.
-   **Evidence over confidence:** Cite sources, distinguish facts from
    assumptions, and report uncertainty.
-   **Human control:** Require approval for sensitive, destructive,
    financial, external-facing, or production-impacting actions.
-   **Least privilege:** Give each integration only the access it needs.
-   **Secure by default:** Authenticate clients, protect secrets, use
    HTTPS, log actions, and isolate untrusted content.
-   **Cost-aware:** Set limits on searches, model calls, background
    jobs, storage, and audio usage.
-   **Device-independent brain:** Cloud tasks can continue while
    personal devices are offline; local device actions require that
    device to be online.
-   **Privacy-conscious memory:** Store only useful information, support
    review and deletion, and avoid collecting sensitive personal
    information unnecessarily.

## 3. Target interfaces

### 3.1 MacBook client

-   Wake-word detection for "Hey Friday".
-   Microphone capture, speech-to-text, and spoken responses.
-   Optional desktop controls for opening apps, searching files, and
    running approved local scripts.
-   Repository and terminal access only through explicitly configured
    tools and permissions.

### 3.2 Phone client

-   Voice and text interface.
-   Access to shared conversation history and cloud-generated reports.
-   Push notifications for completed tasks and important alerts.
-   Mobile background microphone behavior depends on OS permissions and
    platform restrictions; always-on wake-word listening is not
    guaranteed.

### 3.3 Cloud dashboard

-   Research reports and task history.
-   Upcoming reminders and scheduled jobs.
-   Connected integrations and permissions.
-   Usage and cost visibility.
-   Audit history, memory controls, and approval queue.

### 3.4 Bluetooth earpiece

-   Acts as microphone and audio output when paired with an available
    phone or computer.
-   It is not an independent cloud client unless it has its own
    connectivity and compute, which ordinary Bluetooth earpieces
    generally do not.

## 4. Consolidated use-case catalog

### A. Voice, devices, and assistant basics

1.  **Hands-free voice assistant:** Ask questions, dictate ideas, get
    explanations, and hear spoken answers.
2.  **Computer operator:** Open apps, find files, inspect screenshots,
    and run approved scripts.
3.  **Cross-device continuity:** Continue a conversation from Mac to
    phone with shared history.
4.  **Cloud brain:** Perform research, retain approved memory, and run
    scheduled server-side tasks while devices are off.
5.  **Persistent personal memory:** Store user-approved preferences,
    project decisions, notes, and ongoing goals with controls to review
    or delete them.

### B. Research and public-source intelligence

6.  **Deep web research:** Search multiple sources, inspect accessible
    pages, compare evidence, and produce cited reports.
7.  **Technical research:** Compare frameworks, cloud services, APIs,
    architectures, pricing, and open-source projects.
8.  **Public-source OSINT:** Research public professional profiles,
    published work, company biographies, news, and public websites.
9.  **Public social-media research:** Analyze accessible public
    Instagram/Facebook content or authorized exports where platform
    terms and access allow it.
10. **Research report generation:** Produce an executive summary, source
    links, evidence, conflicting claims, confidence notes, and next
    steps.
11. **Overnight research:** Run scheduled research on selected topics
    and prepare a morning briefing.

**OSINT boundaries:** Do not bypass logins or privacy settings, access
private accounts or messages, obtain credentials, or compile sensitive
personal details such as home addresses or live location. Confirm
identity carefully to avoid mixing up people with similar names. Use
official APIs where eligible and respect platform terms.

### C. Developer productivity

12. **AI code review:** Inspect Git diffs and pull requests for bugs,
    security issues, missing tests, and performance risks.
13. **Debugging agent:** Correlate authorized logs, traces,
    configuration, and source code to find likely root causes.
14. **Build-and-test agent:** Work on a separate branch, implement
    scoped changes, run approved tests, and show the diff.
15. **Project-aware knowledge assistant:** Search authorized
    repositories, PDFs, architecture docs, tickets, logs, and notes.
16. **Workflow automation:** Trigger approved scripts, CI jobs, API
    calls, database queries, scanner workflows, or ticket creation.
17. **SQL and data analyst:** Run read-only or explicitly approved
    queries, analyze datasets, detect anomalies, and create summaries or
    charts.
18. **PDF-to-JSON extraction:** Extract structured fields from PDFs,
    validate schemas, and return normalized JSON.
19. **Project status briefing:** Summarize activity across connected
    repositories, issue trackers, tickets, and communication tools.

### D. Security and authorized operations

20. **Asset discovery:** Discover domains, subdomains, and services
    within an explicitly authorized scope.
21. **Vulnerability intelligence:** Track relevant public advisories and
    correlate them with an approved asset inventory.
22. **Digital exposure monitoring:** Check authorized assets for
    unintended public exposure or configuration leaks.
23. **Incident investigation:** Build timelines from authorized logs,
    alerts, metrics, and deployment changes.
24. **Security report generation:** Prioritize findings, include
    evidence, explain impact, and suggest remediation.

**Security boundaries:** Only scan or test systems owned by the user or
covered by explicit authorization. Enforce scope allowlists, rate
limits, audit logs, and approval gates. Never execute arbitrary commands
supplied by untrusted webpages or messages.

### E. Navigation and situational awareness

25. **Fastest/shortest route:** Compare distance, travel time, traffic,
    tolls, and route preferences using a maps/routing service.
26. **Travel planning:** Suggest departure times and alternatives using
    available traffic and weather data.
27. **Personal situation briefing:** With permission, combine calendar,
    local conditions, and upcoming plans into a concise briefing.

**Limitations:** Accurate routing requires a routing provider and
relevant live data. Do not claim live location access without permission
or invent route times. If both phone and Mac are off, FRIDAY cannot hear
a wake word or operate those devices.

### F. Personal productivity

28. **Daily planner:** Organize work, gym, learning, errands, and
    commitments.
29. **Email and follow-up manager:** Summarize authorized email, draft
    replies, and track unanswered requests.
30. **Career and learning coach:** Maintain a learning roadmap, create
    practice questions, and track progress.
31. **Freelance opportunity assistant:** Find relevant projects, compare
    requirements with skills, draft proposals, and track applications.
32. **Knowledge archive:** Organize notes, code snippets, conversations,
    and learning summaries into searchable records.
33. **Scheduled reports:** Generate daily or weekly summaries from
    selected projects, datasets, logs, or research topics.

### G. Proactive, event-driven agents

34. **Event-driven alerts:** Respond to configured events such as
    errors, advisories, deadlines, replies, or releases.
35. **Multi-step task orchestration:** Break a goal into steps, call
    tools, verify results, retry safe failures, and report completion.
36. **Goal-based monitoring:** Track a specific unresolved dependency,
    such as a pending application, promised reply, or expected release.
37. **Deployment watch:** Monitor a deployment, check health endpoints
    afterward, and alert if verification fails.

### H. Money and paperwork

38. **Expense intelligence:** Analyze authorized statements and
    receipts, find recurring charges, and flag possible duplicate or
    unusual charges.
39. **Refund assistant:** Check official policies, match receipts to
    charges, prepare refund requests, and track responses.
40. **Document intelligence:** Read authorized PDFs, invoices,
    warranties, insurance policies, agreements, and receipts.
41. **Deadline tracker:** Extract renewal dates, payment deadlines,
    notice periods, and required documents.
42. **Claim and renewal preparation:** Find supporting documents,
    assemble checklists, draft correspondence, and track completion.

**Financial/document boundaries:** FRIDAY may analyze, draft, and
remind, but should not move money, cancel services, submit consequential
claims, or make legal/financial decisions without user review. Confirm
critical dates against original documents.

### I. Additional integrations

43. **Screenshot understanding:** Explain errors and identify UI
    elements in screenshots the user provides.
44. **Smart-home control:** Operate compatible devices with explicit
    permissions.
45. **Authorized account and service integrations:** Connect email,
    calendar, GitHub, cloud services, and other systems through
    supported APIs.
46. **Notification delivery:** Deliver task completion, reminders, and
    important alerts to the phone or dashboard.

## 5. Prioritization

Use these criteria to select work: - User value and frequency. -
Feasibility and integration complexity. - Risk and permission
requirements. - Ongoing API/hosting cost. - Reuse of shared
infrastructure.

### Recommended first three capabilities

1.  **Voice + cloud brain:** A working assistant accessible from Mac and
    phone, with memory and secure authentication.
2.  **Deep research + scheduled reports:** Search, source reading,
    evidence comparison, cited reports, and morning delivery.
3.  **Developer assistant:** Repository analysis, code review,
    debugging, and test execution in a controlled environment.

These deliver visible value early and establish the shared architecture
needed for later modules.

## 6. Architecture

### 6.1 Logical architecture

``` text
Mac client ───────┐
                  ├── HTTPS/WebSocket ──> FastAPI backend
Phone client ─────┘                          |
                                             v
                                      Agent orchestrator
                                             |
                      ┌──────────────────────┼─────────────────────┐
                      v                      v                     v
                 LLM provider          Tool registry          Memory layer
                                         /  |  \              /       \
                                        v   v   v             v         v
                                      Search Git Maps      PostgreSQL  Object storage
                                      Email  Code  Jobs    or MongoDB  reports/files
                                             |
                                             v
                                  Scheduler / background workers
                                             |
                                             v
                                  Push notifications / dashboard
```

### 6.2 Backend responsibilities

-   Authenticate clients and authorize each request.
-   Manage conversations, sessions, and user-approved memory.
-   Route requests to the appropriate tools.
-   Track task state, retries, timeouts, and results.
-   Store citations and evidence for research.
-   Apply approval policies before sensitive actions.
-   Record an audit trail.
-   Enforce per-user and per-task cost limits.

### 6.3 Suggested stack

-   **Language/backend:** Python + FastAPI.
-   **AI:** LLM API supporting tool/function calling.
-   **Voice:** Wake-word engine, speech-to-text, text-to-speech.
-   **Database:** PostgreSQL for core entities and task state; MongoDB
    is also reasonable if preferred. Avoid operating multiple databases
    before there is a clear need.
-   **Files/reports:** Object storage compatible with S3.
-   **Background jobs:** Start with a simple reliable queue/scheduler;
    consider Prefect for longer workflows or existing familiarity.
-   **Search:** Search API plus controlled webpage extraction.
-   **Mobile/Mac:** Start with a simple authenticated web or desktop
    client; native mobile integration may be needed for background audio
    and notifications.
-   **Observability:** Structured logs, error tracking, metrics, task
    traces, and usage accounting.

## 7. Core service modules

Suggested logical modules (adjust names to the repository structure):

``` text
friday/
  api/                 # HTTP endpoints and request schemas
  agent/               # planning, tool selection, orchestration
  auth/                # authentication and authorization
  memory/              # preferences and conversation context
  tools/
    web_research/
    documents/
    github/
    navigation/
    email_calendar/
    security/
    finance/
  voice/               # speech input/output and client protocols
  tasks/               # background jobs, retries, task state
  notifications/       # push/email/dashboard notifications
  policies/            # approvals, scopes, risk classification
  storage/              # database and object-storage adapters
  observability/       # audit logs, metrics, tracing
  tests/
```

This is a target organization, not a requirement to create every module
on day one.

## 8. Data model concepts

Minimum entities: - **User:** identity, settings, and preferences. -
**Device:** registered clients, revocation status, and notification
token. - **Conversation:** messages and session metadata. - **Memory:**
user-approved facts/preferences, source, timestamp, and deletion
controls. - **Task:** goal, status, steps, timestamps, retry count, and
outcome. - **Tool execution:** tool name, validated input, result
summary, duration, and approval reference. - **Research source:** URL,
title, retrieval time, extracted claims, and citation metadata. -
**Scheduled job:** schedule, scope, limits, last run, and next run. -
**Approval request:** proposed action, risk level, requester, decision,
and expiry. - **Usage record:** model, token/audio/search usage,
estimated cost, and budget category. - **Audit event:** who/what
initiated an action, what happened, and whether it succeeded.

Do not store secrets, access tokens, or sensitive personal data in
ordinary conversation memory.

## 9. API surface (initial proposal)

-   `GET /v1/health` --- service health.
-   `POST /v1/assistant/ask` --- text question or instruction.
-   `POST /v1/assistant/voice` --- voice request or audio upload.
-   `GET /v1/conversations/{id}` --- authorized conversation history.
-   `POST /v1/research` --- start a research task.
-   `GET /v1/tasks/{id}` --- task status and result.
-   `POST /v1/tasks/{id}/cancel` --- cancel a cancellable task.
-   `GET /v1/reports/{id}` --- retrieve an authorized report.
-   `GET /v1/approvals` --- list pending approvals.
-   `POST /v1/approvals/{id}` --- approve or reject a proposed action.
-   `GET /v1/memory` and `DELETE /v1/memory/{id}` --- review and delete
    saved memory.
-   `GET /v1/usage` --- view usage and cost summaries.

All endpoints must authenticate callers and enforce
ownership/authorization checks.

## 10. Task lifecycle

Use an explicit task state machine:

``` text
QUEUED -> RUNNING -> VERIFYING -> SUCCEEDED
                    |              |
                    v              v
                  FAILED        NEEDS_APPROVAL
                                    |
                              APPROVED / REJECTED
```

Also support `CANCELLED` and `TIMED_OUT`. Persist state so a worker
restart does not lose a long-running task. Make retried operations
idempotent where possible.

## 11. Security, privacy, and reliability

-   Require authentication for every client and API.
-   Use HTTPS in production and securely manage credentials.
-   Store provider keys in server-side secret storage, never in the
    mobile app or frontend.
-   Apply least-privilege OAuth scopes and revokeable device sessions.
-   Treat web pages, emails, PDFs, and repository files as untrusted
    input; they must never be allowed to override system policy or
    trigger tools directly.
-   Validate and constrain tool inputs.
-   Use allowlists and sandboxing for shell commands and code execution.
-   Separate read-only operations from write operations.
-   Require explicit confirmation for sending external messages, making
    payments, deleting data, changing permissions, or production
    deployments.
-   Keep audit logs without unnecessarily recording sensitive content.
-   Provide data export and deletion controls.
-   Add rate limits, timeouts, retries, quotas, and budget caps.
-   Back up important task state and reports.
-   Do not expose a development server or unauthenticated API directly
    to the public internet.
-   Use authorized scope boundaries for security scanning and OSINT
    research.

## 12. Cloud and offline behavior

When the Mac and phone are off, cloud-side features can continue if the
cloud backend, database, and worker are running: - Web research and
report generation. - Scheduled tasks and reminders. - Cloud-stored
memory and task history. - Monitoring configured against accessible
services. - Notifications queued for later delivery.

They cannot continue while both devices are off: - Listening for "Hey
Friday" through a normal Bluetooth earpiece. - Controlling local Mac
apps or files. - Using phone-only sensors or local apps. - Speaking
through an earpiece that is disconnected or powered off.

When a client reconnects, it can fetch task results and queued
notifications.

## 13. Cost plan

Treat these as initial planning bands rather than vendor quotes; actual
costs depend on region, provider, usage, and free-tier changes.

-   **Prototype/local-first:** roughly ₹0--₹500/month beyond existing
    hardware, with limited API use.
-   **Small cloud brain:** target approximately ₹800--₹1,500/month for
    light personal usage.
-   **Moderate usage:** roughly ₹1,500--₹3,300/month depending on model
    calls, search, database, and hosting.
-   **Heavy research/voice/automation:** ₹2,500--₹5,000+ per month may
    be needed.

Cost controls: - Use a small server first. - Run wake-word detection
locally. - Use smaller/cheaper models for classification and simple
requests. - Limit research source count and agent iterations. - Cache
repeated search and document results where appropriate. - Track costs
per task and feature. - Set alerts and hard limits where providers
support them. - Avoid always-on streaming audio until there is a clear
need.

## 14. Phased roadmap and acceptance criteria

### Phase 0 --- Project foundation

**Deliverables** - Repository and configuration structure. -
`.env.example`, secret handling, linting, formatting, and test setup. -
Architecture decisions and threat model.

**Done when** - App starts locally. - Secrets are not committed. - Basic
CI runs tests and lint checks.

### Phase 1 --- Text assistant and secure cloud API

**Deliverables** - FastAPI service. - Authentication. - LLM
request/response service. - Conversation/session persistence. - Basic
audit logging and usage accounting.

**Done when** - Authenticated user can ask a question and retrieve
history. - Invalid credentials are rejected. - Provider errors and
timeouts are handled safely.

### Phase 2 --- Voice on Mac

**Deliverables** - Wake-word detection. - Audio capture, transcription,
response generation, and speech output. - Device selection and recovery
from microphone/speaker errors.

**Done when** - User can invoke FRIDAY by voice and hear a response. -
The app handles missing audio devices and API failures without
crashing. - Voice recording and transmission are clearly indicated.

### Phase 3 --- Deep research

**Deliverables** - Search adapter. - Page fetch/extraction with timeouts
and size limits. - Source/claim representation and citation
generation. - Report storage and retrieval.

**Done when** - A research request produces a report with working source
links. - Unverified claims are labeled and conflicting sources are
surfaced. - Search depth and cost are bounded.

### Phase 4 --- Cloud worker and scheduler

**Deliverables** - Durable task queue. - Task status and retry
behavior. - Scheduled jobs. - Notifications and cloud reports.

**Done when** - A queued task completes when clients are offline. - Task
state survives a worker restart. - Duplicate execution is prevented or
safely handled.

### Phase 5 --- Phone and cross-device access

**Deliverables** - Authenticated phone/web client. - Shared conversation
and report history. - Push or in-app notifications. - Device
registration and revocation.

**Done when** - The user can start a task on one client and read the
result on another. - Revoked devices can no longer access the account. -
Mobile platform constraints are documented.

### Phase 6 --- Developer agent

**Deliverables** - Git/repository integration. - Read-only analysis
first. - Sandboxed test execution. - Branch-based changes and diff
review.

**Done when** - FRIDAY can explain a change using repository evidence. -
Tests run in a controlled environment. - It never merges or deploys
without explicit approval.

### Phase 7 --- Documents and productivity

**Deliverables** - PDF/document extraction. - Task and deadline
tracking. - Calendar/email integrations where authorized. - Reminder and
follow-up workflows.

**Done when** - Extracted dates include a source reference. - Critical
dates require confirmation. - Users can inspect and edit tasks.

### Phase 8 --- Security and proactive agents

**Deliverables** - Authorized asset inventory integration. - Advisory
correlation. - Alert rules and incident summaries. - Approval workflow
for write or active-scanning actions.

**Done when** - Jobs stay within an explicit allowlisted scope. -
Findings include evidence and timestamps. - Risky actions are blocked
until approval.

### Phase 9 --- Finance, navigation, and additional integrations

**Deliverables** - Expense/receipt analysis. - Refund preparation. -
Maps/routing integration. - Smart-home and other integrations only when
needed.

**Done when** - Outputs cite source records. - No financial transaction
or consequential external action occurs without approval. - Routes and
live data are identified with source and retrieval time.

## 15. Testing strategy

-   **Unit tests:** tool input validation, cost limits, permissions,
    task transitions, and extraction functions.
-   **Integration tests:** LLM provider, search provider, database,
    scheduler, and notification adapters.
-   **End-to-end tests:** voice/text request → tool execution →
    verification → report delivery.
-   **Security tests:** unauthenticated access, IDOR/ownership checks,
    prompt injection from web pages, unsafe command execution, secret
    leakage, and scope bypass.
-   **Reliability tests:** worker restarts, retries, timeouts, duplicate
    events, unavailable providers, and database outages.
-   **Evaluation tests:** citation correctness, extraction accuracy,
    false claims, tool selection, and approval enforcement.
-   **Cost tests:** ensure a single task cannot exceed configured
    search, model, time, or money budgets.

## 16. Initial MVP scope

Do not attempt all 46 use cases in the first release. The MVP should
include only:

1.  Secure FastAPI backend.
2.  Text-based assistant with tool calling.
3.  Persistent conversation history.
4.  Voice interaction on Mac.
5.  Deep web research with citations.
6.  Background task execution and report storage.
7.  A simple phone-accessible interface.
8.  Usage tracking and hard operational limits.

Once this is stable, add repository analysis, document/deadline
management, notifications, and security workflows.

## 17. Initial project checklist

-   [ ] Create repository and Python environment.
-   [ ] Decide PostgreSQL vs MongoDB for the initial release.
-   [ ] Define auth and session model.
-   [ ] Implement `/v1/health` and `/v1/assistant/ask`.
-   [ ] Add provider abstraction and usage tracking.
-   [ ] Add persistent conversation storage.
-   [ ] Implement a safe tool registry.
-   [ ] Add search and cited research reports.
-   [ ] Add durable background tasks and scheduling.
-   [ ] Add Mac voice client.
-   [ ] Add phone-accessible client and notifications.
-   [ ] Add approval policies and audit events.
-   [ ] Document deployment, backups, and recovery.

------------------------------------------------------------------------

## Final recommendation

Build FRIDAY as a secure, modular personal-agent platform. Start with
voice, a cloud backend, memory, and cited deep research. Then add
background tasks and cross-device access. Use the same tool framework to
expand into developer automation, document management, navigation,
authorized security monitoring, and finance workflows.

The long-term differentiator is not the number of features. It is
FRIDAY's ability to **complete a task reliably, prove what it did,
preserve user control, and remain available across devices**.
