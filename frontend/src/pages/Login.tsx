import { useState } from "react"
import { useAuthStore } from "@/store/auth"

export default function Login() {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const login = useAuthStore(s => s.login)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(null)
    setLoading(true)
    try {
      await login(email, password)
      location.href = "/dashboard"
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Credenciales inválidas")
    } finally { setLoading(false) }
  }

  return (
    <div className="min-h-screen grid place-items-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm bg-white rounded-xl p-6 shadow">
        <h1 className="text-xl font-semibold mb-4">CRM 2025 — Login</h1>
        <label className="text-sm">Email</label>
        <input className="w-full h-10 border rounded px-3 mb-3" value={email} onChange={e=>setEmail(e.target.value)} />
        <label className="text-sm">Contraseña</label>
        <input className="w-full h-10 border rounded px-3 mb-3" type="password" value={password} onChange={e=>setPassword(e.target.value)} />
        {err && <p className="text-sm text-red-600 mb-2">{err}</p>}
        <button className="w-full h-10 rounded bg-black text-white" disabled={loading}>
          {loading ? "Ingresando..." : "Entrar"}
        </button>
      </form>
    </div>
  )
}
