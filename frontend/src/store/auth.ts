import { create } from "zustand"
import { api } from "@/lib/api"

export type Role =
  | "admin"
  | "ventas"
  | "operaciones"
  | "bodega"
  | "patio"

export type User = {
  id: number
  email: string
  full_name?: string | null
  role?: Role | null
}

type State = {
  token: string | null
  user: User | null
  login: (email: string, password: string) => Promise<void>
  fetchMe: () => Promise<void>
  logout: () => void
}

export const useAuthStore = create<State>((set, get) => ({
  token: null,
  user: null,

  login: async (email, password) => {
    const body = new URLSearchParams()
    body.set("username", email)
    body.set("password", password)

    const { data } = await api.post("/auth/token", body, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    })
    set({ token: data.access_token })
    await get().fetchMe()
  },

  fetchMe: async () => {
    const { data } = await api.get("/auth/me")
    set({ user: data })
  },

  logout: () => set({ token: null, user: null }),
}))
