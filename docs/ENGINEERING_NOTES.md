# Engineering notes

[← back to README](../README.md)


## What live testing found: what live testing found

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

## Hosting

**Daily job (Vercel Cron, 03:00 UTC):** `GET /api/v1/admin/cron` (secret-protected) keeps 14 days of open slots for every bookable doctor (idempotent insert; booked slots untouched), deletes expired conversations and old rate-limit rows, and creates any newly added tables (never drops or alters).

**Request path:** browser → Next.js proxy route (adds the secret proxy key and the client IP) → FastAPI. The backend refuses `/api/*` without the key.

**Hosting (the live demo):** two Vercel projects, the Next.js UI and the FastAPI API (`healtrip-api`), with Neon Postgres. On serverless hosting each request can land on a different instance, so conversation state lives in Postgres (`chat_sessions`, 30-minute expiry), not in process memory. A `daily_usage` counter caps chat turns per day. The hosted database was seeded through a one-time endpoint protected by a random token (`POST /api/v1/admin/seed`), which answers 404 once the token is removed; the database port wasn't reachable from the development machine.

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
