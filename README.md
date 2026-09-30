# HealTrip — AI Patient Decision Assistant (Prototype)

**[النسخة العربية ← README.ar.md](README.ar.md)**

> ## 🔗 Live demo: **https://healtrip-demo.vercel.app**
> Try: *"I have chest pain and I'm not sure whether I should see a cardiologist, go to the ER, or seek a second opinion."* When reception asks, use the fictional patient file **`MRN-100001`**, born **12/04/1971**, or say you have no file. Arabic works too (toggle top-right).
> Hosted on Vercel (Next.js UI + FastAPI) with Neon Postgres and DeepSeek. Capped at 300 chat messages/day as a spend guard; appointment slots cover the 14 days after the last seed.
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

**It routes; it never diagnoses.** The assistant never names a condition ("sounds like angina"), guesses a cause, or suggests medication. Its only output is *which kind of care to go to* and *which real doctors can see you*. Only a doctor diagnoses.

HealTrip describes its model as **Decision-First Healthcare**: the right decision comes before the booking. This prototype is built around that idea: *decide safely, then discover providers.*

The engineering principle throughout:

> **The LLM understands and talks. Code decides what is safe. The database decides what is true.**

---

## Patient workflow (how a hospital reception works)

The assistant follows the same path as a real hospital front desk, with safety checked before any paperwork:

```
 ┌──────────────────────────────────────────────────────────────────────────────────────────┐
 │ EVERY message: red-flag check (code). "I can't breathe" / "أغمي علي" → emergency now,     │
 │ fixed message + nearest ER hospitals. No reception, no questions, no booking.             │
 └──────────────────────────────────────────────────────────────────────────────────────────┘
   1  SAFETY FIRST     "Is the chest pain happening right now?"
                        (asked BEFORE the file number: nobody with active chest pain looks for paperwork)
   2  RECEPTION        "Do you have a patient file with us? File number or national ID?"
                        → "What is your date of birth?"  → verified in code → "Thank you, Mr. Ahmed. I found your file."
                        (no file / 3 failed checks → continue as a guest)
   3  TRIAGE           only the questions that can still change the decision; rules pick the clinic
   4  HANDOFF          "I'm transferring you to the Cardiology clinic assistant."
                        "Hello Mr. Ahmed. From your file: hypertension, type 2 diabetes; allergy: penicillin;
                         last visit 2026-03-02 (Internal Medicine). From what you told me today: chest pain,
                         not now, started weeks ago, no warning symptoms. Is this correct?"
                        (template filled from the DB + structured facts: the LLM never paraphrases a medical file)
   5  CONFIRM          patient says yes → continue · says no → "What should I correct?"
   6  DOCTORS + OFFERS matching doctors from the DB + optional services (cross-sell / upsell), only now
   7  SCHEDULE         real open times → patient taps Confirm (the AI cannot book)
   8  TICKET           DB-generated ticket HT-2026-000123 → doctor e-mail + hospital-system message (mocked)
```

### The workflow in screenshots

Real runs of the demo UI (Next.js) against the real backend and a live LLM (DeepSeek). The patient file `MRN-100001` and every doctor are fictional.

**1 · Safety first, then reception and identity check**

![Reception: pain-now question, file number, date of birth, file found](docs/screenshots/01-reception-file-lookup.jpg)

> The first question is *"Is the chest pain happening right now?"*, **before** any paperwork. Only then does reception ask for the file number, then the date of birth. The file is found only when both match: *"Thank you, Mr. Ahmed. I found your file."* That sentence is fixed text, not model output. Each reply has a **"How this answer was produced"** line showing the rule that fired (`Q_PAIN_NOW`, `Q_ASSOCIATED`…); expanding it shows the full trace: facts extracted → rule → tools.

**2 · Handoff to the clinic assistant, which repeats back and asks for confirmation**

