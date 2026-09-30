// Server-side proxy: the browser only talks to this Next.js server. We add the secret proxy key
// (the backend refuses /api/* without it) and pass the client's real IP for rate limiting.
// The backend URL, the key, the DB and the AI key never reach the browser.
export const runtime = "nodejs"
export const maxDuration = 60

const BACKEND = process.env.BACKEND_URL || "http://localhost:8000"
const KEY = process.env.PROXY_KEY || ""

async function forward(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params
  if (path[0] !== "v1" || path.includes("admin")) {
    return Response.json({ error: { code: "not_found", message: "Not found" } }, { status: 404 })
  }
  const url = new URL(req.url)
  const headers: Record<string, string> = { "content-type": req.headers.get("content-type") || "application/json" }
  if (KEY) headers["x-proxy-key"] = KEY
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0].trim() || req.headers.get("x-real-ip") || ""
  if (ip) headers["x-forwarded-for"] = ip
  try {
    const res = await fetch(`${BACKEND}/api/${path.join("/")}${url.search}`, {
      method: req.method, headers, body: req.method === "GET" ? undefined : await req.text(), cache: "no-store",
    })
    return new Response(await res.text(), { status: res.status, headers: { "content-type": res.headers.get("content-type") || "application/json" } })
  } catch {
    return Response.json({ error: { code: "unavailable", message: "The service is temporarily unavailable. If you feel unwell now, call 997." } }, { status: 503 })
  }
}

export { forward as GET, forward as POST }
