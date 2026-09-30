"""The orchestrator: ONE agent whose steps are fixed by code.

Every turn:
  1. red-flag pre-check on the raw message          (code)  → emergency ends here, no LLM
  2. extract structured facts from the conversation (LLM, forced tool call, schema-validated)
  3. decide the care path                           (code: triage.decide)
  4a. emergency / urgent → fixed message + ER hospitals from the DB   (code, no LLM text)
  4b. need more info     → LLM phrases the question the rules asked for
  4c. path decided       → LLM calls tools through the gateway, then explains the next step
  5. every LLM reply passes the no-diagnosis and grounding checks before the patient sees it
"""
import json
import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import no_diagnosis, red_flags, repository as repo
from app.llm import LLM, LLMUnavailable
from app.tools import TOOL_SCHEMAS, ToolGateway, hospital_card
from app.triage import (EMERGENCY_MESSAGE, URGENT_MESSAGE, CarePath, PatientFacts, TriageResult,
                        decide)

log = logging.getLogger("healtrip.agent")
MAX_TOOL_ROUNDS = 3
HISTORY_WINDOW = 12


# ── Session (in memory only: nothing about the patient is persisted) ──
@dataclass
class ChatSession:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    facts: PatientFacts = field(default_factory=PatientFacts)
    prefs: dict = field(default_factory=dict)          # city / country / language for search
    history: list[dict] = field(default_factory=list)  # {"role", "content"} text only
    known_doctor_ids: set[str] = field(default_factory=set)
    emergency_locked: bool = False                     # once emergency, stays emergency


@dataclass
class TurnResult:
    reply: str
    language: str
    triage: TriageResult | None
    providers: list[dict] = field(default_factory=list)
    hospitals: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)   # what the output checks removed (for transparency)


# ── Fixed texts (reviewed once; used when the LLM is unavailable or its reply is rejected) ──
QUESTIONS = {
    "chief_complaint": {"en": "What is the main health concern you'd like help with?",
                        "ar": "ما هي المشكلة الصحية الأساسية التي تريد المساعدة بها؟"},
    "pain_now": {"en": "Is the chest pain happening right now?", "ar": "هل ألم الصدر موجود الآن؟"},
    "associated": {"en": "Do you have any of these: shortness of breath, fainting or dizziness, sweating or nausea, "
                         "or pain spreading to your arm, jaw, back or shoulder?",
                   "ar": "هل لديك أيٌّ من التالي: ضيق في التنفس، إغماء أو دوخة، تعرّق أو غثيان، "
                         "أو ألم ينتشر إلى الذراع أو الفك أو الظهر أو الكتف؟"},
    "onset": {"en": "When did the chest pain first start: today, in the last few days, or weeks ago or longer?",
              "ar": "متى بدأ ألم الصدر أول مرة: اليوم، خلال الأيام الماضية، أم قبل أسابيع أو أكثر؟"},
    "has_diagnosis_to_review": {"en": "Have you already been given a diagnosis or treatment plan for this that you'd like another doctor to review?",
                                "ar": "هل لديك تشخيص أو خطة علاج سابقة لهذه الحالة وتريد أن يراجعها طبيب آخر؟"},
}
PATH_TEXT = {
    CarePath.specialist: {"en": "Based on your answers, the right next step is an assessment by a cardiologist.",
                          "ar": "بناءً على إجاباتك، الخطوة المناسبة التالية هي تقييم من طبيب قلب."},
    CarePath.second_opinion: {"en": "Based on your answers, the right next step is a second opinion from a cardiologist who can review your existing diagnosis.",
                              "ar": "بناءً على إجاباتك، الخطوة المناسبة التالية هي رأي طبي ثانٍ من طبيب قلب يراجع تشخيصك الحالي."},
    CarePath.routine: {"en": "Based on your answers, the right next step is a visit to a family medicine doctor.",
                       "ar": "بناءً على إجاباتك، الخطوة المناسبة التالية هي زيارة طبيب أسرة."},
}
OPTIONS_TEXT = {"en": " These are matching options from the HealTrip provider network:",
                "ar": " هذه خيارات مطابقة من شبكة مقدمي الخدمة في HealTrip:"}
NO_RESULTS = {"en": "I couldn't find a matching provider in the current HealTrip provider database.",
              "ar": "لم أجد مقدم خدمة مطابقًا في قاعدة بيانات HealTrip الحالية."}
DB_DOWN = {"en": "I'm unable to access the provider database right now. Please try again later.",
           "ar": "لا أستطيع الوصول إلى قاعدة بيانات مقدمي الخدمة الآن. يُرجى المحاولة لاحقًا."}
AI_DOWN = {"en": "The AI service is temporarily unavailable. Please try again. If you feel unwell now, call 997.",
           "ar": "خدمة الذكاء الاصطناعي غير متاحة مؤقتًا. يُرجى المحاولة مرة أخرى. إذا كنت تشعر بتوعك الآن، اتصل بالرقم 997."}


def detect_language(message: str, hint: str | None) -> str:
    return "ar" if re.search(r"[؀-ۿ]", message) else (hint or "en")