![Handoff: transferred to the Cardiology clinic assistant, summary from the file and today's answers](docs/screenshots/02-handoff-clinic-assistant.jpg)

> The rules chose the clinic (`S_CARDIOLOGY`), and the UI shows the handoff line. The summary (conditions, allergies, last visit, today's answers) is a **template filled from the database and structured facts**. The LLM never rewrites a medical record, because a paraphrase is exactly where a condition gets invented or dropped. Nothing is offered until the patient confirms.

**3 · Doctors from the database, then optional services**

![Doctor card from the DB and an optional cardiac check-up package](docs/screenshots/03-doctors-and-offers.jpg)

> The model refers to doctors only by ID (`[DOC-001]`). The UI replaces the ID with the name **from the DB card in the same response**, and the card itself is a DB row. The offer (cross-sell) comes from the `offers` table, appears only after confirmation, and travel offers are hidden because this doctor is in Riyadh.

**4 · Real open times → the patient confirms → ticket from the database**

![Open times, one marked Booked, and the ticket HT-2026-000001](docs/screenshots/04-booking-ticket.jpg)

> The times are real `slots` rows. Tapping **Confirm** calls `POST /bookings` (the model has no booking tool). The ticket `HT-2026-000001` is generated by a Postgres sequence, and the doctor (e-mail) and hospital system (FHIR `Appointment`) are notified through the outbox (mocked).

**5 · Arabic, right-to-left, emergency**

![Arabic RTL: "I have chest pain and can't breathe" → fixed emergency message with 997](docs/screenshots/05-arabic-emergency.jpg)

> *"عندي ألم في صدري وما أقدر أتنفس"* ("I have chest pain and can't breathe") triggers the red-flag rule `RF_BREATHING` on the raw text. **The LLM is never called**: the reply is the fixed, reviewed emergency message with 997 / 911, and the session stays locked in emergency mode (no reception, no doctor search, no booking).

**The same run as text** (abridged):

| Patient | Assistant |
|---|---|
| I have chest pain and I'm not sure whether I should see a cardiologist, go to the ER, or seek a second opinion. | *[reception]* I understand your concern… Is the chest pain happening right now? |
| No, not right now | Thank you for clarifying. Before we continue, do you already have a patient file with us? … file number or national ID. |
| Yes, my file number is MRN-100001 | Thank you. To verify it's you, what is your date of birth? |
| 12/04/1971 | Thank you, Mr. Ahmed. I found your file. Do you have any of these: shortness of breath, fainting or dizziness, sweating or nausea, or pain spreading…? |
| None of those | When did the chest pain first start…? |
| About three weeks ago. I am in Riyadh | Have you already been given a diagnosis… you'd like another doctor to review? |
| No | *[Cardiology clinic assistant]* Hello Mr. Ahmed… From your file: … From what you told me today: … Is this correct? |
| Yes, that is correct | The next step is a cardiology assessment in Riyadh. A matching cardiologist is **[DOC-001]**… *(cards: Dr. Faisal Al-Harbi · offer: Cardiac check-up package)* |
| What times does the doctor have? | [DOC-001] has open in-person times… tap Confirm on the time you want. *(09:00 · 10:00 · 11:00)* |

Why each step is where it is:
- **Safety before identity.** Emergency detection runs on every message, and "is it happening now?" comes before the file number.
- **Identity is verified, not assumed.** A file number alone isn't enough: it must match the date of birth. A failed check says "I couldn't verify these details" and never reveals whether a file exists. After 3 failures the patient continues as a guest. The identifiers are dropped from the session once checked. (Production: Nafath / OTP.)
- **The clinic assistant repeats back and asks for confirmation** before anything is offered, so a misunderstood fact is caught by the patient, not discovered at the clinic.
- **Selling happens only after a confirmed, non-urgent decision.** It is never shown on emergency paths and never influences which clinic was chosen.

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

### Engineering notes: what live testing found

Everything was first tested with a scripted fake LLM (free, deterministic, failure cases on demand). Then the full flow was run against a real model. The real model found problems the fake one couldn't, and **each fix went into code, with a regression test**:

| Found in a live run | Why it happened | Fix (code, not prompt) |
|---|---|---|
| The model told a chest-pain patient *"this is not an emergency, you don't need the ER"* | The rules chose "specialist", but the model added its own reassurance | The output check now blocks false reassurance (EN + AR: *"not an emergency"*, *"وليس الطوارئ"*…) and replaces the reply with fixed text |
| Our own question *"Have you been given a diagnosis?"* was blocked | The no-diagnosis filter blocked the word "diagnosis" itself | Block *stating* a diagnosis, not *asking* about one; a test checks that none of our fixed questions is ever blocked |
| A patient with an existing valve diagnosis (no chest pain) was asked "what is your concern?" 3 times, then sent to primary care | The rules only modelled chest pain → other | New rule `S_REVIEW_OTHER`: an existing diagnosis to review is its own path (second opinion in that specialty) |
| *"لا ما عندي تشخيص"* ("no, I have no diagnosis") was sometimes not recorded, so the same question repeated | Even at temperature 0 the model occasionally left out a field (observed about 1 in 5 in a small sample) | If the rules would ask the same question twice, one **focused** extraction runs on just that question and answer |
| Told *"do not thank the patient"*, the model still wrote *"Thanks, that's verified"* after our fixed *"I found your file"* | A prompt instruction isn't a guarantee | After a fixed prefix, the question is fixed text: the model isn't asked at all |
| DeepSeek can return empty replies | It "thinks" by default and can spend the whole token budget on hidden reasoning | `LLM_DISABLE_THINKING=true` |

The pattern: **when something matters, it is enforced in code and pinned with a test; prompts only shape tone.**

**Measured cost** (DeepSeek `deepseek-flash`, token counts from the API, priced at peak rates): a full 6-message conversation took 14 LLM calls and 11,519 tokens, about **$0.0045**.

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

## 4. Tool Calling (✅ M3)

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

---

## 5. Safety (✅ M2)

Two layers, both code:

1. **Red-flag pre-check** on the raw message (Arabic + English phrase rules). It runs **before** the LLM, so it still works when the AI service is down.
2. **Triage rules** on the structured `PatientFacts`. The rules follow the conservative direction of the [2021 AHA/ACC chest-pain guideline](https://professional.heart.org/en/science-news/2021-guideline-for-the-evaluation-and-diagnosis-of-chest-pain/top-things-to-know), which advises that **acute** chest pain should be handled as an emergency (call EMS), not as a clinic booking. So:
   - chest pain **happening now or started recently** → **emergency**, even without other symptoms
   - chest pain + shortness of breath / fainting / sweating / pain spreading to arm, jaw or back → **emergency**
   - past, resolved or recurring pain with no red flags → **specialist** (cardiology)
   - an existing diagnosis or treatment plan the patient wants reviewed → **second opinion**

On emergency, the flow **stops provider discovery**. The reply is a fixed, reviewed message (not LLM-generated), e.g.:

> "Based on what you've described, this may need urgent medical attention. Please seek emergency care now (in Saudi Arabia call **997**, the Red Crescent ambulance, or **911** where the unified number operates) rather than waiting for a specialist appointment. This assistant cannot diagnose your condition."

The full rule set, in order (code: `backend/app/triage.py`, `backend/app/red_flags.py`):

| Rule | If | Then |
|---|---|---|
| `RF_*` | Raw message contains a red-flag phrase (can't breathe, fainted, crushing/severe chest pain, stroke signs; AR + EN) | **emergency**, and the AI is never called |
| `E_PAIN_NOW` | Chest pain happening now | **emergency** |
| `E_ASSOCIATED` | Chest pain + shortness of breath / fainting / sweating or nausea / pain spreading | **emergency** |
| `Q_PAIN_NOW` → `Q_ASSOCIATED` → `Q_ONSET` → `Q_REVIEW` | That fact is still unknown | ask it next (only questions that can still change the path) |
| `U_RECENT` | Started today, gone now, no symptoms | **urgent**: same-day ER visit, not a booking |
| `S_REVIEW` | Older pain, no symptoms, existing diagnosis to review | **second opinion** (cardiology) |
| `S_CARDIOLOGY` | Older pain, no symptoms | **specialist** (cardiology) |
| `R_OTHER` | Not chest pain, no red flag | **routine** (family medicine) |

Emergency checks run first, so a "yes" to any warning sign ends the questions immediately, and a second-opinion request can never override an emergency. Questions such as age aren't asked, because in these rules they wouldn't change the path.

`POST /api/v1/triage` exposes the same function directly, with no LLM, so the rules can be tested in isolation:

```bash
curl -X POST localhost:8000/api/v1/triage -H 'Content-Type: application/json' \
  -d '{"message":"عندي ألم في صدري وما أقدر أتنفس","language":"ar"}'
# → care_path: emergency, rule: RF_BREATHING, fixed Arabic emergency message with 997
```

Rules are conservative (they over-triage rather than under-triage), listed in one file, unit-tested in both languages, and **documented as prototype rules, not clinical guidance**. A real deployment needs clinician-authored and clinically validated rules.

**No diagnosis, enforced in code** (`backend/app/no_diagnosis.py`, ✅): every LLM reply is checked before the patient sees it. A reply that names a condition (angina, reflux, ذبحة, ارتجاع…), guesses a cause ("it's probably…", "يبدو أنه…") or suggests medication ("take an aspirin", "خذ حبة…") is blocked and replaced with a fixed message: *"I can't tell you what is causing your symptoms. Only a doctor can assess that. What I can do is help you reach the right kind of doctor."* A prompt instruction alone isn't enough, because models drift into diagnosing when they try to be helpful.

Why the LLM isn't trusted here: it isn't that LLMs are always worse at triage. Published results are mixed, and one study found ChatGPT recognised high-acuity patients better than triage nurses ([JMIR 2024](https://doaj.org/article/3e9c85a398b543bab44f5293f804c800)). The problem is **repeatability and auditability**: another study found ChatGPT's triage had poor repeatability, with 47.5% accuracy and a 13.7% under-triage rate on simulated patients ([Emergency Care Journal](https://www.pagepressjournals.org/ecj/article/download/15130/14090/101289)). A safety decision must give the same answer every time and be testable line by line, and `if pain_now: EMERGENCY` is.

---

## 6. Hallucination Prevention (✅ M3, enforced architecturally, not by prompt)

| Risk | Guard |
|---|---|
| Invented doctor/hospital | Provider cards in the UI are rendered **from tool results (DB rows)**, never from model text |
| Model mentions a provider not returned | Grounding check: every provider ID in the reply must be in this turn's tool results. Otherwise the reply is replaced with a safe template built from the tool results |
| Empty search | Tool returns an explicit `no_results`; reply: *"I couldn't find a matching provider in the current HealTrip database."* |
| DB down | Tool returns `unavailable`; reply: *"I'm unable to access the provider database right now."* No results are ever produced from model memory |
| Invented specialty | `specialty` is an enum of codes that exist in the DB; any other value is rejected at the tool boundary |
| Readable IDs | `DOC-001`, `HOSP-001` make the grounding rule easy to audit in logs and tests |

---

## 6b. Booking: schedule, confirm, ticket (✅ M5)

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

### Notifying the doctor / hospital (✅ mocked end-to-end)

A booking isn't finished until the doctor and the hospital know about it. The demo builds the real pattern with a **mock sender** (nothing leaves the machine):

- In the **same transaction** as the booking, two outbox rows are written: a **doctor email** and a **hospital-system message** shaped as a FHIR `Appointment`.
- After commit, a mock sender marks them `sent` and logs them. `GET /api/v1/hospital/inbox?doctor_id=DOC-001` shows exactly what the doctor and the hospital received.
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
| GET | `/api/v1/hospital/inbox?doctor_id=` (**demo only**: what the doctor/hospital received) | ✅ |

Interactive OpenAPI docs: `http://localhost:8000/docs`.

---

## 9. Security

**In the prototype:**
- Validation at every boundary (query patterns, ranges, result cap of 20; tool inputs validated before queries)
- Bound parameters only; no string-built SQL
- Secrets in environment variables; the AI key lives only in the backend, never in the frontend
- CORS allow-list (never `*`)
- The database is reachable only through the backend
- Patient identity: file number / national ID **+ date of birth**, identical failure message, 3-attempt cap, identifiers dropped after the check ✅
- Notifications carry the file number to the hospital (its own patient), never the medical history or today's symptoms ✅
- Tool authorisation per care path ✅
- Simple per-client rate limit on `/chat` (20/min, in memory) ✅
- No patient accounts, and no conversation stored beyond the session; logs record event types and IDs, not symptom text ✅

**Required before production (not done here):** authentication and consent, encryption at rest, audit logging, data-residency review (Saudi PDPL / NCA controls), an **SFDA regulatory assessment** (software that guides clinical decisions may count as a medical device under [SFDA MDS-G010](https://www.sfda.gov.sa/en/guide/guidance-artificial-intelligence-and-machine-learning-aiml-enabled-medical-devices-mds-%E2%80%93-g010)), clinician-validated triage rules and clinical governance, BAAs/DPAs with the LLM provider, penetration testing, and monitoring. **This prototype claims no regulatory compliance.**

---

## 10. Error Handling

One error shape everywhere: `{"error": {"code", "message", "request_id?"}}`. Stack traces and SQL never reach the client; they are logged server-side under the `request_id`.

| Failure | Behaviour |
|---|---|
| Invalid input | `422 invalid_input`, rejected before any query ✅ |
| Unknown doctor / hospital | `404 not_found` ✅ |
| DB unavailable | `503 database_unavailable`; the chat says it can't reach the provider database, and fabricates nothing ✅ |
| AI provider down / timeout | one retry, then *"The AI service is temporarily unavailable… if you feel unwell now, call 997."* The red-flag pre-check still runs ✅ |
| No matching provider | explicit "no matching provider in the current database", even if the model claims otherwise ✅ |
| Emergency | provider discovery stops; fixed emergency message ✅ |

---

## 11. Assumptions

- No real HealTrip infrastructure, data or clinical rules were provided, so providers, prices and slots are fictional.
- Emergency numbers shown are Saudi Arabia's (997 ambulance; 911 unified in Riyadh, Makkah, Madinah and the Eastern Province), since HealTrip is registered in Saudi Arabia.
- Triage rules are illustrative and conservative, written by an engineer, not a clinician.
- "Second opinion" means a patient who already has a diagnosis or treatment plan and wants it reviewed, often remotely before travelling.
- The chat replies in the patient's language; DB search uses language-independent codes (`cardiology`, `SA`, `ar`).

---

## 12. Future Architecture

- **Booking hardening**: temporary slot holds while the patient decides, idempotent confirmation, cancellation/rescheduling, and doctor/hospital notification via the outbox (see §6b).
- **CRM / case pipeline** with an audit log of every AI turn and tool call, plus a **human review queue** for high-risk or unclear cases.
- **RAG** for unstructured knowledge (procedures, travel/visa, insurance), with cited chunks and refusal below a similarity threshold.
- Provider onboarding API for partner hospitals; real availability via integrations.
- Evaluation harness: a fixed set of Arabic/English patient scenarios run against every prompt/model change, with the triage path asserted.
- Observability: per-turn traces (facts extracted → path → tools → reply), and cost/latency per conversation.

### Revenue layer: upsell & cross-sell (✅ minimal version built)

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

---

## Roadmap

Deliberately small: a demo that makes the engineering decisions visible, not a platform. Priority order: safety first, visual polish last.

| # | Milestone | Status |
|---|---|---|
| M1 | Data foundation: PostgreSQL schema, mock network, provider API | ✅ done |
| M2 | **Safety / triage layer**: red-flag pre-check + care-path rules (AR/EN), unit-tested | ✅ done |
| M3 | **AI agent + tools**: fact extraction, relevant questions, `search_providers` / `get_*_details`, grounding check, failure handling | ✅ done: scripted-LLM tests + live DeepSeek runs (EN/AR) |
| M4 | Chat UI (Arabic / English, RTL, handoff, cards, booking, trace) | ✅ done |
| M5 | Booking: real open times → patient confirms → DB ticket number | ✅ done, incl. mocked doctor/hospital notification |
| M5b | Reception workflow: patient file lookup (mock CRM), clinic-assistant handoff + confirmation, offers | ✅ done |
| M6 | README walkthrough with screenshots + public demo | ✅ done: https://healtrip-demo.vercel.app |

**87 automated tests** (`backend/tests/`), run against a real Postgres test database, with a scripted fake LLM for the agent tests.

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

# frontend (second terminal)
cd frontend && npm install && npm run build && npx next start -p 3100   # http://localhost:3100
```

**Hosting (the live demo):** two Vercel projects, the Next.js UI and the FastAPI API (`healtrip-api`), with Neon Postgres. On serverless hosting each request can land on a different instance, so conversation state lives in Postgres (`chat_sessions`, 30-minute expiry), not in process memory. A `daily_usage` counter caps chat turns per day. The hosted database was seeded through a one-time endpoint protected by a random token (`POST /api/v1/admin/seed`), which answers 404 once the token is removed; the database port wasn't reachable from the development machine.

The frontend proxies `/api/*` to the backend (`BACKEND_URL`, default `http://localhost:8000`), so the browser never sees the backend URL, the database or the AI key. Set `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` in `backend/.env` (see `.env.example`). Any OpenAI-compatible provider works.
