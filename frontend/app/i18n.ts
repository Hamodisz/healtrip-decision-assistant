// All UI text in one place. The chat content itself comes from the backend in the patient's language.
export type Lang = "en" | "ar"

export const T = {
  en: {
    title: "HealTrip Assistant", subtitle: "Demo · mock data · not medical advice",
    placeholder: "Describe what's going on…", send: "Send", thinking: "Thinking…",
    reception: "Reception", clinic: "clinic assistant", handoff: "Transferred to",
    doctors: "Doctors from the HealTrip network", er: "Nearest emergency departments", offers: "Optional services",
    times: "Open times", confirm: "Confirm", booked: "Booked", booking: "Booking…", ticket: "Your ticket",
    years: "yrs experience", remote: "Remote consult", secondOpinion: "Second opinion", fee: "Fee",
    trace: "How this answer was produced", netError: "Can't reach the service. Please try again.",
    start: "Example: I have chest pain and I'm not sure whether I should see a cardiologist, go to the ER, or seek a second opinion.",
    disclaimer: "This assistant does not diagnose. In an emergency call 997.",
  },
  ar: {
    title: "مساعد HealTrip", subtitle: "نسخة تجريبية · بيانات وهمية · ليست نصيحة طبية",
    placeholder: "صف ما تشعر به…", send: "إرسال", thinking: "جارٍ التفكير…",
    reception: "الاستقبال", clinic: "مساعد العيادة", handoff: "تم التحويل إلى",
    doctors: "أطباء من شبكة HealTrip", er: "أقرب أقسام الطوارئ", offers: "خدمات اختيارية",
    times: "المواعيد المتاحة", confirm: "تأكيد", booked: "تم الحجز", booking: "جارٍ الحجز…", ticket: "تذكرتك",
    years: "سنة خبرة", remote: "استشارة عن بُعد", secondOpinion: "رأي ثانٍ", fee: "الرسوم",
    trace: "كيف تم إنتاج هذا الرد", netError: "تعذر الوصول إلى الخدمة. حاول مرة أخرى.",
    start: "مثال: عندي ألم في الصدر ومحتار هل أروح طبيب قلب ولا الطوارئ ولا آخذ رأي ثاني",
    disclaimer: "هذا المساعد لا يشخّص. في حالات الطوارئ اتصل بالرقم 997.",
  },
} as const