# ── Step 2: fact extraction (the LLM's main job) ──
EXTRACT_TOOL = {"type": "function", "function": {
    "name": "record_facts",
    "description": "Record facts the patient has CLEARLY stated. Leave a field out if not stated. Never guess.",
    "parameters": {"type": "object", "properties": {
        "chief_complaint": {"type": "string", "enum": ["chest_pain", "other"]},
        "pain_now": {"type": "boolean", "description": "Is the chest pain happening right now?"},
        "onset": {"type": "string", "enum": ["today", "days", "weeks_or_more"]},
        "shortness_of_breath": {"type": "boolean"},
        "fainting_or_dizziness": {"type": "boolean"},
        "sweating_or_nausea": {"type": "boolean"},
        "pain_spreads": {"type": "boolean", "description": "Pain spreads to arm, jaw, back or shoulder"},
        "has_diagnosis_to_review": {"type": "boolean", "description": "Has an existing diagnosis/treatment plan to be reviewed"},
        "city": {"type": "string", "description": "City the patient wants care in, in English"},
        "country": {"type": "string", "description": "ISO-2 country code"},
        "language": {"type": "string", "description": "ISO-639-1 language the patient wants the doctor to speak"},
    }},
}}
EXTRACT_PROMPT = (
    "You extract structured facts from a patient conversation for a care-navigation system. "
    "Call record_facts with ONLY the facts the patient clearly stated, in any language. "
    "If the patient answers 'no' to a grouped question (e.g. 'none of these'), set each asked field to false. "
    "Do not infer, do not diagnose, do not add fields the patient did not mention.\n"
    "Facts already known: {known}"
)
FACT_FIELDS = set(PatientFacts.model_fields)


def _merge_facts(s: ChatSession, args: dict) -> None:
    updates = {k: v for k, v in args.items() if k in FACT_FIELDS and v is not None}
    merged = s.facts.model_dump() | updates
    s.facts = PatientFacts.model_validate(merged)  # invalid enum values raise → handled by caller
    for k in ("city", "country", "language"):
        if args.get(k):
            s.prefs[k] = args[k]


def extract(llm: LLM, s: ChatSession) -> None:
    msgs = [{"role": "system", "content": EXTRACT_PROMPT.format(known=s.facts.model_dump_json(exclude_none=True))},
            *s.history[-HISTORY_WINDOW:]]
    out = llm.chat(msgs, tools=[EXTRACT_TOOL], tool_choice={"type": "function", "function": {"name": "record_facts"}})
    for call in out.get("tool_calls") or []:
        if call["function"]["name"] == "record_facts":
            try:
                _merge_facts(s, json.loads(call["function"]["arguments"] or "{}"))
            except Exception:  # malformed extraction: keep previous facts, rules will ask again
                log.warning("extraction_rejected session=%s", s.id)


# ── Output checks ──
_ID = re.compile(r"\b(DOC|HOSP)-\d{3}\b")


def grounded(text: str, gw: ToolGateway, s: ChatSession) -> bool:
    """Every provider ID the reply mentions must have come from a tool call."""
    allowed = set(gw.doctors) | set(gw.hospitals) | s.known_doctor_ids | {c["hospital_id"] for c in gw.doctors.values()}
    return all(m.group(0) in allowed for m in _ID.finditer(text))


# ── Step 4a: deterministic safety paths ──
def _er_hospitals(db: Session, s: ChatSession) -> list[dict]:
    try:
        hs = repo.search_hospitals(db, city=s.prefs.get("city"), has_emergency=True) if s.prefs.get("city") else []
        return [hospital_card(h) for h in hs]
    except SQLAlchemyError:
        return []  # the safety message stands on its own; hospitals are a bonus


def _safety_turn(db, s, lang, result: TriageResult, trace) -> TurnResult:
    text = (EMERGENCY_MESSAGE if result.care_path is CarePath.emergency else URGENT_MESSAGE)[lang]
    return TurnResult(reply=text, language=lang, triage=result, hospitals=_er_hospitals(db, s), trace=trace)


# ── The turn ──
def run_turn(db: Session, llm: LLM, s: ChatSession, message: str, lang_hint: str | None = None) -> TurnResult:
    lang = detect_language(message, lang_hint)
    s.history.append({"role": "user", "content": message})
    trace: list[dict] = []

    # 1. Red flags in the patient's own words: no LLM involved at all.
    hits = red_flags.check(message)
    if hits or s.emergency_locked:
        s.emergency_locked = True
        rule = hits[0].id if hits else "E_LOCKED"
        result = TriageResult(care_path=CarePath.emergency, rule_id=rule, reason="Emergency red flag")
        trace.append({"step": "red_flag_precheck", "result": [h.id for h in hits] or ["session already emergency"]})
        return _finish(s, _safety_turn(db, s, lang, result, trace))

    # 2. Extract facts.
    try:
        extract(llm, s)
        trace.append({"step": "extract_facts", "facts": s.facts.model_dump(exclude_none=True), "prefs": s.prefs})
    except LLMUnavailable:
        trace.append({"step": "extract_facts", "error": "llm_unavailable"})
        return _finish(s, TurnResult(reply=AI_DOWN[lang], language=lang, triage=None, trace=trace))

    # 3. Decide (code).
    result = decide(s.facts)
    trace.append({"step": "triage", "care_path": result.care_path.value, "rule": result.rule_id, "ask_next": result.ask_next})

    # 4a. Emergency / urgent.
    if result.care_path in (CarePath.emergency, CarePath.urgent):
        s.emergency_locked = result.care_path is CarePath.emergency
        return _finish(s, _safety_turn(db, s, lang, result, trace))

    # 4b. Ask what the rules need.
    if result.care_path is CarePath.need_more_info:
        key = "associated" if len(result.ask_next) > 1 else result.ask_next[0]
        return _finish(s, _ask(llm, s, lang, key, result, trace))

    # 4c. Path decided: tools.
    return _finish(s, _recommend(db, llm, s, lang, result, trace))


