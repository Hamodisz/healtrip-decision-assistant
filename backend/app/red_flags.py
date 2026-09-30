"""Layer 1 of safety: a red-flag phrase check on the patient's RAW message.

It runs before the LLM is called, so it still protects the patient when the AI service is down
or when the model misreads the message. It only catches unambiguous, present-tense emergency
phrases; everything subtler is handled by the triage rules on structured facts (triage.py).

PROTOTYPE RULES written by an engineer, not a clinician. Deliberately conservative:
a false alarm costs a patient a trip to the ER; a miss can cost a life.
"""
import re
from dataclasses import dataclass

# Arabic letters vary in spelling (أ/إ/آ/ا, ة/ه, ى/ي) and may carry diacritics.
_AR_DIACRITICS = re.compile(r"[ً-ْـ]")
_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"})


def normalize(text: str) -> str:
    text = _AR_DIACRITICS.sub("", text.lower()).translate(_AR_MAP)
    return re.sub(r"\s+", " ", text)


@dataclass(frozen=True)
class RedFlag:
    id: str
    description: str
    patterns: tuple[str, ...]  # regexes over normalized text


RED_FLAGS: tuple[RedFlag, ...] = (
    RedFlag(
        "RF_BREATHING",
        "Severe difficulty breathing",
        (
            r"\b(can'?t|cannot|can not|unable to) breathe\b",
            r"\bstruggling to breathe\b",
            r"(ما|مو|مش|لا) (اقدر|قادر|قادره|استطيع) (اتنفس|اتنفّس|التنفس)",
            r"ضيق (نفس|تنفس) (شديد|قوي)",
            r"ضيق (شديد|قوي) (في ال|بال)تنفس",
        ),
    ),
    RedFlag(
        "RF_FAINTING",
        "Fainting or loss of consciousness",
        (
            r"\b(passed out|fainted|blacked out|lost consciousness)\b",
            r"(اغمي|انغمي) علي",
            r"(فقدت|فقد) (ال)?وعي",
        ),
    ),
    RedFlag(
        "RF_CRUSHING_CHEST",
        "Crushing or severe chest pressure now",
        (
            r"\bcrushing (chest )?pain\b",
            r"\b(severe|worst) chest pain\b",
            r"\bchest (feels )?like (an elephant|something heavy)\b",
            r"الم (شديد|قوي) (في ال|في |بال|ب)صدر",
            r"(ضغط|عصر) (شديد|قوي) (علي|في) (ال)?صدر",
        ),
    ),
    RedFlag(
        "RF_STROKE_SIGNS",
        "Sudden weakness, face droop or speech problems",
        (
            r"\b(face (is )?drooping|slurred speech|can'?t (move|feel) (my )?(arm|leg))\b",
            r"(ثقل|تلعثم) (في )?(ال)?(كلام|لسان)",
            r"(ما|مو|لا) (اقدر|قادر) (احرك|اتحكم (في|ب)) (يدي|رجلي|ايدي)",
        ),
    ),
)


def check(message: str) -> list[RedFlag]:
    """Return every red flag whose pattern appears in the message."""
    text = normalize(message)
    return [f for f in RED_FLAGS if any(re.search(p, text) for p in f.patterns)]
