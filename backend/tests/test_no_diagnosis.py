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
