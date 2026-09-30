import pytest

from app.no_diagnosis import SAFE_REPLY, enforce


@pytest.mark.parametrize(
    "reply",
    [
        "This sounds like angina, but you should see a doctor.",
        "It's probably reflux since it happens after meals.",
        "You might have costochondritis.",
        "Take an aspirin while you wait.",
        "يبدو انه ارتجاع في المريء",
        "غالبا عندك ذبحة صدرية",
        "خذ حبة بنادول وارتاح",
    ],
)
def test_diagnosis_or_treatment_is_blocked(reply):
    shown, blocked = enforce(reply, "ar" if any("؀" <= c <= "ۿ" for c in reply) else "en")
    assert blocked and shown in SAFE_REPLY.values()


@pytest.mark.parametrize(
    "reply",
    [
        "Is the pain happening right now?",
        "Based on your answers, a cardiologist is the right doctor to assess this. Here are options from our network.",
        "هل الألم موجود الآن؟",
        "بناءً على إجاباتك، طبيب القلب هو المختص المناسب لتقييم حالتك.",
    ],
)
def test_routing_language_passes_unchanged(reply):
    shown, blocked = enforce(reply)
    assert shown == reply and blocked == []


@pytest.mark.parametrize(
    "reply",
    [
        "This is not an emergency situation, so you don't need the ER right now. See [DOC-001].",
        "Nothing to worry about, a cardiologist can see you next week.",
        "حالتك ليست طارئة ولا داعي للقلق.",
    ],
)
def test_false_reassurance_is_blocked(reply):
    # Found in a live DeepSeek run: the rules chose "specialist", but the model added
    # "you don't need the ER". The system can't promise that; only fixed safety text speaks to urgency.
    assert enforce(reply)[1]


def test_our_own_fixed_questions_are_never_blocked():
    # Found in a live run: the word "diagnosis" in our OWN question was blocked.
    from app.agent import QUESTIONS
    for q in QUESTIONS.values():
        for lang, text in q.items():
            assert enforce(text, lang) == (text, []), text


def test_arabic_not_the_er_reassurance_is_blocked():
    # Live-run regression (DeepSeek, Arabic): "…تقييم من طبيب قلب، وليس الطوارئ"
    assert enforce("الخطوة المناسبة لحالتك هي تقييم من طبيب قلب، وليس الطوارئ", "ar")[1]
