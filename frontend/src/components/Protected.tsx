import { ReactNode } from "react"
import { Navigate } from "react-router-dom"
import { useAuthStore, Role } from "@/store/auth"

export function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuthStore(s => s.token)
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

export function RequireRole({ roles, children }: { roles: Role[], children: ReactNode }) {
  const user = useAuthStore(s => s.user)
  if (!user) return null
  if (!user.role || !roles.includes(user.role)) return <Navigate to="/dashboard" replace />
  return <>{children}</>
}
