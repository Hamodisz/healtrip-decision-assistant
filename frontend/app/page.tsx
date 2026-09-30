"use client"
// Renders ONLY what the backend returns. Doctor, hospital, offer, slot and ticket data come from
// DB rows in the API response, never from the model's text.
import { useEffect, useRef, useState } from "react"
import { Lang, T } from "./i18n"

type Doctor = { doctor_id: string; doctor_name: string; doctor_name_ar: string; specialty: string; specialty_ar: string
  hospital_name: string; hospital_name_ar: string; city: string; country: string; languages: string[]
  years_experience: number; offers_second_opinion: boolean; offers_remote_consult: boolean; consultation_fee: number; currency: string }
type Hospital = { hospital_id: string; hospital_name: string; hospital_name_ar: string; city: string; address: string }
type Slot = { slot_id: number; doctor_id: string; local_time: string; mode: string }
type Offer = { offer_id: string; kind: string; title_en: string; title_ar: string; description_en: string; description_ar: string; price: number; currency: string }
type Turn = { reply: string; agent: string; clinic: string | null; triage: { care_path: string; rule_id: string } | null
  providers: Doctor[]; hospitals: Hospital[]; slots: Slot[]; offers: Offer[]; trace: object[] }
type Msg = { role: "user" | "assistant"; text: string; turn?: Turn; ticket?: string }

