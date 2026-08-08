import axios from "axios"
import { useAuthStore } from "../store/auth"

export const api = axios.create({
  baseURL: "http://127.0.0.1:8000",
})

api.interceptors.request.use(cfg => {
  const token = useAuthStore.getState().token
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})
