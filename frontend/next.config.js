/** The browser only ever talks to this Next.js server. /api/* is proxied to the backend, so the
 *  backend URL, the database and the AI key are never exposed to the browser. */
module.exports = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.BACKEND_URL || "http://localhost:8000"}/api/:path*` }]
  },
}
