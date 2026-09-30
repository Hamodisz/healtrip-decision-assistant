# Safety, anti-hallucination, security & errors

[← back to README](../README.md)


## How this prototype stops the AI from inventing information

**The rule:** the AI can only talk about what the database returned in this conversation. The data here is mock data, but it's *the* source of truth: the check isn't "does this doctor exist in the real world?", it's **"did this come from our database?"**. Mock doctors pass; anything the model makes up beyond the database is blocked.

It's enforced in **code**, in five layers, not by asking the model nicely in a prompt:

| # | Layer | What it means in practice |
|---|---|---|
| 1 | **No knowledge path except tools** | The model has no provider list in its prompt. The only way to get a doctor, hospital or time is a tool call that runs a real DB query (`backend/app/tools.py`). |
| 2 | **Code owns the clinical choice** | The care path and specialty come from deterministic rules (`triage.py`). The model can't change the specialty it searches for; the tool gateway overrides it. |
| 3 | **IDs only, names come from the database** | The model may refer to a provider only as `[DOC-001]`. The UI replaces the ID with the name from the DB card in the same response, and cards are DB rows, never model text. |
| 4 | **Every reply is checked before the patient sees it** | IDs must come from this conversation's tool results; any doctor/hospital *name* written by the model is rejected (EN + AR); no diagnosis; no false reassurance; no "you're booked". A rejected reply is replaced with fixed text built from DB data (`agent.py`, `no_diagnosis.py`). |
| 5 | **Facts that matter are never generated** | Ticket numbers come from a Postgres sequence; open times are DB rows; the patient's file summary is a template filled from DB fields; emergency messages are fixed text. |

**Real example** (live DeepSeek run, after the patient's path was decided):

| Patient asks for someone who isn't in the database | What the patient sees |
|---|---|
| *"Can I book with Dr. Ahmed Al-Zahrani at King Faisal Specialist Hospital instead?"* | *"I can only book doctors in the HealTrip network, and I couldn't find that doctor or hospital in it. These are matching options from the HealTrip provider network:"* + card: **Dr. Faisal Al-Harbi** (from the DB) |
| *"أبغى الدكتور خالد العمري في مستشفى الحبيب، هل هو متاح؟"* | *"أستطيع الحجز فقط مع أطباء شبكة HealTrip، ولم أجد هذا الطبيب أو المستشفى فيها…"* + the same DB card |

The model's own answer repeated the invented name, so layer 4 rejected it and the fixed text answered the patient's actual question. Neither name was ever shown as an available provider.

**Try it yourself** on the [live demo](https://healtrip-demo.vercel.app): get to the doctor cards, then ask for any doctor or hospital that isn't in the mock data. Expand *"How this answer was produced"* to see `blocked: ["ungrounded_provider_reference"]`.

**Proven by tests**, not just claimed: the agent tests use a scripted fake LLM that deliberately misbehaves: it invents `DOC-099`, invents names in English and Arabic, claims three doctors in Tokyo when the DB found none, announces a booking, diagnoses "angina", and tries to search orthopedics instead of cardiology. Every case has a test, and I checked that the tests **fail when the guard is switched off** (then restored it).

**Why code, not prompts:** building my own AI product (SportSyncAI) taught me that a prompt rule doesn't hold once a model is confident. A rule written in the prompt *and* repeated was still broken in live tests, so every rule that could break the product became a code gate with a deterministic fallback, plus a separate verification step that never lets an unchecked answer through when the checker itself fails. Live testing of this prototype showed the same thing again: told "don't thank the patient", the model still did; told to stay on the decided path, it still added "you don't need the ER". Each of those became a code check with a regression test (see [ENGINEERING_NOTES.md](ENGINEERING_NOTES.md)).

**Production next steps:** an independent verifier pass (a second model call that checks every claim in the reply against the tool results before it's shown), logging and weekly review of every blocked reply, and a fixed Arabic/English evaluation set run on every prompt or model change.

## 5. Safety

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

## 6. Hallucination Prevention

| Risk | Guard |
|---|---|
| Invented doctor/hospital | Provider cards in the UI are rendered **from tool results (DB rows)**, never from model text |
| Model mentions a provider not returned | Grounding check: every provider ID in the reply must be in this turn's tool results. Otherwise the reply is replaced with a safe template built from the tool results |
| Empty search | Tool returns an explicit `no_results`; reply: *"I couldn't find a matching provider in the current HealTrip database."* |
| DB down | Tool returns `unavailable`; reply: *"I'm unable to access the provider database right now."* No results are ever produced from model memory |
| Invented specialty | `specialty` is an enum of codes that exist in the DB; any other value is rejected at the tool boundary |
| Readable IDs | `DOC-001`, `HOSP-001` make the grounding rule easy to audit in logs and tests |
| Invented name with no ID (*"Dr. Ahmed at King Faisal Hospital"*, *"الدكتور أحمد في مستشفى…"*) | The model may refer to providers **only by ID**; the UI turns IDs into names from DB cards. Any doctor/facility **name** in model text (EN + AR patterns) counts as a violation and the reply is replaced. This applies to question turns too, which have no tool results at all. (Found on review: the first version only checked IDs, and 5 tests now pin this.) |

## 9. Security

**In the prototype:**
- Validation at every boundary (query patterns, ranges, result cap of 20; tool inputs validated before queries)
- Bound parameters only; no string-built SQL
- Secrets in environment variables; the AI key lives only in the backend, never in the frontend
- CORS allow-list (never `*`)
- The database is reachable only through the backend
- Patient identity: file number / national ID **+ date of birth**, identical failure message, 3-attempt cap; file/ID numbers extracted in code and never sent to the LLM or stored in chat history; the date-of-birth answer is erased after the check ✅
- Notifications carry the file number to the hospital (its own patient), never the medical history or today's symptoms ✅
- Tool authorisation per care path ✅
- Simple per-client rate limit on `/chat` (20/min, in memory) ✅
- No patient accounts; conversation text is kept in the DB for at most 30 minutes (serverless needs shared state); logs record event types and IDs, not symptom text ✅
- The red-flag emergency reply is returned even when the database is down or the daily cap is reached ✅ (audit finding, now tested)

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
