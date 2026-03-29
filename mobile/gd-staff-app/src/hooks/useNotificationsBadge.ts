import { useEffect, useState } from "react";
import { apiAuthedJSON } from "../api/client";

export function useNotificationsBadge(pollMs: number = 15000) {
  const [count, setCount] = useState<number>(0);

  useEffect(() => {
    let alive = true;
    let timer: any = null;

    async function tick() {
      try {
        const j = await apiAuthedJSON<any>("/notifications/summary");
        const c = Number(j?.counts?.system_notifs || 0);
        if (alive) setCount(Number.isFinite(c) ? c : 0);
      } catch {
        // ignore
      } finally {
        if (alive) timer = setTimeout(tick, pollMs);
      }
    }

    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [pollMs]);

  return count;
}

