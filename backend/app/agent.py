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

from datetime import date

from app import no_diagnosis, offers, patients, red_flags, repository as repo
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
    care_path: CarePath | None = None                  # last decided path: booking is only allowed on bookable paths
    offered_slot_ids: set[int] = field(default_factory=set)   # patient can only confirm a slot we offered
    last_ask: list[str] = field(default_factory=list)  # fields the previous turn asked about
    # Reception / handoff state
    stage: str = "reception"                           # reception → triage → confirm → recommend
    reception: dict = field(default_factory=dict)      # has_file / identifiers / dob: transient, cleared after check
    patient_id: int | None = None                      # set only after identity verification
    patient_name: str | None = None                    # "Mr. Ahmed" for greetings
    is_guest: bool = False
    verify_attempts: int = 0
    confirmed: bool = False                            # patient confirmed the clinic assistant's summary


@dataclass
class TurnResult:
    reply: str
    language: str
    triage: TriageResult | None
    providers: list[dict] = field(default_factory=list)
    hospitals: list[dict] = field(default_factory=list)
    slots: list[dict] = field(default_factory=list)
    offers: list[dict] = field(default_factory=list)
    agent: str = "reception"                           # which assistant is speaking (UI shows the handoff)
    clinic: str | None = None
    trace: list[dict] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)   # what the output checks removed (for transparency)