export default function Page() {
  const [lang, setLang] = useState<Lang>("en")
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [booking, setBooking] = useState<number | null>(null)
  const [booked, setBooked] = useState<Set<number>>(new Set())
  const [error, setError] = useState("")
  const bottom = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const t = T[lang]
  const ar = lang === "ar"

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }) }, [msgs, loading])
  useEffect(() => { if (!loading) inputRef.current?.focus() }, [loading])

  async function send(text: string) {
    if (!text.trim() || loading) return
    setMsgs(m => [...m, { role: "user", text }])
    setInput(""); setLoading(true); setError("")
    try {
      const res = await fetch("/api/v1/chat", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text, language: lang }) })
      const data = await res.json()
      if (!res.ok) { setError(data?.error?.message || t.netError); return }
      setSessionId(data.session_id)
      setMsgs(m => [...m, { role: "assistant", text: data.reply, turn: data }])
    } catch { setError(t.netError) } finally { setLoading(false) }
  }

  async function confirm(slot: Slot) {
    if (!sessionId || booking) return
    setBooking(slot.slot_id); setError("")
    try {
      const res = await fetch("/api/v1/bookings", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, slot_id: slot.slot_id }) })
      const data = await res.json()
      if (!res.ok) { setError(data?.error?.message || t.netError); return }
      setBooked(b => new Set(b).add(slot.slot_id))
      setMsgs(m => [...m, { role: "assistant", text: data.reply, ticket: data.ticket_number }])
    } catch { setError(t.netError) } finally { setBooking(null) }
  }

  let lastAgent = "reception"
  return (
    <main dir={ar ? "rtl" : "ltr"} lang={lang}>
      <header>
        <div><h1>{t.title}</h1><p className="muted">{t.subtitle}</p></div>
        <button className="ghost" onClick={() => setLang(ar ? "en" : "ar")}>{ar ? "English" : "العربية"}</button>
      </header>

      <section className="chat">
        {msgs.length === 0 && <p className="muted hint" onClick={() => setInput(t.start.replace(/^(Example|مثال): /, ""))}>{t.start}</p>}
        {msgs.map((m, i) => {
          const turn = m.turn
          const handoff = turn && turn.agent !== lastAgent
          if (turn) lastAgent = turn.agent
          const emergency = turn?.triage && ["emergency", "urgent"].includes(turn.triage.care_path)
          return (
            <div key={i}>
              {handoff && <div className="handoff">{t.handoff} {clinicLabel(turn!.clinic, ar)}</div>}
              <div className={`row ${m.role}`}>
                <div className={`bubble ${m.role} ${emergency ? "emergency" : ""}`}>
                  {m.role === "assistant" && turn && <div className="who">{turn.agent === "reception" ? t.reception : clinicLabel(turn.clinic, ar)}</div>}
                  <div className="text">{turn ? withNames(m.text, turn.providers, ar) : m.text}</div>
                  {m.ticket && <div className="ticket"><span>{t.ticket}</span><strong>{m.ticket}</strong></div>}
                </div>
              </div>

              {turn && turn.hospitals.length > 0 && <Cards title={t.er}>{turn.hospitals.map(h =>
                <div className="card er" key={h.hospital_id}><strong>{ar ? h.hospital_name_ar : h.hospital_name}</strong><span>{h.address}</span></div>)}</Cards>}

              {turn && turn.providers.length > 0 && <Cards title={t.doctors}>{turn.providers.map(d =>
                <div className="card" key={d.doctor_id}>
                  <strong>{ar ? d.doctor_name_ar : d.doctor_name}</strong> <code>{d.doctor_id}</code>
                  <span>{ar ? d.specialty_ar : d.specialty} · {ar ? d.hospital_name_ar : d.hospital_name}</span>
                  <span>{d.city}, {d.country} · {d.languages.join(" / ")} · {d.years_experience} {t.years}</span>
                  <span className="tags">{d.offers_second_opinion && <em>{t.secondOpinion}</em>}{d.offers_remote_consult && <em>{t.remote}</em>}</span>
                  <span>{t.fee}: {d.consultation_fee} {d.currency}</span>
                </div>)}</Cards>}

              {turn && turn.slots.length > 0 && <Cards title={t.times}>{turn.slots.map(s =>
                <div className="card slot" key={s.slot_id}>
                  <span>{s.local_time} · {s.mode === "remote" ? t.remote : "🏥"}</span>
                  {booked.has(s.slot_id) ? <span className="booked">✓ {t.booked}</span>
                    : <button disabled={booking !== null} onClick={() => confirm(s)}>{booking === s.slot_id ? t.booking : t.confirm}</button>}
                </div>)}</Cards>}

              {turn && turn.offers.length > 0 && <Cards title={t.offers}>{turn.offers.map(o =>
                <div className="card offer" key={o.offer_id}>
                  <strong>{ar ? o.title_ar : o.title_en}</strong>
                  <span>{ar ? o.description_ar : o.description_en}</span>
                  <span>{o.price} {o.currency}</span>
                </div>)}</Cards>}

              {turn && <details className="trace"><summary>{t.trace}{turn.triage ? ` · ${turn.triage.care_path} (${turn.triage.rule_id})` : ""}</summary>
                <pre dir="ltr">{JSON.stringify(turn.trace, null, 2)}</pre></details>}
            </div>
          )
        })}
        {loading && <div className="row assistant"><div className="bubble assistant muted">{t.thinking}</div></div>}
        <div ref={bottom} />
      </section>

      {error && <p className="error">{error}</p>}
      <form onSubmit={e => { e.preventDefault(); send(input) }}>
        <input value={input} onChange={e => setInput(e.target.value)} placeholder={t.placeholder} maxLength={1000} ref={inputRef} />
        <button type="submit" disabled={loading || !input.trim()}>{t.send}</button>
      </form>
      <p className="muted small">{t.disclaimer}</p>
    </main>
  )
}

// Word order differs by language: "Cardiology clinic assistant" vs "مساعد عيادة أمراض القلب".
function clinicLabel(clinic: string | null, ar: boolean) {
  if (ar) return clinic ? `مساعد عيادة ${clinic}` : "مساعد العيادة"
  return clinic ? `${clinic} clinic assistant` : "Clinic assistant"
}

// The model refers to doctors only by ID ([DOC-001]); the name shown comes from the DB card in the same response.
function withNames(text: string, providers: Doctor[], ar: boolean) {
  return text.replace(/\[?(DOC-\d{3})\]?/g, (_, id) => {
    const d = providers.find(p => p.doctor_id === id)
    return d ? (ar ? d.doctor_name_ar : d.doctor_name) : id
  })
}

function Cards({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="cards"><div className="cards-title">{title}</div><div className="grid">{children}</div></div>
}
