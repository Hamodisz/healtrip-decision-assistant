# HealTrip — AI Patient Decision Assistant (Prototype)

> **Demo:** _coming soon (added when the chat UI milestone ships)_
> **Status:** 🚧 In progress. See [Roadmap](#roadmap). This README is updated as each milestone lands; sections marked _planned_ describe the design before the code exists.
>
> ⚠️ **Prototype. Mock data only. Not medical advice. Not a diagnostic system. Not HIPAA / Saudi-regulation compliant.** All doctors, hospitals and slots are fictional.

---

## 1. Overview

A patient writes, in Arabic or English:

> *"I have chest pain and I'm not sure whether I should see a cardiologist, go to the ER, or seek a second opinion."*

The assistant does **not** answer "see a cardiologist." It:

1. Checks for emergency red flags **first**.
2. Asks only the few questions that matter for this decision.
3. Lets a **deterministic triage layer** (code, not the LLM) choose the care path: emergency, urgent, specialist, routine or second opinion.
4. Only if the path allows it, calls tools that search a real provider database.
5. Presents options that exist in the database, and nothing else.

HealTrip describes its model as **Decision-First Healthcare**: the right decision comes before the booking. This prototype is built around that idea: *decide safely, then discover providers.*

The engineering principle throughout:

> **The LLM understands and talks. Code decides what is safe. The database decides what is true.**

---

## 2. Architecture

```
┌─────────────────────┐   POST /api/chat    ┌───────────────────────────────────────────────┐
│ Next.js chat UI     │ ──────────────────▶ │ FastAPI backend                               │
│ AR (RTL) / EN (LTR) │ ◀────────────────── │                                               │
│ renders only what   │                     │  ① Red-flag pre-check (code, AR/EN)          │
│ the backend returns │                     │  ② AI orchestrator (1 agent, LLM)            │
└─────────────────────┘                     │       └─ extracts structured PatientFacts     │
                                            │  ③ Triage / safety layer (code, no LLM)      │
                                            │       └─ care path + missing questions        │
                                            │  ④ Tool gateway (authorised per care path)   │
                                            │       └─ search_providers, get_*_details      │
                                            │  ⑤ Grounding check on the final reply        │
                                            │  Provider service / repository (one layer)    │
                                            └──────────────────┬────────────────────────────┘
                                                               │ SQLAlchemy, bound parameters
                                                               ▼
                                            ┌───────────────────────────────────────────────┐
                                            │ PostgreSQL (source of truth)                  │
                                            │ specialties · hospitals · doctors · slots     │
                                            └───────────────────────────────────────────────┘
```

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js + React + TypeScript | Simple chat, RTL/LTR switching |
| Backend | **FastAPI (Python)** | The brief suggests Next.js API routes + Zod. FastAPI + **Pydantic** plays the same role (typed, validated contracts), also generates the OpenAPI docs, and is the strongest ecosystem for LLM tool calling. The backend is a separate service, so the frontend never touches the DB or the AI key. |
| DB | PostgreSQL + SQLAlchemy | Relational provider data, DB-level constraints, transactions |
| AI | One LLM with native tool calling | One orchestrator, not a multi-agent system (not needed at this scope) |

---

## 3. AI Agent Design: who is responsible for what

| Responsibility | Owner | Why |
|---|---|---|
| Understanding the patient's words (AR/EN, colloquial) | **LLM** | Language is what LLMs are good at |
| Extracting structured facts (`PatientFacts`) | **LLM**, schema-validated | Turns free text into fields code can reason about |
| Deciding which question to ask next | **Triage layer** says *what is missing*; **LLM** phrases it | Relevance is decided by rules, wording by the model |
| Choosing the care path (ER / urgent / specialist / routine / second opinion) | **Deterministic code** | Safety-critical. Must be testable and repeatable, never an LLM confidence score |
| Which tools may be called | **Tool gateway (code)** | e.g. emergency path: no doctor search, only ER hospitals |
| Provider facts (names, hospitals, languages, fees) | **Database via tools** | The model has no provider knowledge of its own that we trust |
| The final sentence the patient reads | **LLM**, then a **grounding check** | Natural language, but only about data that was returned |

Planned `PatientFacts` (what the LLM extracts; the triage layer consumes):

```json
{
  "chief_complaint": "chest_pain",
  "currently_experiencing": true,
  "onset": "30_minutes_ago",
  "character": "pressure",
  "radiation": ["left_arm"],
  "shortness_of_breath": true,
  "syncope_or_dizziness": false,
  "sweating_or_nausea": true,
  "age": 54,
  "known_heart_condition": null,
  "prior_diagnosis_seeking_review": false,
  "preferred_language": "ar",
  "preferred_city": "Riyadh"
}
```

`null` means *not asked yet*. The triage layer turns nulls that matter into the next question, so the agent stops asking as soon as a path can be decided.

---

## 4. Tool Calling _(planned: M3)_

| Tool | Input | Output | Allowed when |
|---|---|---|---|
| `search_providers` | `specialty`, `city?`, `country?`, `language?`, `second_opinion?`, `remote?` | list of `{doctor_id, doctor_name, specialty, hospital_id, hospital_name, city, languages}` | care path ∈ specialist, routine, second opinion |
| `get_doctor_details` | `doctor_id` | full doctor row + hospital | a `doctor_id` returned earlier in this conversation |
| `get_hospital_details` | `hospital_id` | full hospital row | any non-emergency path; emergency path: only `has_emergency = true` hospitals |

Tool inputs are validated with Pydantic **before** any query; invalid parameters are rejected and reported back to the model as a structured tool error, never passed to the DB. Tools call the same repository layer as the REST API.

---

## 5. Safety _(planned: M2)_

Two layers, both code:

1. **Red-flag pre-check** on the raw message (Arabic + English phrase rules). It runs **before** the LLM, so it still works when the AI service is down.
2. **Triage rules** on the structured `PatientFacts`. The rules follow the conservative direction of the [2021 AHA/ACC chest-pain guideline](https://professional.heart.org/en/science-news/2021-guideline-for-the-evaluation-and-diagnosis-of-chest-pain/top-things-to-know), which advises that **acute** chest pain should be handled as an emergency (call EMS), not as a clinic booking. So:
   - chest pain **happening now or started recently** → **emergency**, even without other symptoms
   - chest pain + shortness of breath / fainting / sweating / pain spreading to arm, jaw or back → **emergency**
   - past, resolved or recurring pain with no red flags → **specialist** (cardiology)
   - an existing diagnosis or treatment plan the patient wants reviewed → **second opinion**

On emergency, the flow **stops provider discovery**. The reply is a fixed, reviewed message (not LLM-generated), e.g.:

> "Based on what you've described, this may need urgent medical attention. Please seek emergency care now (in Saudi Arabia call **997**, the Red Crescent ambulance, or **911** where the unified number operates) rather than waiting for a specialist appointment. This assistant cannot diagnose your condition."

Rules are conservative (they over-triage rather than under-triage), listed in one file, unit-tested in both languages, and **documented as prototype rules, not clinical guidance**. A real deployment needs clinician-authored and clinically validated rules.

Why the LLM isn't trusted here: it isn't that LLMs are always worse at triage. Published results are mixed, and one study found ChatGPT recognised high-acuity patients better than triage nurses ([JMIR 2024](https://doaj.org/article/3e9c85a398b543bab44f5293f804c800)). The problem is **repeatability and auditability**: another study found ChatGPT's triage had poor repeatability, with 47.5% accuracy and a 13.7% under-triage rate on simulated patients ([Emergency Care Journal](https://www.pagepressjournals.org/ecj/article/download/15130/14090/101289)). A safety decision must give the same answer every time and be testable line by line, and `if pain_now: EMERGENCY` is.

---

## 6. Hallucination Prevention _(planned: M3–M4; enforced architecturally, not by prompt)_

| Risk | Guard |
|---|---|
| Invented doctor/hospital | Provider cards in the UI are rendered **from tool results (DB rows)**, never from model text |
| Model mentions a provider not returned | Grounding check: every provider ID in the reply must be in this turn's tool results. Otherwise the reply is replaced with a safe template built from the tool results |
| Empty search | Tool returns an explicit `no_results`; reply: *"I couldn't find a matching provider in the current HealTrip database."* |
| DB down | Tool returns `unavailable`; reply: *"I'm unable to access the provider database right now."* No results are ever produced from model memory |
| Invented specialty | `specialty` is an enum of codes that exist in the DB; any other value is rejected at the tool boundary |
| Readable IDs | `DOC-001`, `HOSP-001` make the grounding rule easy to audit in logs and tests |

---

## 7. Database Schema (✅ M1)

```
specialties                   hospitals                          doctors
───────────                   ─────────                          ───────
code  PK ("cardiology")       id  PK ("HOSP-001")                id  PK ("DOC-001")
name_en / name_ar             name_en / name_ar                  name_en / name_ar
description                   country (ISO-2) · city · address   specialty_code  FK → specialties
                              timezone (IANA)                    hospital_id     FK → hospitals
                              has_emergency                      languages[]
                              accreditation                      years_experience
                              languages[]                        offers_second_opinion
                              is_mock                            offers_remote_consult
                                                                 consultation_fee · currency
                                                                 is_mock
slots
─────
id PK · doctor_id FK → doctors · starts_at (timestamptz) · duration_min
mode (in_person | remote) · status (open | held | booked) · held_until
UNIQUE (doctor_id, starts_at) · CHECK status/mode · INDEX (doctor_id, status, starts_at)
```

- **Cross-border by design** (`country`, `timezone`, `languages`, `currency`, `offers_remote_consult`): a patient in Riyadh can get a remote second opinion from Berlin before deciding to travel.
- **The ER is not bookable**: emergency-medicine doctors have no slots. An emergency path resolves to hospitals with `has_emergency = true`.
- **Constraints live in the DB**, not only in app code.
- **Mock network**: 6 hospitals (Riyadh, Jeddah, Dubai, Istanbul, Berlin), 15 doctors, 9 specialties, ~760 slots. Every row has `is_mock = true` and hospital names start with "Demo".

---

## 8. API

| Method | Endpoint | Status |
|---|---|---|
| GET | `/health` | ✅ |
| GET | `/api/v1/specialties` | ✅ |
| GET | `/api/v1/hospitals?country=&city=&has_emergency=` | ✅ |
| GET | `/api/v1/hospitals/{id}` | ✅ |
| GET | `/api/v1/doctors?specialty=&country=&city=&language=&second_opinion=&remote_consult=` | ✅ |
| GET | `/api/v1/doctors/{id}` | ✅ |
| GET | `/api/v1/doctors/{id}/slots?days=&mode=` | ✅ |
| POST | `/api/v1/triage` (structured facts → care path; no LLM) | planned (M2) |
| POST | `/api/v1/chat` (conversation turn) | planned (M3) |

Interactive OpenAPI docs: `http://localhost:8000/docs`.

---

## 9. Security

**In the prototype:**
- Validation at every boundary (query patterns, ranges, result cap of 20; tool inputs validated before queries)
- Bound parameters only; no string-built SQL
- Secrets in environment variables; the AI key lives only in the backend, never in the frontend
- CORS allow-list (never `*`)
- The database is reachable only through the backend
- Tool authorisation per care path _(M3)_
- Simple per-client rate limit on `/chat` _(M3)_
- No patient accounts, and no conversation stored beyond the session; logs record event types and IDs, not symptom text _(M3)_

**Required before production (not done here):** authentication and consent, encryption at rest, audit logging, data-residency review (Saudi PDPL / NCA controls), an **SFDA regulatory assessment** (software that guides clinical decisions may count as a medical device under [SFDA MDS-G010](https://www.sfda.gov.sa/en/guide/guidance-artificial-intelligence-and-machine-learning-aiml-enabled-medical-devices-mds-%E2%80%93-g010)), clinician-validated triage rules and clinical governance, BAAs/DPAs with the LLM provider, penetration testing, and monitoring. **This prototype claims no regulatory compliance.**

---

## 10. Error Handling

One error shape everywhere: `{"error": {"code", "message", "request_id?"}}`. Stack traces and SQL never reach the client; they are logged server-side under the `request_id`.

| Failure | Behaviour |
|---|---|
| Invalid input | `422 invalid_input`, rejected before any query ✅ |
| Unknown doctor / hospital | `404 not_found` ✅ |
| DB unavailable | `503 database_unavailable`; the chat says it can't reach the provider database, and fabricates nothing ✅ (API) / M3 (chat) |
| AI provider down / timeout | one retry, then *"The AI service is temporarily unavailable."* The red-flag pre-check still runs _(M3)_ |
| No matching provider | explicit "no matching provider in the current database" _(M3)_ |
| Emergency | provider discovery stops; fixed emergency message _(M2)_ |

---

## 11. Assumptions

- No real HealTrip infrastructure, data or clinical rules were provided, so providers, prices and slots are fictional.
- Emergency numbers shown are Saudi Arabia's (997 ambulance; 911 unified in Riyadh, Makkah, Madinah and the Eastern Province), since HealTrip is registered in Saudi Arabia.
- Triage rules are illustrative and conservative, written by an engineer, not a clinician.
- "Second opinion" means a patient who already has a diagnosis or treatment plan and wants it reviewed, often remotely before travelling.
- The chat replies in the patient's language; DB search uses language-independent codes (`cardiology`, `SA`, `ar`).

---

## 12. Future Architecture

- **Full booking** as the consequence of a decision: hold slot → patient confirms → DB transaction → DB-generated ticket number. The demo (M5) books directly on patient confirmation; production adds temporary holds and idempotent confirmation.
- **CRM / case pipeline** with an audit log of every AI turn and tool call, plus a **human review queue** for high-risk or unclear cases.
- **RAG** for unstructured knowledge (procedures, travel/visa, insurance), with cited chunks and refusal below a similarity threshold.
- Provider onboarding API for partner hospitals; real availability via integrations.
- Evaluation harness: a fixed set of Arabic/English patient scenarios run against every prompt/model change, with the triage path asserted.
- Observability: per-turn traces (facts extracted → path → tools → reply), and cost/latency per conversation.

### Revenue layer: upsell & cross-sell (design only, not built)

HealTrip is a medical-travel business, so the decision flow is also where revenue happens. The rule: **selling must never touch the clinical decision.**

- The care path is computed **before and independently of** any offer. The triage code has no access to prices or partners.
- **No offers on emergency or urgent paths.** The offer step isn't even called.
- Offers come from an `offers` table, like providers do, so the LLM can't invent a package or a price.
- Paid placement never changes provider ranking.

| Care path | Cross-sell (related service) | Upsell (better tier) |
|---|---|---|
| Second opinion | Remote records review before travel; medical-report translation | Priority review (faster turnaround), senior-consultant tier |
| Specialist abroad | Visa support, hotel near hospital, airport transfer, interpreter | VIP concierge: private transfer, companion stay, dedicated coordinator |
| Specialist / routine | Check-up package at the matched hospital (e.g. cardiac screening) | Executive check-up |
| Family history given | Family screening via HealTrip's Health Tree (شجرة الصحة) | — |
| After treatment | Follow-up telemedicine, rehab package | Extended follow-up plan |

---

## Roadmap

Deliberately small: a demo that makes the engineering decisions visible, not a platform. Priority order: safety first, visual polish last.

| # | Milestone | Status |
|---|---|---|
| M1 | Data foundation: PostgreSQL schema, mock network, provider API | ✅ done (12 tests) |
| M2 | **Safety / triage layer**: red-flag pre-check + care-path rules (AR/EN), unit-tested | ⏳ next |
| M3 | **AI agent + tools**: fact extraction, relevant questions, `search_providers` / `get_*_details`, grounding check, failure handling | ⬜ |
| M4 | Chat UI (Arabic / English) | ⬜ |
| M5 | Simple booking: patient confirms → ticket number from the DB | ⬜ |
| M6 | README examples (normal, emergency, provider search) + demo link | ⬜ |

---

## Run it locally

```bash
docker compose up -d                       # Postgres 16 + pgvector on :5433
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m app.seed --reset       # schema + mock data
.venv/bin/uvicorn app.main:app --port 8000
.venv/bin/python -m pytest -q              # tests run against a separate healtrip_test DB
```