# ── Fixed texts (reviewed once; used when the LLM is unavailable or its reply is rejected) ──
QUESTIONS = {
    "has_file": {"en": "Before we continue, do you already have a patient file with us? If so, please share your file number (e.g. MRN-100001) or your national ID.",
                 "ar": "قبل أن نكمل، هل لديك ملف طبي لدينا؟ إذا نعم، أرسل رقم الملف (مثل MRN-100001) أو رقم الهوية."},
    "date_of_birth": {"en": "Thank you. To verify it's you, what is your date of birth?",
                      "ar": "شكرًا لك. للتحقق من هويتك، ما هو تاريخ ميلادك؟"},
    "reverify": {"en": "I couldn't verify these details. Please check your file number or national ID and date of birth, or tell me if you'd like to continue without a file.",
                 "ar": "لم أتمكن من التحقق من هذه البيانات. يُرجى التأكد من رقم الملف أو الهوية وتاريخ الميلاد، أو أخبرني إذا كنت تريد المتابعة بدون ملف."},
    "chief_complaint": {"en": "What is the main health concern you'd like help with?",
                        "ar": "ما هي المشكلة الصحية الأساسية التي تريد المساعدة بها؟"},
    "pain_now": {"en": "Is the chest pain happening right now?", "ar": "هل ألم الصدر موجود الآن؟"},
    "associated": {"en": "Do you have any of these: shortness of breath, fainting or dizziness, sweating or nausea, "
                         "or pain spreading to your arm, jaw, back or shoulder?",
                   "ar": "هل لديك أيٌّ من التالي: ضيق في التنفس، إغماء أو دوخة، تعرّق أو غثيان، "
                         "أو ألم ينتشر إلى الذراع أو الفك أو الظهر أو الكتف؟"},
    "onset": {"en": "When did the chest pain first start: today, in the last few days, or weeks ago or longer?",
              "ar": "متى بدأ ألم الصدر أول مرة: اليوم، خلال الأيام الماضية، أم قبل أسابيع أو أكثر؟"},
    "review_specialty": {"en": "Which kind of doctor gave you the diagnosis, for example a cardiologist, a heart surgeon, an orthopedic surgeon or a neurologist?",
                         "ar": "ما تخصص الطبيب الذي شخّص حالتك، مثل طبيب قلب أو جراح قلب أو جراح عظام أو طبيب أعصاب؟"},
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
RECEPTION_TEXT = {
    "found": {"en": "Thank you, {name}. I found your file.", "ar": "شكرًا {name}، وجدت ملفك."},
    "guest": {"en": "No problem, we'll continue without a file.", "ar": "لا مشكلة، سنكمل بدون ملف."},
    "give_up": {"en": "I couldn't verify your file, so we'll continue without it for now. Our team can link it later.",
                "ar": "لم أتمكن من التحقق من ملفك، لذا سنكمل بدونه الآن ويمكن لفريقنا ربطه لاحقًا."},
}
HANDOFF_TEXT = {
    "en": "Thank you. I'm transferring you to the {clinic} clinic assistant.\n\nHello{name}, I'm the {clinic} clinic assistant. {file}{facts} Is this correct?",
    "ar": "شكرًا لك. سأحوّلك الآن إلى مساعد عيادة {clinic}.\n\nأهلًا{name}، أنا مساعد عيادة {clinic}. {file}{facts} هل هذه المعلومات صحيحة؟",
}
CORRECTION_TEXT = {"en": "Thanks for telling me. What should I correct?", "ar": "شكرًا لتوضيحك. ما الذي يجب تصحيحه؟"}
OFFERS_NOTE = {"en": " Optional services that fit this visit are shown below.",
               "ar": " تظهر بالأسفل خدمات اختيارية تناسب هذه الزيارة."}
OPTIONS_TEXT = {"en": " These are matching options from the HealTrip provider network:",
                "ar": " هذه خيارات مطابقة من شبكة مقدمي الخدمة في HealTrip:"}
NO_RESULTS = {"en": "I couldn't find a matching provider in the current HealTrip provider database.",
              "ar": "لم أجد مقدم خدمة مطابقًا في قاعدة بيانات HealTrip الحالية."}
DB_DOWN = {"en": "I'm unable to access the provider database right now. Please try again later.",
           "ar": "لا أستطيع الوصول إلى قاعدة بيانات مقدمي الخدمة الآن. يُرجى المحاولة لاحقًا."}
AI_DOWN = {"en": "The AI service is temporarily unavailable. Please try again. If you feel unwell now, call 997.",
           "ar": "خدمة الذكاء الاصطناعي غير متاحة مؤقتًا. يُرجى المحاولة مرة أخرى. إذا كنت تشعر بتوعك الآن، اتصل بالرقم 997."}


SLOTS_TEXT = {"en": "Here are the next open times. Tap Confirm on the one you want, and I'll give you your ticket number.",
              "ar": "هذه أقرب المواعيد المتاحة. اضغط «تأكيد» على الموعد الذي يناسبك وسأعطيك رقم التذكرة."}
BOOKABLE = {CarePath.specialist, CarePath.second_opinion, CarePath.routine}


def _claims_booked(text: str) -> bool:
    """The model must never announce a booking: only the confirm endpoint books."""
    return bool(re.search(r"\b(booked|confirmed|reserved|ticket)\b|(تم (ال)?حجز|تم تاكيد|رقم (ال)?تذكره)", red_flags.normalize(text)))


def detect_language(message: str, hint: str | None) -> str:
    return "ar" if re.search(r"[؀-ۿ]", message) else (hint or "en")


# ── Step 2: fact extraction (the LLM's main job) ──
EXTRACT_TOOL = {"type": "function", "function": {
    "name": "record_facts",
    "description": "Record facts the patient has CLEARLY stated. Leave a field out if not stated. Never guess.",
    "parameters": {"type": "object", "properties": {
        "chief_complaint": {"type": "string", "enum": ["chest_pain", "other"],
                            "description": "chest_pain if the patient has chest pain/pressure/tightness; "
                                           "other for ANY other concern, including wanting a second opinion on an existing diagnosis"},
        "pain_now": {"type": "boolean", "description": "Is the chest pain happening right now?"},
        "onset": {"type": "string", "enum": ["today", "days", "weeks_or_more"]},
        "shortness_of_breath": {"type": "boolean"},
        "fainting_or_dizziness": {"type": "boolean"},
        "sweating_or_nausea": {"type": "boolean"},
        "pain_spreads": {"type": "boolean", "description": "Pain spreads to arm, jaw, back or shoulder"},
        "has_diagnosis_to_review": {"type": "boolean", "description": "Has an existing diagnosis/treatment plan to be reviewed"},
        "review_specialty": {"type": "string", "enum": ["cardiology", "cardiac_surgery", "internal_medicine", "pulmonology",
                                                         "gastroenterology", "orthopedics", "neurology"],
                             "description": "Specialty of the doctor who made the existing diagnosis (heart/valve → cardiology)"},
        "has_file": {"type": "boolean", "description": "Patient says they have (true) or don't have (false) a patient file"},
        "file_number": {"type": "string", "description": "Patient file number, format MRN-123456"},
        "national_id": {"type": "string", "description": "10-digit national ID or iqama number"},
        "date_of_birth": {"type": "string", "description": "Date of birth as YYYY-MM-DD"},
        "summary_confirmed": {"type": "boolean", "description": "ONLY if the assistant's last message asked the patient to confirm a "
                                                                "summary: true if they confirmed, false if they said something is wrong"},
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
    for k in ("has_file", "file_number", "national_id", "summary_confirmed"):
        if args.get(k) is not None:
            s.reception[k] = args[k]
    if args.get("date_of_birth"):
        try:
            s.reception["date_of_birth"] = date.fromisoformat(str(args["date_of_birth"])[:10])
        except ValueError:
            pass


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


def extract_focused(llm: LLM, s: ChatSession, fields: list[str], question: str, answer: str) -> None:
    """Retry for ONE question only. Found in live runs: the full extraction occasionally leaves out
    a field the patient clearly answered (e.g. "لا ما عندي تشخيص"), and the same question gets
    asked again. A narrow call (just the question, the answer and the asked fields) is cheap and
    much harder to get wrong."""
    props = {k: v for k, v in EXTRACT_TOOL["function"]["parameters"]["properties"].items() if k in fields}
    tool = {"type": "function", "function": {"name": "record_facts", "description": "Record the patient's answer.",
                                             "parameters": {"type": "object", "properties": props}}}
    msgs = [{"role": "system", "content": "The assistant asked the patient a question and the patient answered. "
                                          "Record the answer for the listed fields only. A plain 'no'/'لا'/'none' "
                                          "to a grouped question means false for every field. Do not guess."},
            {"role": "user", "content": f"Question: {question}\nAnswer: {answer}"}]
    out = llm.chat(msgs, tools=[tool], tool_choice={"type": "function", "function": {"name": "record_facts"}})
    for call in out.get("tool_calls") or []:
        try:
            _merge_facts(s, {k: v for k, v in json.loads(call["function"]["arguments"] or "{}").items() if k in fields})
        except Exception:
            log.warning("focused_extraction_rejected session=%s", s.id)


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

    # 3. Decide (code). If the rules ask the SAME question again, the answer was probably missed:
    #    one focused extraction for just that question, then decide again.
    result = decide(s.facts)
    if result.care_path is CarePath.need_more_info and result.ask_next == s.last_ask and len(s.history) >= 2:
        try:
            extract_focused(llm, s, result.ask_next, s.history[-2]["content"], message)
            result = decide(s.facts)
            trace.append({"step": "extract_focused_retry", "fields": s.last_ask, "facts": s.facts.model_dump(exclude_none=True)})
        except LLMUnavailable:
            pass
    s.last_ask = []  # set again only if this turn actually asks a question (see _ask)
    trace.append({"step": "triage", "care_path": result.care_path.value, "rule": result.rule_id, "ask_next": result.ask_next})

    # 4a. Emergency / urgent.
    if result.care_path in (CarePath.emergency, CarePath.urgent):
        s.emergency_locked = result.care_path is CarePath.emergency
        return _finish(s, _safety_turn(db, s, lang, result, trace))

    # Reception: find the patient's file. Safety first: while we don't know whether chest pain is
    # happening RIGHT NOW, we ask that before asking anyone to look for a file number.
    prefix = ""
    if s.stage == "reception" and result.rule_id != "Q_PAIN_NOW":
        key, prefix = _reception(db, s, lang, trace)
        if key:
            return _finish(s, _ask(llm, s, lang, key, result, trace, agent="reception"))

    # 4b. Ask what the rules need.
    if result.care_path is CarePath.need_more_info:
        key = "associated" if len(result.ask_next) > 1 else result.ask_next[0]
        return _finish(s, _ask(llm, s, lang, key, result, trace, prefix=prefix))

    # 4c. Path decided → hand off to the clinic assistant, who confirms the summary before anything is offered.
    if not s.confirmed:
        answer = s.reception.pop("summary_confirmed", None) if s.stage == "confirm" else None
        if answer is True:
            s.confirmed, s.stage = True, "recommend"
            trace.append({"step": "summary_confirmed"})
        elif answer is False:
            trace.append({"step": "summary_rejected"})
            return _finish(s, TurnResult(reply=CORRECTION_TEXT[lang], language=lang, triage=result,
                                         agent="clinic_assistant", clinic=_clinic(db, result, lang), trace=trace))
        else:
            s.stage = "confirm"
            return _finish(s, _handoff(db, s, lang, result, trace))
    return _finish(s, _recommend(db, llm, s, lang, result, trace))


def _reception(db, s, lang, trace) -> tuple[str | None, str]:
    """Returns (question key to ask, or None when reception is done; text to prefix the next reply)."""
    rec = s.reception
    if rec.get("has_file") is False and not (rec.get("file_number") or rec.get("national_id")):
        s.is_guest, s.stage = True, "triage"
        trace.append({"step": "reception", "result": "guest"})
        return None, RECEPTION_TEXT["guest"][lang] + " "
    ident = rec.get("file_number") or rec.get("national_id")
    if ident and rec.get("date_of_birth"):
        p = patients.verify(db, str(ident), rec["date_of_birth"])
        for k in ("file_number", "national_id", "date_of_birth"):
            rec.pop(k, None)  # identifiers are not kept once checked
        if p:
            s.patient_id, s.patient_name, s.stage = p.id, patients.greeting_name(p, lang), "triage"
            trace.append({"step": "reception", "result": "verified", "patient_id": p.id})
            return None, RECEPTION_TEXT["found"][lang].format(name=s.patient_name) + " "
        s.verify_attempts += 1
        trace.append({"step": "reception", "result": "not_verified", "attempt": s.verify_attempts})
        if s.verify_attempts >= patients.MAX_VERIFY_ATTEMPTS:
            s.is_guest, s.stage = True, "triage"
            return None, RECEPTION_TEXT["give_up"][lang] + " "
        return "reverify", ""
    if ident:
        return "date_of_birth", ""
    return "has_file", ""


def _clinic(db, result: TriageResult, lang: str) -> str | None:
    from app.models import Specialty
    sp = db.get(Specialty, result.specialty) if result.specialty else None
    return (sp.name_ar if lang == "ar" else sp.name_en) if sp else None


def _handoff(db, s, lang, result, trace) -> TurnResult:
    """Fixed template filled from the DB file + structured facts. No LLM text: a medical record
    must never be paraphrased by a model."""
    from app.models import Patient
    p = db.get(Patient, s.patient_id) if s.patient_id else None
    clinic = _clinic(db, result, lang) or ""
    text = HANDOFF_TEXT[lang].format(
        clinic=clinic, name=(" " + s.patient_name) if s.patient_name else "",
        file=(patients.file_summary(db, p, lang) + " ") if p else "",
        facts=patients.facts_summary(s.facts, lang))
    trace.append({"step": "handoff", "clinic": result.specialty, "patient_file": bool(p)})
    return TurnResult(reply=text, language=lang, triage=result, agent="clinic_assistant", clinic=clinic, trace=trace)


def _ask(llm, s, lang, key, result, trace, agent="reception", prefix="") -> TurnResult:
    # Remember what was ACTUALLY asked: a reception question must not look like a missed triage answer.
    s.last_ask = result.ask_next if key not in ("has_file", "date_of_birth", "reverify") else [key]
    template = QUESTIONS[key][lang]
    prompt = (f"You are HealTrip's care-navigation assistant. Reply in {'Arabic' if lang == 'ar' else 'English'}. "
              + ("Do NOT thank or acknowledge (that was already done); " if prefix else "Briefly acknowledge the patient, then ")
              + f"ask exactly this question in your own words: \"{template}\". "
              "Maximum 2 sentences. Do not diagnose, do not name conditions, do not give medical advice.")
    try:
        # Live finding: told "do not thank", the model still added "Thanks, that's verified" after our
        # fixed "I found your file." A prompt isn't a guarantee, so after a fixed prefix we don't ask the model.
        text = template if prefix else (llm.chat([{"role": "system", "content": prompt}, *s.history[-HISTORY_WINDOW:]]).get("content") or template)
    except LLMUnavailable:
        text = template  # the question is fixed text anyway: degrade to it
    text, blocked = no_diagnosis.enforce(text, lang)
    if blocked:
        text = no_diagnosis.SAFE_REPLY[lang] + " " + template
    trace.append({"step": "ask", "question_for": [key], "blocked": blocked})
    return TurnResult(reply=prefix + text, language=lang, triage=result, trace=trace, blocked=blocked, agent=agent)


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
        "provider cards from the database. If the tools return no results, say so plainly.\n"
        "If the patient wants to book or asks about times for a doctor, call get_available_slots and tell them to "
        "tap Confirm on the time they want. Never list times yourself and never say an appointment is booked: "
        "only the patient's confirmation books it."
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
    elif gw.slots:
        text, blocked = no_diagnosis.enforce(text, lang)
        if blocked or not text.strip() or not grounded(text, gw, s) or _claims_booked(text):
            blocked = blocked or ["unsafe_booking_text"]
            text = SLOTS_TEXT[lang]
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
    s.offered_slot_ids |= set(gw.slots)
    offer_cards = []
    if gw.doctors and not gw.db_failed and not gw.slots:
        try:
            offer_cards = offers.select_offers(db, result, {c["country"] for c in gw.doctors.values()})
        except SQLAlchemyError:
            offer_cards = []  # offers are optional; never block the clinical answer
        if offer_cards:
            text += OFFERS_NOTE[lang]
    trace.append({"step": "recommend", "providers": list(gw.doctors), "blocked": blocked})
    return TurnResult(reply=text, language=lang, triage=result, providers=list(gw.doctors.values()),
                      hospitals=list(gw.hospitals.values()), slots=list(gw.slots.values()), offers=offer_cards,
                      agent="clinic_assistant", clinic=_clinic(db, result, lang), trace=trace, blocked=blocked)


def _finish(s: ChatSession, r: TurnResult) -> TurnResult:
    if r.triage and r.triage.care_path is not CarePath.need_more_info:
        s.care_path = r.triage.care_path
    s.history.append({"role": "assistant", "content": r.reply})
    # Log the decision path, never the patient's words.
    log.info("turn session=%s path=%s rule=%s providers=%s blocked=%s", s.id[:8],
             r.triage.care_path.value if r.triage else None, r.triage.rule_id if r.triage else None,
             [p["doctor_id"] for p in r.providers], r.blocked)
    return r


# ── Persistence (serverless: any instance must be able to continue any conversation) ──
def session_to_dict(s: ChatSession) -> dict:
    rec = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in s.reception.items()}
    return {"id": s.id, "facts": s.facts.model_dump(mode="json"), "prefs": s.prefs, "history": s.history[-40:],
            "known_doctor_ids": sorted(s.known_doctor_ids), "emergency_locked": s.emergency_locked,
            "care_path": s.care_path.value if s.care_path else None, "offered_slot_ids": sorted(s.offered_slot_ids),
            "last_ask": s.last_ask, "stage": s.stage, "reception": rec, "patient_id": s.patient_id,
            "patient_name": s.patient_name, "is_guest": s.is_guest, "verify_attempts": s.verify_attempts,
            "confirmed": s.confirmed}


def session_from_dict(d: dict) -> ChatSession:
    rec = dict(d.get("reception") or {})
    if rec.get("date_of_birth"):
        rec["date_of_birth"] = date.fromisoformat(rec["date_of_birth"])
    return ChatSession(id=d["id"], facts=PatientFacts.model_validate(d["facts"]), prefs=d["prefs"], history=d["history"],
                       known_doctor_ids=set(d["known_doctor_ids"]), emergency_locked=d["emergency_locked"],
                       care_path=CarePath(d["care_path"]) if d["care_path"] else None,
                       offered_slot_ids=set(d["offered_slot_ids"]), last_ask=d["last_ask"], stage=d["stage"],
                       reception=rec, patient_id=d["patient_id"], patient_name=d["patient_name"],
                       is_guest=d["is_guest"], verify_attempts=d["verify_attempts"], confirmed=d["confirmed"])
