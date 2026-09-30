# Architecture & data flow

[← back to README](../README.md)


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
                                            │ patients (CRM file) · offers · bookings ·     │
                                            │ notifications (outbox)                        │
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
| The final sentence the patient reads | **LLM**, then two **code checks**: grounding + no-diagnosis | Natural language, but only about returned data, and never a diagnosis or treatment advice |

`PatientFacts` (what the LLM extracts; the triage layer consumes, `backend/app/triage.py`):

```json
{
  "chief_complaint": "chest_pain",
  "pain_now": false,
  "onset": "weeks_or_more",
  "shortness_of_breath": false,
  "fainting_or_dizziness": false,
  "sweating_or_nausea": false,
  "pain_spreads": false,
  "has_diagnosis_to_review": false,
  "review_specialty": null
}
```

Reception data (file number / national ID / date of birth) and search preferences (city, language) are extracted by the same call but kept **outside** `PatientFacts`: the triage rules never see identity or location.

`null` means *not asked yet*. The triage layer turns nulls that matter into the next question, so the agent stops asking as soon as a path can be decided.

---

## 4. Tool Calling

| Tool | Input | Output | Allowed when |
|---|---|---|---|
| `search_providers` | `city?`, `country?`, `language?`, `remote?` (specialty and second-opinion are **set by the rules**, not the model) | list of `{doctor_id, doctor_name, specialty, hospital_id, hospital_name, city, languages}` | care path ∈ specialist, routine, second opinion |
| `get_doctor_details` | `doctor_id` | full doctor row + hospital | a `doctor_id` returned earlier in this conversation |
| `get_available_slots` | `doctor_id`, `mode?` | up to 5 open times (local time) | a `doctor_id` returned earlier in this conversation |
| `get_hospital_details` | `hospital_id` | full hospital row | hospitals of doctors returned in this conversation |

The agent (`backend/app/agent.py`) is one orchestrator whose steps are fixed by code:

```
message ─▶ ① red-flag pre-check ──(hit)──▶ fixed emergency reply + ER hospitals from DB   [no LLM]
             │
             ▼
           ② LLM: record_facts (forced tool call, schema-validated)  →  PatientFacts
             ▼
           ③ triage.decide(facts)                                       [code]
             ├─ emergency / urgent ─▶ fixed reply + ER hospitals from DB [no LLM text]
             ├─ need more info ─────▶ LLM phrases the question the rules asked for
             └─ path decided ───────▶ LLM calls search_providers / get_*_details via the gateway
                                        ▼
           ④ output checks: no-diagnosis + grounding  ─▶  reply + provider cards (DB rows) + trace
```

The **tool gateway** (`backend/app/tools.py`) sits between the model and the database:
- `search_providers` is refused on emergency/urgent paths.
- `specialty` and `second_opinion` are **set by the triage rules and can't be changed by the model**. The model only chooses city/country/language filters.
- `get_doctor_details` only works for doctors already returned in this conversation; `get_hospital_details` only for their hospitals.

The LLM client (`backend/app/llm.py`) speaks the OpenAI-compatible API, so DeepSeek (used for the live runs), Anthropic, Groq, Gemini or OpenAI is a `.env` change, not a code change.

Tool inputs are validated with Pydantic **before** any query; invalid parameters are rejected and reported back to the model as a structured tool error, never passed to the DB. Tools call the same repository layer as the REST API.

## Separate agents in production

