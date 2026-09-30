"""The assistant routes patients to the right doctor. It never diagnoses.

Applied to every LLM reply before it reaches the patient (M3). A prompt instruction alone is not
enough: models drift into "this sounds like angina" when they are trying to be helpful. So the
rule is enforced in code: a reply that names a condition, guesses a cause or suggests treatment
is blocked and replaced with a fixed message that directs the patient to a doctor.

Deliberately simple (a phrase list). It over-blocks rather than under-blocks; a blocked reply
only costs a slightly less fluent sentence.
"""
import re

from app.red_flags import normalize

_PATTERNS = (
    # Naming a condition
    r"\b(angina|heart attack|myocardial|infarction|acs|pericarditis|costochondritis|reflux|gerd|"
    r"pulmonary embolism|pneumonia|anxiety attack|panic attack|muscle strain|dissection)\b",
    r"(ذبحه|جلطه|احتشاء|ارتجاع|التهاب (ال)?(غضروف|تامور|رئه)|نوبه (قلبيه|هلع)|شد عضلي|قولون)",
    # Guessing the cause / diagnosing language
    r"\b(you (probably|likely|might|may) have|it (is|'s) (probably|likely)|sounds like|consistent with|"
    r"i (think|suspect|believe) (it|you|this)|diagnos(is|e|ed))\b",
    r"(غالبا|على الارجح|يبدو ان(ه|ها)?|اعتقد ان(ه|ها|ك)?|تشخيص|عندك (مرض|حاله))",
    # Treatment / medication advice
    r"\b(take|try) (an? )?(aspirin|ibuprofen|paracetamol|nitroglycerin|antacid|painkiller|medication)\b",
    r"(خذ|تناول|استخدم) (حبه|حبوب|دواء|اسبرين|بنادول|مسكن)",
)
_COMPILED = [re.compile(p) for p in _PATTERNS]

SAFE_REPLY = {
    "en": ("I can't tell you what is causing your symptoms. Only a doctor can assess that. "
           "What I can do is help you reach the right kind of doctor for your situation."),
    "ar": ("لا أستطيع أن أحدد سبب الأعراض، فهذا يحتاج إلى تقييم الطبيب. "
           "ما أستطيع فعله هو مساعدتك في الوصول إلى الطبيب المناسب لحالتك."),
}


def violations(reply: str) -> list[str]:
    text = normalize(reply)
    return [m.group(0) for p in _COMPILED if (m := p.search(text))]


def enforce(reply: str, lang: str = "en") -> tuple[str, list[str]]:
    """Return (text safe to show the patient, what was blocked, for logs/tests)."""
    found = violations(reply)
    return (SAFE_REPLY[lang], found) if found else (reply, [])
