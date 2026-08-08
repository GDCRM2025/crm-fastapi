import { useEffect } from "react"
import { useAuthStore } from "@/store/auth"

export default function Dashboard() {
  const user = useAuthStore(s => s.user)
  const fetchMe = useAuthStore(s => s.fetchMe)
  const logout = useAuthStore(s => s.logout)

  useEffect(() => {
    if (!user) fetchMe()
  }, [user, fetchMe])

  return (
    <div className="p-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-semibold">Panel</h1>
        <div className="flex gap-2">
          {user?.role === "admin" && <a className="underline" href="/usuarios">Gestionar Usuarios</a>}
          <button className="h-10 px-4 rounded bg-gray-200" onClick={()=>location.href="/dashboard"}>Dashboard</button>
          <button className="h-10 px-4 rounded bg-black text-white" onClick={()=>{logout(); location.href="/login"}}>Salir</button>
        </div>
      </div>
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div className="p-4 rounded-xl bg-white shadow">Bienvenido, <b>{user?.email}</b></div>
        <div className="p-4 rounded-xl bg-white shadow">Tu rol: <b>{user?.role ?? "—"}</b></div>
        <div className="p-4 rounded-xl bg-white shadow">KPIs (próx.)</div>
      </div>
    </div>
  )
}
