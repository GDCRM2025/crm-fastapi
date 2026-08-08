import { ReactNode, useEffect } from "react"
import { useAuthStore, Role } from "@/store/auth"

function Redirect({ to }: { to: string }) {
  useEffect(() => {
    window.location.replace(to)
  }, [to])
  return null
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuthStore(s => s.token)
  if (!token) return <Redirect to="/login" />
  return <>{children}</>
}

export function RequireRole({ roles, children }: { roles: Role[], children: ReactNode }) {
  const user = useAuthStore(s => s.user)
  if (!user) return null
  if (!user.role || !roles.includes(user.role)) return <Redirect to="/dashboard" />
  return <>{children}</>
}