def _ask(llm, s, lang, key, result, trace) -> TurnResult:
    template = QUESTIONS[key][lang]
    prompt = (f"You are HealTrip's care-navigation assistant. Reply in {'Arabic' if lang == 'ar' else 'English'}. "
              f"Briefly acknowledge the patient, then ask exactly this question in your own words: \"{template}\". "
              "Maximum 2 sentences. Do not diagnose, do not name conditions, do not give medical advice.")
    try:
        text = llm.chat([{"role": "system", "content": prompt}, *s.history[-HISTORY_WINDOW:]]).get("content") or template
    except LLMUnavailable:
        text = template  # the question is fixed text anyway: degrade to it
    text, blocked = no_diagnosis.enforce(text, lang)
    if blocked:
        text = no_diagnosis.SAFE_REPLY[lang] + " " + template
    trace.append({"step": "ask", "question_for": result.ask_next, "blocked": blocked})
    return TurnResult(reply=text, language=lang, triage=result, trace=trace, blocked=blocked)


def _recommend(db, llm, s, lang, result, trace) -> TurnResult:
    gw = ToolGateway(session=db, triage=result, known_doctor_ids=s.known_doctor_ids)
    prompt = (
        f"You are HealTrip's care-navigation assistant. Reply in {'Arabic' if lang == 'ar' else 'English'}.\n"
        f"The clinical rules have decided the care path: {result.care_path.value} ({result.reason}). "
        "You do not change this decision and you never diagnose, name conditions or suggest treatment.\n"
        f"Patient search preferences: {json.dumps(s.prefs)}. "
        "Call search_providers to find matching doctors. If a location filter returns nothing, you may retry "
        "once without it. Then write at most 3 short sentences: state the next step and refer to doctors ONLY by "
        "the doctor_id values returned by the tools, written like [DOC-001]. Do not repeat details: the app shows "
        "provider cards from the database. If the tools return no results, say so plainly."
    )
    msgs = [{"role": "system", "content": prompt}, *s.history[-HISTORY_WINDOW:]]
    text = ""
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            out = llm.chat(msgs, tools=TOOL_SCHEMAS)
            calls = out.get("tool_calls") or []
            if not calls:
                text = out.get("content") or ""
                break
            msgs.append({"role": "assistant", "content": out.get("content"), "tool_calls": calls})
            for c in calls:
                msgs.append({"role": "tool", "tool_call_id": c["id"],
                             "content": gw.call(c["function"]["name"], c["function"]["arguments"])})
    except LLMUnavailable:
        trace += gw.trace + [{"step": "recommend", "error": "llm_unavailable"}]
        return TurnResult(reply=AI_DOWN[lang], language=lang, triage=result, trace=trace)

    trace += gw.trace
    blocked: list[str] = []
    fallback = PATH_TEXT[result.care_path][lang] + OPTIONS_TEXT[lang]

    # Deterministic outcomes first: the model's text never overrides these facts.
    if gw.db_failed:
        text = DB_DOWN[lang]
    elif not gw.doctors:
        text = PATH_TEXT[result.care_path][lang] + " " + NO_RESULTS[lang]
    else:
        text, blocked = no_diagnosis.enforce(text, lang)
        if blocked or not text.strip():
            text = fallback
        elif not grounded(text, gw, s):
            blocked = ["ungrounded_provider_reference"]
            text = fallback
    s.known_doctor_ids |= set(gw.doctors)
    trace.append({"step": "recommend", "providers": list(gw.doctors), "blocked": blocked})
    return TurnResult(reply=text, language=lang, triage=result, providers=list(gw.doctors.values()),
                      hospitals=list(gw.hospitals.values()), trace=trace, blocked=blocked)


def _finish(s: ChatSession, r: TurnResult) -> TurnResult:
    s.history.append({"role": "assistant", "content": r.reply})
    # Log the decision path, never the patient's words.
    log.info("turn session=%s path=%s rule=%s providers=%s blocked=%s", s.id[:8],
             r.triage.care_path.value if r.triage else None, r.triage.rule_id if r.triage else None,
             [p["doctor_id"] for p in r.providers], r.blocked)
    return r
