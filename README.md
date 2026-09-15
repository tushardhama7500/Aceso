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
- [Guardrails](#guardrails)
- [Observability](#observability)
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
        FE["React chat UI\n(frontend/)"]
    end

    subgraph Backend["FastAPI backend"]
        API["Thin API routes\n/chat /appointments /metrics /health"]
        Agent["Agent (orchestrator)\napp/services/agent.py"]
        Guardrails["Guardrails\nPII · injection · emergency"]
        Memory["Memory\nconversations · messages · issues"]
        ToolReg["create_appointment tool"]
        Summary["Summary service\n(deterministic, from Issue fields)"]
        Obs["Observability\nstructured logs + /metrics"]
        LLMService["LLM Service\nfallback · structured output · tool calling"]
    end

    subgraph Providers["LLM Provider Interface"]
        Gemini["Gemini\n(primary, free tier)"]
        OpenRouter["OpenRouter\n(fallback, free models)"]
        OpenAI["OpenAI\n(optional)"]
    end

    DB[(PostgreSQL)]

    FE -->|POST /chat| API --> Agent
    Agent --> Guardrails
    Agent --> Memory
    Agent --> LLMService
    Agent --> ToolReg
    Agent --> Summary
    Agent --> Obs
    ToolReg -->|executes| DB
    Memory --> DB
    Obs --> DB
    LLMService --> Gemini
    LLMService --> OpenRouter
    LLMService --> OpenAI
    FE -->|GET /appointments| API
```

The Agent is the only thing that talks to `LLMService`. `LLMService` is the only thing that talks to a provider. Providers are the only thing that talk to Gemini/OpenRouter/OpenAI SDKs. Nothing skips a layer — that's what makes swapping providers, or unit-testing the agent with a fake provider, possible without touching business logic.

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

## Configuration

Copy `.env.example` to `.env` and fill in what you have:

```env
LLM_PROVIDER=gemini
LLM_FALLBACK_PROVIDER=openrouter

GEMINI_API_KEY=            # free at https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-2.0-flash

OPENROUTER_API_KEY=        # free at https://openrouter.ai
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free

OPENAI_API_KEY=            # optional — the app works without this
OPENAI_MODEL=gpt-4o-mini

LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=1000
LLM_FREQUENCY_PENALTY=0

DATABASE_URL=postgresql+psycopg2://aceso:aceso@localhost:5432/aceso
```

**Free-first, by construction**: `LLM_PROVIDER=gemini` + `LLM_FALLBACK_PROVIDER=openrouter` is the default, and both have usable free tiers. `OPENAI_API_KEY` is never required — `LLMService` only constructs providers that are actually referenced by `LLM_PROVIDER`/`LLM_FALLBACK_PROVIDER`, so leaving OpenAI unconfigured is not just supported, it means OpenAI code never even runs.

`.env` is git-ignored; only `.env.example` (placeholders only) is committed.

---

## Running locally

```bash
git clone <this repo>
cd aceso
cp .env.example .env
# edit .env and add at least GEMINI_API_KEY (or OPENROUTER_API_KEY)

docker compose up --build
```

This starts three containers — `postgres`, `backend` (FastAPI on `:8000`), `frontend` (Vite dev server on `:5173`). No local PostgreSQL, Python, or Node install is required. Open `http://localhost:5173` for the chat UI, or hit the API directly:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I have ringing in my ears"}'

curl http://localhost:8000/appointments
curl http://localhost:8000/metrics
curl http://localhost:8000/health
```

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

43 tests, all offline — every LLM call is mocked through a scriptable `FakeProvider` (`tests/fakes.py`) that implements the exact same `LLMProvider` interface a real provider does, so **no test ever consumes real API quota**. Coverage:

| File | Covers |
|---|---|
| `test_llm_providers.py` | Provider abstraction: fallback, bounded retry, structured-output parsing + one corrective retry, tool-call parsing |
| `test_chat.py` | `POST /chat` end-to-end through FastAPI's `TestClient`: follow-up questions, department recommendation, tool-call-triggered booking |
| `test_multi_appointment.py` | The assignment's critical end-to-end scenario: headache -> Neurology + ringing in ears -> ENT in one conversation, two separate appointments with two separate summaries; conversation memory persisting across calls |
| `test_memory.py` | Conversation/message persistence, issue upsert-by-reference and field merging |
| `test_tools.py` | `create_appointment` tool schema, argument validation, and that executing it actually writes a row |
| `test_guardrails.py` | PII redaction (including the date/phone false-positive case), prompt-injection detection, emergency detection, and that both short-circuit *before* any LLM call |

---

## API reference

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/chat` | `{conversation_id?, message}` -> `{conversation_id, message}` |
| `POST` | `/appointments` | `{patient_name, department, visit_date, summary}` -> `{appointment_id}` |
| `GET` | `/appointments` | List all appointments |
| `GET` | `/metrics` | Aggregate LLM usage/latency/fallback stats |
| `GET` | `/health` | Liveness + DB connectivity |

Interactive docs at `http://localhost:8000/docs` once running.

---

## Tradeoffs — what was intentionally not built

- **No RAG / vector database** — department recommendation is a reasoning task over a handful of categories, not a retrieval task over a large knowledge base. Adding one would add infrastructure without improving the actual behavior being tested here.
- **No LangChain/LangGraph** — the orchestration needed (guardrails -> memory -> one structured call -> conditional tool call) is a few hundred lines of plain Python. A graph framework would add indirection without adding capability, and would obscure exactly the "clean agent/tool design" this assignment is meant to demonstrate.
- **No Kafka/Redis/Kubernetes** — single-process FastAPI app, one Postgres instance. Nothing here has a queueing, caching, or horizontal-scaling problem to solve.
- **No Alembic/migrations** — `metadata.create_all()` on startup. Fine for a single-environment take-home; would be the first thing added for a real deployment.
- **No authentication** — out of scope for the assignment; every conversation is addressed purely by `conversation_id`.
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
