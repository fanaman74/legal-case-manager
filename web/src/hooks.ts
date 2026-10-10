import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { Case, User } from "./api";

/** Loads data and reloads it on demand. Keeps the last good data while reloading. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const run = useCallback(async () => {
    const n = ++seq.current;
    setLoading(true);
    try {
      const d = await load();
      if (n === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (n === seq.current) setError((e as Error).message);
    } finally {
      if (n === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    void run();
  }, [run]);
  return { data, error, loading, reload: run, setData };
}

export type AppState = {
  user: User;
  cases: Case[];
  reloadCases: () => Promise<void>;
  signOut: () => void;
  toast: (message: string) => void;
};

export const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp outside the app");
  return ctx;
}

/** Updates the page title so browser tabs and history read well. */
export function useTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} · Case File Manager` : "Case File Manager";
  }, [title]);
}
