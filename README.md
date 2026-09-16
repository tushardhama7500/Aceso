# Aceso

**Intelligent pathways to care.**

Aceso *(ah-KAY-so)* is named after the Greek goddess associated with the process of healing and recovery. That's a deliberate positioning choice: Aceso does not diagnose, and it does not prescribe. It helps a patient describe what's wrong, asks only the follow-up questions that matter, points them to the right department, and gets them booked — with a clean, factual summary waiting for the doctor when they arrive.

This is a 2-day take-home assignment for SixHats. The brief asked for strong LLM engineering, agent/tool design, backend engineering, safety, observability and testing — over frontend polish. That's exactly where the effort went.

---

## Table of contents

- [Architecture](#architecture)
- [Request flow](#request-flow)
- [Agent design](#agent-design)
- [LLM abstraction](#llm-abstraction)
- [Provider fallback](#provider-fallback)
- [Prompting strategy](#prompting-strategy)
- [Memory](#memory)
- [Multi-issue handling](#multi-issue-handling)
- [Doctor-facing summary](#doctor-facing-summary)
- [Authentication & user data isolation](#authentication--user-data-isolation)
- [Appointment ownership & duplicate protection](#appointment-ownership--duplicate-protection)
- [Email notifications](#email-notifications)
- [Guardrails](#guardrails)
- [Observability](#observability)
- [Security considerations](#security-considerations)
- [Configuration](#configuration)
- [Running locally](#running-locally)
- [Testing](#testing)
- [API reference](#api-reference)
- [Tradeoffs — what was intentionally not built](#tradeoffs--what-was-intentionally-not-built)
- [Future improvements](#future-improvements)

---

## Architecture

```mermaid
flowchart TB
    subgraph Client
        FE["React chat UI\n(frontend/) — JWT in localStorage"]
    end

    subgraph Backend["FastAPI backend"]
        Auth["/auth/register /auth/login\n(bcrypt + JWT)"]
        CurrentUser["get_current_user\n(JWT dependency)"]
        API["Thin API routes\n/chat /appointments /metrics /health"]
        Agent["Agent (orchestrator)\napp/services/agent.py"]
        Guardrails["Guardrails\nPII · injection · emergency"]
        Memory["Memory\nconversations (user-owned) · messages · issues"]
        ToolReg["create_appointment tool"]
        Summary["Summary service\n(deterministic, from Issue fields)"]
        Obs["Observability\nstructured logs + /metrics"]
        LLMService["LLM Service\nfallback · structured output · tool calling"]
        ApptSvc["AppointmentService\natomic booking + dedup"]
        EmailSvc["EmailService\n(best-effort, post-commit)"]
    end

    subgraph Providers["LLM Provider Interface"]
        Groq["Groq (primary)"]
        Gemini["Gemini (fallback)"]
        OpenRouter["OpenRouter (fallback, multi-model)"]
    end

    DB[(PostgreSQL\nusers · conversations · issues · appointments)]
    SMTPServer[(SMTP server\ne.g. Gmail)]

    FE -->|POST /auth/register, /auth/login| Auth --> DB
    FE -->|Bearer token| API --> CurrentUser --> DB
    API --> Agent
    Agent --> Guardrails
    Agent --> Memory --> DB
    Agent --> LLMService
    Agent --> ToolReg --> ApptSvc
    API -->|POST /appointments| ApptSvc
    Agent --> Summary
    Agent --> Obs --> DB
    ApptSvc --> DB
    ApptSvc --> EmailSvc --> SMTPServer
    LLMService --> Groq
    LLMService --> Gemini
    LLMService --> OpenRouter
```

The Agent is the only thing that talks to `LLMService`. `LLMService` is the only thing that talks to a provider. Providers are the only thing that talk to Groq/Gemini/OpenRouter SDKs. `AppointmentService` is the only thing that writes an `Appointment` row or talks to `EmailService`; `EmailService` is the only thing that talks to the SMTP server. Nothing skips a layer — that's what makes swapping providers, or unit-testing the agent with a fake provider, possible without touching business logic. Every request into the backend that isn't `/auth/*`, `/health`, or `/metrics` passes through `get_current_user` first — the Agent and `AppointmentService` are never reachable without a validated JWT resolving to a real user.

---

## Request flow

```mermaid
sequenceDiagram
    participant U as Patient
    participant API as POST /chat
    participant G as Guardrails
    participant M as Memory (Postgres)
    participant A as Agent
    participant L as LLM Service
    participant T as create_appointment tool
    participant S as Summary service

    U->>API: "I've had ringing in my ears for 3 days"
    API->>A: process(conversation_id, message)
    A->>G: evaluate_input(message)
    G-->>A: redacted text, pii/injection/emergency flags
    Note over A,G: injection or emergency -> canned reply, LLM never called
    A->>M: load history + open issues
    A->>L: generate_structured(TurnAnalysis)
    L-->>A: issues[], reply, patient_name
    A->>M: upsert Issue row(s)
    alt issue ready_for_booking (department + date + name known)
        A->>L: generate_with_tools([create_appointment])
        L-->>A: tool_call(patient_name, department, visit_date, summary)
        A->>S: build_doctor_summary(issue)
        A->>T: execute create_appointment
        T->>M: INSERT appointment
        A->>L: generate(tool result) — final confirmation phrasing
        L-->>A: "Your ENT appointment has been booked for 2026-10-01."
    end
    A->>M: persist assistant reply
    A-->>API: reply
    API-->>U: reply + conversation_id
```

---

## Agent design

`app/services/agent.py` is a plain Python orchestrator — no framework, no graph library. One `Agent.process()` call handles one turn:

1. **Guardrails first.** PII redaction, prompt-injection detection, and emergency detection all run before anything reaches an LLM. Injection and emergency cases short-circuit with a canned response — deterministic, free, and impossible to prompt-inject around.
2. **Load memory.** Full conversation history and all *open* issues (status `collecting` or `ready_for_booking`) are pulled from Postgres. There is no per-process conversation state — a second `/chat` call, even after a restart, sees exactly what's in the database.
3. **One structured LLM call per turn** (`generate_structured` against `TurnAnalysis` — see [Multi-issue handling](#multi-issue-handling)) does the actual reasoning: which issue(s) are being discussed, what's still missing, what department fits, and what to say back.
4. **Sync issue state.** Each `ExtractedIssue` the model returns is upserted into the `issues` table — either updating an existing issue (referenced by id) or creating a new one, so unrelated concerns never collide.
5. **Book anything that's ready.** For every issue now in `ready_for_booking` with a department, a valid ISO date, and a known patient name, the Agent runs the tool-calling round-trip (see below) and appends the confirmation to the reply.
6. **Persist and return.** The assistant's reply is written to `messages` and returned.

The API route (`app/api/chat.py`) is 4 lines. All of the above lives in the Agent, not the route — that's intentional, so the route stays testable and swappable (e.g. adding a websocket transport later touches only the route, not the logic).

---

## LLM abstraction

```python
class LLMProvider(ABC):
    async def generate(...) -> LLMResult: ...
    async def generate_with_tools(...) -> LLMResult: ...
    async def generate_stream(...) -> AsyncIterator[StreamChunk]: ...
```

`app/services/llm/base.py` defines this interface plus provider-agnostic types (`LLMMessage`, `ToolDefinition`, `ToolCall`, `Usage`, `LLMResult`). Three concrete providers implement it:

| Provider | File | SDK |
|---|---|---|
| Gemini | `gemini_provider.py` | `google-genai` (official) |
| OpenRouter | `openrouter_provider.py` | `openai` SDK pointed at OpenRouter's OpenAI-compatible endpoint |
| OpenAI | `openai_provider.py` | `openai` (official) |

OpenRouter and OpenAI share one implementation (`openai_compatible.py`) because they speak the same wire format — only the base URL and key differ. Gemini gets its own implementation because its message/content format, function-calling shape, and streaming API are genuinely different. This is the abstraction earning its keep: the *shape* of the code follows the *shape* of the actual APIs, not a wrapper invented in advance of need.

**The Agent never imports a provider module.** It only calls `LLMService.generate_structured(...)` / `generate_with_tools(...)`. Swapping `LLM_PROVIDER=gemini` for `LLM_PROVIDER=openai` in `.env` changes zero lines of agent code — this is covered directly by `tests/test_llm_providers.py`, which runs the same assertions against a fake provider standing in for any of the three.

### Structured output, uniformly

Rather than relying on each provider's native JSON-mode being available and consistent (`response_format` support varies a lot across OpenRouter's free models), `LLMService.generate_structured()`:

1. Appends the target Pydantic schema (as JSON schema) to the prompt with an explicit "respond with ONLY this JSON" instruction.
2. *Also* asks the provider for JSON mode where it exists (best-effort, silently ignored if the provider/model rejects the parameter).
3. Parses the response and validates it with the actual Pydantic model — this is the real enforcement point, not the provider's cooperation.
4. On a validation failure, makes **one** bounded corrective retry ("that wasn't valid JSON, try again") before raising `LLMOutputError`.

This is what backs issue extraction and department reasoning (`TurnAnalysis` / `ExtractedIssue` in `app/schemas/analysis.py`).

### Tool calling

`create_appointment` (`app/tools/appointments.py`) is the only tool exposed to the model. `generate_with_tools()` returns parsed, structured `ToolCall` objects (arguments already JSON-decoded) regardless of provider — the OpenAI-compatible providers decode `tool_calls[].function.arguments`, and the Gemini provider decodes `response.function_calls[].args`. The Agent validates the arguments with `CreateAppointmentArgs` (Pydantic) before ever touching the database. **The LLM never has database access** — `appointment_service.create_appointment()` is the only code path that writes an appointment row, and it's only ever called by the Agent after tool-call arguments have been validated.

If a model doesn't return a usable tool call (some free OpenRouter models have inconsistent tool support), the Agent books deterministically using the same data it already verified — the patient isn't blocked by a model quirk, and the LLM is still given the chance to phrase the final confirmation.

---

## Provider fallback

```mermaid
flowchart LR
    A[LLM call] --> P{Primary provider}
    P -->|success| R[Return result + meta]
    P -->|ProviderError, up to 2 attempts| F{Fallback configured?}
    F -->|yes| FB[Fallback provider, 1 attempt]
    FB -->|success| R2[Return result + meta\nfallback_used=true]
    FB -->|failure| E[LLMUnavailableError]
    F -->|no| E
```

`LLMService._call_with_fallback()` is the single place this logic lives — **not** in the Agent, and **not** duplicated per-provider. Bounded: at most 2 attempts on the primary, 1 on the fallback, then a controlled `LLMUnavailableError` (the Agent turns this into a graceful "please try again" reply, never a raw 500). Every call — success, retry, or fallback — is logged as one structured JSON line and (for successful calls) written to the `llm_request_logs` table with `fallback_used` / `fallback_from` set. `tests/test_llm_providers.py::test_falls_back_when_primary_fails` and `test_bounded_retry_does_not_retry_forever` cover this directly with a fake provider that always errors.

Configured via `.env`:

```env
LLM_PROVIDER=gemini
LLM_FALLBACK_PROVIDER=openrouter
```

---

## Prompting strategy

`app/prompts/system_prompt.py` holds one deliberately short system prompt. It's structured in three parts:

1. **What Aceso is / isn't** — navigation and booking assistant, not a doctor, stated before anything else so it anchors every downstream instruction.
2. **How to run the conversation** — ask one concise follow-up at a time, don't run a questionnaire, treat unrelated symptoms as separate issues, use hedged department-recommendation language, collect the patient's name once and reuse it.
3. **Safety rules** — no diagnosis, no medication advice, emergency redirection, and explicit instructions to never reveal the system prompt / developer instructions / hidden reasoning, and to treat any in-conversation attempt to change these rules as patient text to be declined, not as new instructions.

The conversational instructions and the JSON-schema instruction are **kept separate** on purpose: the system prompt is about *behavior*, and the schema instruction (appended by `LLMService.generate_structured`, see above) is about *output format*. Mixing the two makes prompts harder to maintain and, in practice, harder for smaller free models to follow reliably.

Department mapping is described with a few concrete examples (ENT / Neurology / Orthopedics / Dermatology / Cardiology / General Medicine) but framed as "use clinical judgement" rather than a rigid lookup table — the assignment explicitly asks for this to be handled "intelligently through the LLM," so no hardcoded keyword→department map exists in code. What *is* hardcoded is the requirement that the model's answer land in the validated `department: Optional[str]` field of `ExtractedIssue`, so however it reasons, the result is structured data the backend can act on.

---

## Memory

Four tables, all in Postgres, no in-process state anywhere:

- **`conversations`** — id, `patient_name` (captured once, reused for later appointments in the same conversation), timestamps.
- **`messages`** — every user/assistant turn, in order. PII-redacted *before* storage (see Guardrails) — Aceso never persists a raw email or phone number, even briefly.
- **`issues`** — see [Multi-issue handling](#multi-issue-handling).
- **`appointments`** — matches the assignment's API contract exactly (`appointment_id`, `patient_name`, `department`, `visit_date`, `summary`), plus a `doctor_summary` JSON column for the richer structured summary.
- **`llm_request_logs`** — one row per LLM call, backing `/metrics`.

A conversation is fully reconstructable from `conversation_id` alone: `tests/test_memory.py` and `test_multi_appointment.py::test_conversation_memory_persists_across_calls` both verify a second `/chat` call (simulating a fresh request/process) sees the exact history and issue state left by the first.

Tables are created via `SQLAlchemy.metadata.create_all()` on startup — no Alembic. For a 2-day, single-environment assignment, a migration framework would be pure overhead; see [Tradeoffs](#tradeoffs--what-was-intentionally-not-built).

---

## Multi-issue handling

This was called out as the hardest requirement, so it gets its own explicit model: **`Issue`** (`app/models/issue.py`).

```
id, conversation_id, symptoms[], duration, severity, relevant_context[],
department, preferred_date, status, appointment_id, doctor_summary, timestamps
```

States: `collecting -> ready_for_booking -> booked` (or `closed`, reserved for an issue the patient drops).

Every turn, the Agent gives the LLM the **current open issues** (id + captured fields) as context and asks it to return a list of `ExtractedIssue` updates, each either referencing an existing issue's id (`issue_ref`) or omitting it to signal a new, unrelated concern. `app/services/memory.sync_issue()` does the actual upsert — matching by id when given, otherwise creating a new row. List fields (`symptoms`, `relevant_context`) are merged and de-duplicated rather than overwritten, so re-stating a symptom doesn't create noise.

This is what makes "headache -> Neurology" and "ringing in ears -> ENT" in the same conversation produce two independent `Issue` rows and, ultimately, two independent `Appointment` rows — proven end-to-end in `tests/test_multi_appointment.py::test_two_unrelated_issues_produce_two_appointments`, which walks the exact 3-turn scenario from the assignment brief and asserts two appointments with two distinct doctor summaries land in Postgres.

Known limitation: issue matching relies on the LLM correctly returning (or omitting) `issue_ref` — there's no separate embedding-based or fuzzy-matching fallback. For a healthcare-navigation assistant with short conversations (a handful of turns per issue), this held up well in testing; a production system would likely want a second, cheap heuristic as a backstop (see [Future improvements](#future-improvements)).

---

## Doctor-facing summary

Deliberately **not** a second free-form LLM call. `app/services/summary_service.py` assembles the `DoctorSummary` directly from the same `Issue` fields (`symptoms`, `duration`, `severity`, `relevant_context`, `department`) that were already extracted — and Pydantic-validated — during the conversation:

```json
{
  "department": "ENT",
  "chief_concern": "ringing in ears",
  "duration": "3 days",
  "severity": null,
  "patient_reported_symptoms": ["ringing in ears"],
  "relevant_information": []
}
```

Because there's no generative step left in this path, there's no step that could fabricate a diagnosis, a medication, or a symptom the patient never mentioned — the guarantee is structural, not just a prompt instruction. Each `Appointment` gets its own summary (`appointments.doctor_summary`), generated independently per issue.

---

## Authentication & user data isolation

Every conversation, issue, and appointment now belongs to a `User`. There is no more anonymous/shared access — `POST /chat`, `GET /appointments`, and `POST /appointments` all require a valid JWT.

**Registration** (`POST /auth/register`) validates the email (Pydantic `EmailStr`), enforces a unique email at both the application layer and the database's `UNIQUE` constraint (closing the check-then-insert race), hashes the password with `bcrypt` (a fresh salt per hash — never a stored plaintext password, never a stored raw hash the app can reverse), and returns a JWT immediately so registration doubles as first login.

**Login** (`POST /auth/login`) verifies the password against the stored bcrypt hash and returns the same JWT shape.

### JWT flow

```mermaid
sequenceDiagram
    participant C as Client
    participant API as FastAPI route
    participant Dep as get_current_user
    participant DB as PostgreSQL

    C->>API: POST /auth/login {email, password}
    API->>DB: authenticate_user (bcrypt.checkpw)
    DB-->>API: User row
    API-->>C: {access_token, user}

    C->>API: POST /chat  (Authorization: Bearer <token>)
    API->>Dep: get_current_user(credentials)
    Dep->>Dep: jwt.decode(token, JWT_SECRET_KEY, JWT_ALGORITHM)
    Dep->>DB: db.get(User, sub claim)
    DB-->>Dep: User row (or None)
    Dep-->>API: current_user
    API-->>C: 401 if missing/invalid/expired token or unknown user id
```

The token's `sub` claim is the user's **id**, never the email — so rotating a user's email doesn't invalidate outstanding tokens, and nothing email-shaped ever needs parsing out of a token. `get_current_user` (`app/api/deps.py`) is a single reusable FastAPI dependency: it reads `Authorization: Bearer <token>` via `HTTPBearer`, decodes and validates the JWT (rejecting anything expired, tampered, signed with the wrong algorithm, or missing a `sub` claim), and re-resolves the user from Postgres on every request — so a deleted user's still-unexpired token stops working immediately, rather than only after its natural expiry.

### Data isolation — the ownership chain

There is no `X-User-ID` header, no trusting `patient_name`, and no trusting email as an authorization mechanism anywhere. Ownership is a single chain, enforced at the point each resource is reached:

```
User  <--  Conversation.user_id  <--  Issue.conversation_id  <--  Appointment (via Issue.appointment_id)
```

- **Conversations**: `Conversation.user_id` is a required (`NOT NULL`) foreign key. `memory.get_or_create_conversation(db, user_id, conversation_id)` checks ownership before returning an existing conversation — if `conversation_id` exists but belongs to a different user, it raises `ConversationAccessError`, which `POST /chat` turns into a **404** (not 403 — confirming that a conversation ID exists but belongs to someone else is itself an information leak, so the same response is returned whether the ID is unowned or simply doesn't exist).
- **Issues**: no separate ownership column — reached only through their (already-checked) `Conversation`, and never exposed through a standalone API endpoint.
- **Appointments**: no `user_id` column was added here — `Appointment` still has no direct link to `Conversation`/`User` (unchanged from the original schema), and ownership is resolved by joining through the existing `Issue.appointment_id` relationship instead:
  `appointment_service.list_appointments_for_user` does `Appointment JOIN Issue ON Issue.appointment_id JOIN Conversation ON Conversation.id WHERE Conversation.user_id = :user_id`. `POST /appointments` (the direct booking endpoint — see below) now requires an `issue_id` and checks `issue.conversation.user_id == current_user.id` before booking, returning the same **404** on mismatch.

`tests/test_data_isolation.py` exercises this directly: registering two users, booking an appointment for user A, and asserting user B gets an empty appointment list, a 404 on `POST /chat` against user A's `conversation_id`, and a 404 on `POST /appointments` against user A's `issue_id`.

---

## Appointment ownership & duplicate protection

`appointment_service.book_issue_appointment` is now the single code path that links an `Issue` to an `Appointment` — used by both the Agent's `create_appointment` tool-call flow and the direct `POST /appointments` endpoint. It closes the race a plain "read `issue.appointment_id`, then write" check leaves open (a repeated tool call, a frontend retry, or a retried HTTP request arriving concurrently) by taking a row-level lock before deciding:

```python
locked_issue = db.execute(select(Issue).where(Issue.id == issue.id).with_for_update()).scalar_one()
if locked_issue.appointment_id:
    return existing_appointment, created=False   # already booked — don't duplicate
# ... create the Appointment, link it, commit ...
return appointment, created=True
```

`SELECT ... FOR UPDATE` serializes concurrent booking attempts for the *same* issue at the database level (Postgres; SQLite in tests has no real row locking, but the tests exercise the same-session sequential case, which is what actually matters there). `created=False` means "a concurrent attempt already booked this" — the caller returns the existing appointment's confirmation instead of creating a second one, and no email is sent for it.

---

## Email notifications

Appointment confirmations are sent via SMTP, through a small `EmailService` abstraction (`app/services/email_service.py`) — plain stdlib `smtplib`/`email`, no extra dependency. Works with a Gmail account (using an [App Password](https://myaccount.google.com/apppasswords), not the real account password — Google requires 2-Step Verification to be enabled first) or any other SMTP account by pointing `SMTP_HOST`/`SMTP_PORT` elsewhere.

```
Agent (tool call) ──┐
                     ├──> AppointmentService.book_issue_appointment ──> PostgreSQL (commit)
POST /appointments ──┘                                                       │
                                                                               ▼
                                                                        EmailService
                                                                               │
                                                                               ▼
                                                                        SMTP server
```

Two invariants, both enforced in `book_issue_appointment`:

1. **The email is sent only after the appointment has already committed.** The DB write and the email send are not one transaction — there is nothing to "undo" on an email failure because the booking is already durable by the time email is attempted.
2. **A failing (or even raising) `EmailService` can never affect the booking.** The send is wrapped in its own `try/except`; a failure is logged (`aceso.email` logger, `email_send_failed`/`email_confirmation_failed`) and swallowed. The API response is unaffected — the client still gets back a normal booking confirmation.

The email is deliberately minimal: department, date, appointment ID, and the patient's name for a greeting — never the doctor-facing `doctor_summary` (chief concern, severity, symptoms) that appointment records carry. `tests/test_email_service.py` asserts none of those fields' key names can appear in the rendered HTML.

The recipient is the **authenticated account's email** (`current_user.email` / `conversation.user.email`), not `patient_name` (which is free text the LLM extracted from conversation and isn't a validated address) and not any patient-supplied email (there is no such field in this design — see the note in [Security considerations](#security-considerations)).

If `SMTP_USERNAME`/`SMTP_PASSWORD` aren't set, `EmailService.enabled` is `False` and sending is skipped with a log line (`email_skipped reason=smtp_not_configured`) rather than attempted — useful for local dev without setting up email, and exactly what this repo's own `.env` does out of the box.

---

## Guardrails

Three layers, all deterministic and applied **before** the LLM is called (`app/services/guardrails.py`):

1. **PII redaction** — regex-based email/phone detection. The phone pattern is deliberately shaped to require phone-like groupings (`3-3-4` digits, optional `+country`, parens) so it doesn't misfire on ISO dates like `2026-09-20` (a `4-2-2` grouping) — see `test_dates_are_not_mistaken_for_phone_numbers`. Redaction happens *before* the message is sent to the LLM and *before* it's persisted to Postgres.
2. **Prompt injection detection** — a small set of regex patterns for obvious attempts ("ignore previous instructions", "reveal your system prompt", "developer instructions", etc.). A match short-circuits the turn with a fixed redirect message — **no LLM call is made at all**, so there's nothing for the injection to reach. Subtler attempts are the system prompt's job (defense in depth): it explicitly instructs the model to treat any in-conversation rule change as patient text, never as new instructions.
3. **Medical safety** — enforced structurally (the summary can't fabricate a diagnosis, per above) and via the system prompt's explicit "never diagnose / never prescribe / never claim certainty" rules plus example bad/good phrasing.

**Emergency handling** is also deterministic and keyword-based (`detect_emergency`) rather than left to the LLM — chest pain, difficulty breathing, suicidal ideation, stroke symptoms, etc. immediately return a fixed "seek immediate emergency care" message and skip normal triage entirely. The assignment explicitly asked to keep this "conservative and simple"; a keyword list that fails safe (over-triggers rather than under-triggers) is exactly that, and it can't be argued out of by clever phrasing the way an LLM-based classifier potentially could.

All three layers are covered in `tests/test_guardrails.py`, including two Agent-level tests that assert the fake LLM provider is **never called** on an injection or emergency turn.

---

## Observability

Every LLM call — whether it succeeds on the primary, retries, or falls back — produces:

```json
{
  "request_id": "req_...",
  "provider": "openrouter",
  "model": "meta-llama/llama-3.3-70b-instruct:free",
  "usage": {"input_tokens": 812, "output_tokens": 143, "cached_tokens": null, "total_tokens": 955},
  "ttft_ms": null,
  "latency_ms": 1432.6,
  "fallback_used": true,
  "fallback_from": "gemini"
}
```

as one structured log line (`app/services/llm/service.py`, logger `aceso.llm`) — the primary observability mechanism, per the assignment's guidance to favor structured logging over a dashboard. Successful calls are also persisted to `llm_request_logs` (`app/services/observability.py`) so `GET /metrics` survives a restart:

```json
{
  "total_llm_requests": 42,
  "total_input_tokens": 18320,
  "total_output_tokens": 5104,
  "total_tokens": 23424,
  "average_latency_ms": 1180.4,
  "average_ttft_ms": null,
  "provider_usage": {"gemini": 38, "openrouter": 4},
  "fallback_count": 4
}
```

**TTFT**: computed only where streaming is actually used and a first content token is genuinely observed. `generate_stream()` is implemented for both provider families, but the current turn-processing path uses non-streaming calls (`generate` / `generate_structured` / `generate_with_tools`) because the API contract is a synchronous `/chat` response, not SSE — so `ttft_ms` is honestly `null` for those calls rather than a fabricated number. **Cached tokens** are likewise `null` whenever a provider doesn't expose them (OpenRouter/Gemini responses don't always include this) — never invented. This matches the assignment's explicit "do not fabricate metrics" instruction.

---

## Security considerations

This is a take-home project's authentication layer, not a production healthcare identity system — read it that way:

- **Passwords**: hashed with `bcrypt` (fresh salt per hash), never logged, never returned in any API response (`UserPublic` has no password/hash field at all — not even a redacted one).
- **JWT secret**: `JWT_SECRET_KEY` has no default — `app/core/security.py` raises `RuntimeError` immediately if it's empty, rather than silently signing tokens with a blank/predictable key. Generated fresh per environment, `.env`-only, never committed (`.gitignore` already excludes `.env`).
- **Token transport**: `Authorization: Bearer <token>`, validated via `HTTPBearer` + `PyJWT` with an explicit algorithm allowlist (`HS256`/`HS384`/`HS512`) — a token claiming `alg: none` or an unexpected algorithm is rejected outright, not silently accepted.
- **No refresh tokens / no logout-side revocation list**: a JWT is valid until it expires (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, default 60) or the user row it names is deleted (`get_current_user` re-resolves the user from Postgres on every request, so a deleted account's token stops working immediately — but an *active* account's token can't be revoked early). A real deployment would want short-lived access tokens plus a refresh-token/rotation scheme, and likely a proper identity provider (Auth0, Clerk, AWS Cognito, etc.) rather than hand-rolled JWT issuance, depending on compliance requirements (HIPAA-adjacent deployments in particular would need a BAA-covered auth provider, audit logging, and MFA — all out of scope here).
- **Frontend token storage**: the React app stores the JWT in `localStorage` (see `frontend/src/api.js`). That's readable by any script running on the page — acceptable for this take-home's scope, but a real deployment handling real patient data would prefer an `httpOnly` cookie (immune to XSS-driven token theft) plus CSRF protection, which requires a same-site backend/frontend deployment this project doesn't have.
- **Email**: `SMTP_PASSWORD` is read from environment only, never logged (see `tests/test_email_service.py::test_password_never_appears_in_logs`), and the confirmation email is deliberately minimal — no clinical detail (chief concern, severity, symptoms) ever leaves the database via email. Use a Gmail **App Password**, never the real account password — it can be revoked independently without touching the account's main login.
- **Enumeration resistance**: login failures for "wrong password" and "no such account" return the identical `401 Incorrect email or password`; cross-user resource access returns `404`, not `403`, in every case (conversation, issue, appointment) — never enough information to confirm a resource exists but belongs to someone else.

---

## Configuration

Copy `.env.example` to `.env` and fill in what you have:

```env
# --- LLM provider chain: primary, then fallbacks in order ---
LLM_PROVIDER=groq
LLM_FALLBACK_PROVIDERS=gemini,openrouter

GROQ_API_KEY=               # https://console.groq.com/keys — very low latency
GROQ_MODEL=openai/gpt-oss-120b

GEMINI_API_KEY=              # free at https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-2.0-flash

OPENROUTER_API_KEY=          # free at https://openrouter.ai
OPENROUTER_MODEL=model-a:free,model-b:free   # comma-separated — tried in order

OPENAI_API_KEY=               # optional — the app works without this
OPENAI_MODEL=gpt-4o-mini

LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=1000
LLM_FREQUENCY_PENALTY=0
LLM_TIMEOUT_SECONDS=6         # per-attempt ceiling before treating a hang as a retryable failure
LLM_RETRY_BACKOFF_SECONDS=0.5 # pause before retrying the SAME provider/model on a transient error

# --- Auth (JWT) ---
JWT_SECRET_KEY=                # required — generate with e.g. `python -c "import secrets;print(secrets.token_urlsafe(48))"`
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60

# --- Email (SMTP) — appointment confirmations only, best-effort ---
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=                  # leave blank (with SMTP_PASSWORD) to disable email
SMTP_PASSWORD=                  # Gmail: an App Password, never your real password
SMTP_FROM_EMAIL=                # defaults to SMTP_USERNAME if blank

DATABASE_URL=postgresql+psycopg2://aceso:aceso@localhost:5432/aceso
```

**Free-first, by construction**: `LLM_PROVIDER`/`LLM_FALLBACK_PROVIDERS` only construct providers actually referenced — `OPENAI_API_KEY` is never required, and leaving it unconfigured means that provider's code never even runs. `JWT_SECRET_KEY` is the one required secret with no safe default: `app/core/security.py` raises immediately rather than signing tokens with a blank key. `SMTP_USERNAME`/`SMTP_PASSWORD` are optional — leaving them blank disables email sending (logged, not silently ignored) without affecting booking.

`.env` is git-ignored; only `.env.example` (placeholders only) is committed.

---

## Running locally

**Prerequisites**: Docker Desktop (or Docker Engine + Compose v2) only — no local PostgreSQL, Python, or Node install is required.

```bash
git clone <this repo>
cd aceso
cp .env.example .env
# edit .env: add at least one LLM provider key, and set JWT_SECRET_KEY
# (required — generate with: python -c "import secrets;print(secrets.token_urlsafe(48))")

docker compose up --build
```

This starts three containers:

| Service | URL | Purpose |
|---|---|---|
| `frontend` | http://localhost:5173 | Vite dev server (React UI) |
| `backend` | http://localhost:8000 | FastAPI (docs at `/docs`) |
| `postgres` | `localhost:5433` → container `5432` | Database (published on **5433** on the host — see note below) |

Open **http://localhost:5173** for the UI (register or log in first), or hit the API directly:

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "a-strong-password"}'
# -> {"access_token": "...", "token_type": "bearer", "user": {...}}

TOKEN=<paste access_token from above>

curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "I have ringing in my ears"}'

curl http://localhost:8000/appointments -H "Authorization: Bearer $TOKEN"
curl http://localhost:8000/metrics
curl http://localhost:8000/health
```

From PowerShell, the health check is:

```powershell
Invoke-RestMethod http://localhost:8000/health
# -> @{ status = ok; database = True }
```

**Database schema**: there's no Alembic — `init_db()` runs `Base.metadata.create_all()` in the backend's FastAPI `lifespan` on every startup, so a fresh `pgdata` volume gets its full schema (`users`, `conversations`, `messages`, `issues`, `appointments`, `llm_request_logs`) automatically; no separate migration step is needed. `depends_on: postgres: condition: service_healthy` on the `backend` service means Compose won't even start the backend container until Postgres's own healthcheck (`pg_isready`) passes, so there's no startup race to work around.

**Stopping the application**:

```bash
docker compose down        # stop and remove containers, keep the postgres data volume
docker compose down -v      # also delete the data volume — next `up` starts from a truly empty database
```

### Docker troubleshooting

- **Postgres port 5433, not 5432**: `docker-compose.yml` publishes Postgres on host port **5433** (`"5433:5432"`), not the standard 5432. This is deliberate — a local, non-Docker Postgres install commonly already owns 5432 on the host, and that conflict makes `docker compose up` fail outright with a "port is already allocated" error. Containers still reach Postgres internally as `postgres:5432` regardless of this mapping (that's what `DATABASE_URL` inside the backend container uses) — only host-side tools (e.g. `psql` run directly on your machine) need the `5433` port. If you don't have a local Postgres, feel free to change this back to `"5432:5432"`.
- **"port is already allocated" on 8000 or 5173**: something else on your machine (often a previous non-Docker `uvicorn`/`vite` run) is already bound to that port. Find and stop it (`netstat -ano | findstr :8000` on Windows, then `taskkill /F /PID <pid>`), or change the host-side port in `docker-compose.yml`.
- **backend container unhealthy / restarting**: `docker compose logs backend` — almost always either Postgres wasn't reachable yet (shouldn't happen given the healthcheck-gated `depends_on`, but check `docker compose ps` to confirm `postgres` shows `healthy`) or a required env var is missing. The app itself starts fine with every LLM/JWT/SMTP key unset (secrets are read lazily and default to disabled), so a crash-looping backend is not caused by an unset provider key — check the actual traceback in the logs.
- **Stale schema after pulling changes that add new columns/tables**: since there's no migration framework, `create_all()` only ever *adds* missing tables — it can't `ALTER TABLE` an existing one. If a pull adds a new required column to an existing table, run `docker compose down -v` (drops the data volume) and `docker compose up --build` again for a clean schema. This loses local data; there's nothing to preserve in a throwaway dev volume, but don't do this against a volume you actually care about.
- **Frontend loads but chat/login calls fail**: open the browser console — CORS errors there usually mean `CORS_ORIGINS` in `.env` was narrowed away from the default `*`. Confirm with `curl -I -H "Origin: http://localhost:5173" http://localhost:8000/health` and check for `Access-Control-Allow-Origin` in the response.
- **Rebuilding after a dependency change**: `docker compose build --no-cache <service>` — plain `docker compose up --build` reuses cached layers and can miss a `requirements.txt`/`package.json` change if the layer cache is stale.

### Running the backend without Docker (optional, for development)

```bash
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements.txt
# point DATABASE_URL at a Postgres instance you run yourself, or use SQLite for a quick spin:
#   DATABASE_URL=sqlite:///./aceso.db
uvicorn app.main:app --reload
```

---

## Testing

```bash
pip install -r requirements.txt
pytest
```

130 tests, all offline — every LLM call is mocked through a scriptable `FakeProvider` (`tests/fakes.py`) that implements the exact same `LLMProvider` interface a real provider does, so **no test ever consumes real API quota**, and every email test mocks `smtplib.SMTP` rather than sending real mail. Coverage:

| File | Covers |
|---|---|
| `test_llm_providers.py` | Provider abstraction: multi-tier fallback, bounded retry with backoff, retryable/non-retryable error classification, per-attempt timeout, OpenRouter multi-model fallback |
| `test_groq_provider.py` | Groq as primary provider: successful requests, tool calling, structured output, fallback chain wiring |
| `test_llm_provider_wire_format.py` | Gemini/OpenAI-compatible wire-format edge cases and error classification against real SDK exception types |
| `test_chat.py` | `POST /chat` end-to-end through FastAPI's `TestClient`: follow-up questions, department recommendation, tool-call-triggered booking |
| `test_multi_appointment.py` | The assignment's critical end-to-end scenario: headache -> Neurology + ringing in ears -> ENT in one conversation, two separate appointments with two separate summaries; conversation memory persisting across calls |
| `test_memory.py` | Conversation/message persistence (now user-scoped), issue upsert-by-reference and field merging |
| `test_conversation_memory.py` | Regression coverage for known-vs-missing field tracking across turns |
| `test_tools.py` | `create_appointment` tool schema, argument validation, and that executing it actually writes a row |
| `test_guardrails.py` | PII redaction (including the date/phone false-positive case), prompt-injection detection, emergency detection, and that both short-circuit *before* any LLM call |
| `test_auth.py` | Registration, login, password hashing, JWT create/decode, and `get_current_user` rejecting missing/invalid/expired tokens or a deleted user |
| `test_data_isolation.py` | Cross-user isolation: appointments, `/chat` against another user's `conversation_id`, `/appointments` against another user's `issue_id` — all 404, never a data leak |
| `test_appointment_booking.py` | Atomic booking, duplicate-booking protection, and that a failing/raising `EmailService` never affects an already-persisted appointment |
| `test_email_service.py` | SMTP message shape, minimal (non-medical) email content, and that the password never appears in logs |

---

## API reference

| Method | Path | Auth? | Purpose |
|---|---|---|---|
| `POST` | `/auth/register` | No | `{email, password}` -> `{access_token, token_type, user}` |
| `POST` | `/auth/login` | No | `{email, password}` -> `{access_token, token_type, user}` |
| `POST` | `/chat` | **Yes** | `{conversation_id?, message}` -> `{conversation_id, message}` — 404 if `conversation_id` belongs to another user |
| `POST` | `/appointments` | **Yes** | `{issue_id, patient_name, department, visit_date, summary}` -> `{appointment_id}` — 404 if `issue_id` isn't owned by the caller |
| `GET` | `/appointments` | **Yes** | List the caller's own appointments only |
| `GET` | `/metrics` | No | Aggregate LLM usage/latency/fallback stats |
| `GET` | `/health` | No | Liveness + DB connectivity |

Protected routes expect `Authorization: Bearer <token>` from `/auth/login` or `/auth/register`. Interactive docs at `http://localhost:8000/docs` once running.

---

## Tradeoffs — what was intentionally not built

- **No RAG / vector database** — department recommendation is a reasoning task over a handful of categories, not a retrieval task over a large knowledge base. Adding one would add infrastructure without improving the actual behavior being tested here.
- **No LangChain/LangGraph** — the orchestration needed (guardrails -> memory -> one structured call -> conditional tool call) is a few hundred lines of plain Python. A graph framework would add indirection without adding capability, and would obscure exactly the "clean agent/tool design" this assignment is meant to demonstrate.
- **No Kafka/Redis/Kubernetes** — single-process FastAPI app, one Postgres instance. Nothing here has a queueing, caching, or horizontal-scaling problem to solve.
- **No Alembic/migrations** — `metadata.create_all()` on startup. Fine for a single-environment take-home; would be the first thing added for a real deployment. (This meant the auth schema change — adding `users` and `conversations.user_id` — required dropping and recreating the dev database rather than an `ALTER TABLE`; a real deployment with real data would need Alembic before this kind of change.)
- **Hand-rolled JWT auth, not a full identity provider** — registration/login/JWT issuance is deliberately small (bcrypt + PyJWT, no OAuth, no SSO, no MFA, no refresh-token rotation). This is not production-grade healthcare authentication. A real deployment — especially anything HIPAA-adjacent — would want a dedicated identity provider (Auth0, Clerk, AWS Cognito, etc.) handling MFA, audit logging, session/refresh-token management, and compliance-relevant guarantees this hand-rolled layer doesn't attempt. See [Security considerations](#security-considerations).
- **No production frontend build/polish** — the Vite dev server is what `docker compose up` runs; there's no separate Nginx/static-build stage. The brief was explicit that frontend polish doesn't matter here, so the effort went into the backend/agent/LLM layers instead.
- **Doctor summary is deterministic, not a second LLM call** — see [Doctor-facing summary](#doctor-facing-summary). This trades a small amount of "look how much LLM we used" for a much stronger anti-hallucination guarantee, which felt like the right call for anything touching a doctor-facing medical record.
- **No fuzzy/embedding-based issue de-duplication** — multi-issue matching relies on the LLM's own `issue_ref` bookkeeping (see [Multi-issue handling](#multi-issue-handling)) rather than a second matching system.

The goal throughout was to maximize reliability and clarity of the core loop within a 2-day budget, rather than maximize the number of technologies touched.

---

## Future improvements

- A second, cheap heuristic (e.g. symptom-keyword overlap) as a backstop for issue matching, independent of the LLM's own `issue_ref` bookkeeping.
- A real evaluation harness — a fixed set of scripted conversations run against each configured provider/model on every change, checking department accuracy and safety-rule adherence, not just schema validity.
- A lightweight safety classifier (even a small local model) as a second opinion alongside the keyword-based emergency/injection detectors, for cases the deterministic patterns miss.
- Streaming all the way to the client (SSE) so TTFT becomes a real, always-populated metric, and the chat UI can render tokens incrementally.
- A proper tracing platform (e.g. OpenTelemetry export) once there's more than one service to trace across.
- Cost dashboards once real usage/billing data exists to dashboard.
- Real appointment availability/calendar integration instead of always-available slots.
- A human-escalation path for emergencies and cases the Agent can't resolve after a few turns.
- Basic authentication once this needs to be multi-tenant rather than a single deployed instance.
