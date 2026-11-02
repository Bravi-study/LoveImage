import { useCallback, useEffect, useRef, useState } from "react";

import { getShoot, HttpError, type Shoot } from "./api";
import { isBusy } from "./shoots";

const POLL_MS = 2500;
const FAILURES_BEFORE_WARNING = 3;

export function useShoot(id: string, first?: Shoot) {
  const [loaded, setLoaded] = useState<Shoot | null>(first ?? null);
  const [failures, setFailures] = useState(0);
  const [missing, setMissing] = useState<string | null>(null);
  // An answer to an older request must not overwrite a newer state.
  const latest = useRef(0);
  const shoot = loaded?.id === id ? loaded : null;

  const setShoot = useCallback((next: Shoot) => {
    latest.current++;
    setLoaded(next);
    setFailures(0);
  }, []);

  const load = useCallback(async () => {
    const mine = ++latest.current;
    try {
      const next = await getShoot(id);
      if (mine !== latest.current) return;
      setLoaded(next);
      setFailures(0);
    } catch (e) {
      if (mine !== latest.current) return;
      // FastAPI answers 422 to an id that is not a UUID.
      if (e instanceof HttpError && (e.status === 404 || e.status === 422)) setMissing(id);
      else setFailures((n) => n + 1);
    }
  }, [id]);

  const wanted = missing !== id && (shoot === null || isBusy(shoot));

  // Always once: what came through the router's state can be old after a step back in history.
  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!wanted) return;
    const tick = () => {
      if (!document.hidden) void load();
    };
    const timer = setInterval(tick, POLL_MS);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [wanted, load]);

  return { shoot, setShoot, reload: load, missing: missing === id, offline: failures >= FAILURES_BEFORE_WARNING };
}