> **Production design note: separate agents, one orchestrator.** In production, **Reception** and each **Clinic Assistant** (Cardiology, Orthopedics, …) would be separate agents, each with its own prompt, tools and permissions. A main orchestrator calls them and hands the conversation over:
>
> ```
>                        ┌──────────────────────────┐
>   patient ───────────▶ │ Main orchestrator        │  safety check on every message
>                        └──────────┬───────────────┘
>              calls ┌──────────────┴───────────────┐ calls
>                    ▼                              ▼
>   ┌────────────────────────────┐   ┌──────────────────────────────────────┐
>   │ Reception agent            │   │ Clinic assistant agent (per clinic)  │
>   │ tools: lookup_patient,     │   │ tools: search_providers, slots,      │
>   │        verify_identity     │   │        offers, booking               │
>   │ sees: identity data only   │   │ sees: verified file summary + facts  │
>   └────────────────────────────┘   └──────────────────────────────────────┘
> ```
>
> Why separate: each agent gets **least privilege** (reception can't book; the clinic assistant never sees raw identity numbers), smaller focused prompts, and can be tested and changed on its own. Adding a new clinic becomes adding an agent, not growing one prompt.
>
> **In this demo they are deliberately NOT separated.** One orchestrator plays both roles, switching by `stage` (`reception → triage → confirm → recommend`), and the API returns `agent: "reception" | "clinic_assistant"` so the UI can show the handoff. The brief asked for a small prototype and one agent; the stage boundaries in `backend/app/agent.py` are where the split would happen.

## 6b. Booking: schedule, confirm, ticket

The assistant acts as customer service after the decision, but **the AI never books**:

```
patient: "I'd like Dr. X, what times?"
   ▼
LLM calls get_available_slots(doctor_id)     ← only for a doctor returned in THIS conversation
   ▼
UI shows 5 real open times (DB rows) with a Confirm button
   ▼
patient taps Confirm  ─▶  POST /api/v1/bookings {session_id, slot_id}      ← not an LLM call
   ▼
code checks: conversation exists · care path is bookable (never emergency/urgent)
             · this slot was OFFERED in this conversation
   ▼
one DB transaction: lock slot row → still open? → mark booked → insert booking
   ▼
ticket number generated BY THE DATABASE (sequence + column default): HT-2026-000001
   ▼
fixed-template reply: "Your appointment is booked. Your ticket number is HT-2026-000001…"
```

| Risk | Guard |
|---|---|
| Booking a doctor who isn't in the system | Times can only be fetched for doctors a DB search returned; confirm only accepts slots that were offered |
| AI books on its own or announces a fake booking | No booking tool exists for the model. A reply saying "booked" / "ticket" / "تم الحجز" is replaced |
| AI invents a ticket number | The ticket comes from a Postgres sequence in the INSERT; the reply is a template filled from the DB row |
| Two patients take the same time at once | Row lock (`SELECT … FOR UPDATE`) + `UNIQUE(slot_id)`; tested with two concurrent threads: exactly one wins |
| Booking during an emergency | Refused: emergency sessions are locked |

A booking is linked to the patient's file **only if reception verified their identity**; guests book without one. The hospital receives the file number (it's their patient), never the medical history or today's symptoms.

### Notifying the doctor / hospital

A booking isn't finished until the doctor and the hospital know about it. The demo builds the real pattern with a **mock sender** (nothing leaves the machine):

- In the **same transaction** as the booking, two outbox rows are written: a **doctor email** and a **hospital-system message** shaped as a FHIR `Appointment`.
- After commit, a mock sender marks them `sent` and logs them. `GET /api/v1/hospital/inbox?ticket=HT-2026-000001` shows what the doctor and the hospital received for that one ticket (you must know the ticket number).
- **Privacy:** notifications contain the time, place and ticket, never the patient's symptoms.
- Tested: a booking that rolls back produces **no** notifications, and its slot stays open.

Example: what Dr. Faisal Al-Harbi (DOC-001) receives:

```
To: doc-001@hosp-001.demo-hospital.example
Subject: New appointment HT-2026-000005: Thu 01 Oct 2026, 15:00
A new in-person visit has been booked through HealTrip.
Ticket: HT-2026-000005 · Time: 15:00 (Asia/Riyadh) · Demo Riyadh Heart Institute, 12 Demo King Fahd Rd
```
and the hospital system receives:
```json
{"resourceType":"Appointment","status":"booked","identifier":"HT-2026-000005",
 "start":"2026-10-01T12:00:00+00:00","minutesDuration":30,
 "participant":["Practitioner/DOC-001","Location/HOSP-001"]}
```

In production the mock sender becomes a background worker calling a real email provider and the hospital's HIS:

```
booking INSERT  +  outbox row "booking.confirmed"      ← same DB transaction (transactional outbox)
        ▼
notification worker reads the outbox, retries until delivered
        ├─▶ hospital system: FHIR `Appointment` resource / webhook into the hospital's HIS
        ├─▶ doctor: email / SMS / calendar invite (no symptom details, only time + ticket)
        └─▶ patient: confirmation + reminder 24h before
        ▼
hospital acknowledges → booking status: pending_hospital → confirmed
(no acknowledgement within N hours → a HealTrip coordinator follows up)
```

Why an outbox: if the email or hospital API is down, the booking is still saved and the notification is retried. A booking can never exist without its notification eventually being sent, and a notification is never sent for a booking that rolled back.

---

## 7. Database Schema

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

patients (mock CRM file)
────────
id PK · file_number UNIQUE (MRN-100001) · national_id UNIQUE · date_of_birth · name_en / name_ar · sex
city · preferred_language · known_conditions[] · allergies[] · last_visit_date · last_visit_specialty FK

offers
──────
id PK (OFF-001) · kind (cross_sell | upsell) · title/description en+ar · applicable_paths[]
specialty_code FK? · requires_travel · price · currency

bookings (M5)
────────
id PK · ticket_number UNIQUE, DEFAULT 'HT-' || year || '-' || lpad(nextval('ticket_seq'), 6, '0')
slot_id UNIQUE FK → slots · doctor_id FK → doctors · patient_id FK → patients (null = guest) · created_at

notifications (M5, outbox)
─────────────
id · booking_id FK → bookings · channel (doctor_email | hospital_system)
recipient · subject · body · status (pending | sent | failed) · attempts · sent_at
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
| POST | `/api/v1/triage` (message + structured facts → care path; no LLM) | ✅ |
| POST | `/api/v1/chat` (`{session_id?, message, language?}` → reply, `agent` (reception / clinic assistant), clinic, care path, provider cards, offers, open times, trace) | ✅ |
| POST | `/api/v1/bookings` (`{session_id, slot_id}` → DB ticket number; called by the patient's Confirm, never the model) | ✅ |
| GET | `/api/v1/hospital/inbox?ticket=` (**demo only**: what the doctor/hospital received for one ticket) | ✅ |

Interactive OpenAPI docs: `http://localhost:8000/docs`.

## 12. Future Architecture

- **Booking hardening**: temporary slot holds while the patient decides, idempotent confirmation, cancellation/rescheduling, and doctor/hospital notification via the outbox (see the Booking section above).
- **CRM / case pipeline** with an audit log of every AI turn and tool call, plus a **human review queue** for high-risk or unclear cases.
- **RAG** for unstructured knowledge (procedures, travel/visa, insurance), with cited chunks and refusal below a similarity threshold.
- Provider onboarding API for partner hospitals; real availability via integrations.
- Evaluation harness: a fixed set of Arabic/English patient scenarios run against every prompt/model change, with the triage path asserted.
- Observability: per-turn traces (facts extracted → path → tools → reply), and cost/latency per conversation.

### Revenue layer: upsell & cross-sell

HealTrip is a medical-travel business, so the decision flow is also where revenue happens. The rule: **selling must never touch the clinical decision.**

- The care path is computed **before and independently of** any offer. The triage code has no access to prices or partners.
- **No offers on emergency or urgent paths.** The offer step isn't even called.
- Offers come from an `offers` table, like providers do, so the LLM can't invent a package or a price. The model never even receives the offers; the UI renders them from DB rows.
- They're shown only **after the patient confirms** the clinic assistant's summary.
- Travel offers only appear when a matched doctor is abroad.
- Paid placement never changes provider ranking.

| Care path | Cross-sell (related service) | Upsell (better tier) |
|---|---|---|
| Second opinion | Remote records review before travel; medical-report translation | Priority review (faster turnaround), senior-consultant tier |
| Specialist abroad | Visa support, hotel near hospital, airport transfer, interpreter | VIP concierge: private transfer, companion stay, dedicated coordinator |
| Specialist / routine | Check-up package at the matched hospital (e.g. cardiac screening) | Executive check-up |
| Family history given | Family screening via HealTrip's Health Tree (شجرة الصحة) | — |
| After treatment | Follow-up telemedicine, rehab package | Extended follow-up plan |
