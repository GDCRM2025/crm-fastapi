import { useEffect, useState } from "react"
import { api } from "@/lib/api"

type UserRow = { id: number; email: string; full_name?: string | null; role?: string | null }

export default function UsersPage() {
  const [users, setUsers] = useState<UserRow[]>([])
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [role, setRole] = useState("ventas")

  async function load() {
    const { data } = await api.get("/users")
    setUsers(data)
  }

  async function create() {
    if (!email || !password) return
    await api.post("/users", { email, password, role })
    setEmail(""); setPassword("")
    await load()
  }

  async function del(id: number) {
    await api.delete(`/users/${id}`)
    await load()
  }

  useEffect(() => { load() }, [])

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Usuarios</h1>

      <div className="bg-white rounded-xl p-4 shadow flex gap-2 items-end">
        <div className="flex-1">
          <label className="text-sm">Email</label>
          <input className="w-full h-10 border rounded px-3" value={email} onChange={e=>setEmail(e.target.value)} />
        </div>
        <div className="flex-1">
          <label className="text-sm">Contraseña</label>
          <input className="w-full h-10 border rounded px-3" type="password" value={password} onChange={e=>setPassword(e.target.value)} />
        </div>
        <div>
          <label className="text-sm">Rol</label>
          <select className="h-10 border rounded px-3" value={role} onChange={e=>setRole(e.target.value)}>
            <option value="admin">Admin</option>
            <option value="ventas">Ejecutivo de Ventas</option>
            <option value="operaciones">Jefe de Operaciones</option>
            <option value="bodega">Jefe de Bodega</option>
            <option value="patio">Asistente de Patio</option>
          </select>
        </div>
        <button className="h-10 px-4 rounded bg-black text-white" onClick={create}>Crear</button>
      </div>

      <div className="bg-white rounded-xl p-4 shadow">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b">
              <th className="py-2">ID</th>
              <th className="py-2">Email</th>
              <th className="py-2">Nombre</th>
              <th className="py-2">Rol</th>
              <th className="py-2">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {users.map(u=>(
              <tr key={u.id} className="border-b">
                <td className="py-2">{u.id}</td>
                <td className="py-2">{u.email}</td>
                <td className="py-2">{u.full_name ?? "—"}</td>
                <td className="py-2">{u.role ?? "—"}</td>
                <td className="py-2">
                  <button className="h-8 px-3 rounded bg-red-600 text-white" onClick={()=>del(u.id)}>Eliminar</button>
                </td>
              </tr>
            ))}
            {users.length===0 && <tr><td className="py-4 text-gray-500" colSpan={5}>Sin usuarios</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
