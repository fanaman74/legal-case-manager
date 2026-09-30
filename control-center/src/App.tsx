import { useCallback, useEffect, useRef, useState } from "react";
import { api, setCsrf, type Session, type Snapshot } from "./api";
import { ControlCenter } from "./screens/ControlCenter";
import { SetupCode } from "./screens/SetupCode";
import { SignIn } from "./screens/SignIn";

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refreshSession = useCallback(async () => {
    try {
      const s = await api.session();
      setCsrf(s.csrf);
      setSession(s);
      setLoadError(null);
    } catch (e) {
      setLoadError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  const onSession = (s: Session) => {
    setCsrf(s.csrf);
    setSession(s);
  };

  if (loadError) {
    return (
      <main className="auth">
        <div className="auth__card" role="alert">
          <h1 className="auth__title">Can't reach the launcher</h1>
          <p className="auth__lead">{loadError}</p>
          <button className="btn btn--primary btn--md" onClick={() => void refreshSession()}>
            <span>Try again</span>
          </button>
        </div>
      </main>
    );
  }
  if (!session) {
    return <main className="auth" aria-busy="true" />;
  }
  switch (session.state) {
    case "needs_setup":
      return <SetupCode onSession={onSession} />;
    case "signed_out":
      return <SignIn onSession={onSession} />;
    default:
      return <Live session={session} onSession={onSession} onSignedOut={() => void refreshSession()} />;
  }
}

/** Keeps a live snapshot via server-sent events while signed in. */
function Live({ session, onSession, onSignedOut }: { session: Session; onSession: (s: Session) => void; onSignedOut: () => void }) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [stale, setStale] = useState(false);
  const last = useRef(Date.now());

  useEffect(() => {
    let es: EventSource | null = null;
    let closed = false;
    const connect = () => {
      es = new EventSource("/api/events");
      es.addEventListener("snapshot", (ev) => {
        last.current = Date.now();
        setStale(false);
        setSnap(JSON.parse((ev as MessageEvent).data) as Snapshot);
      });
      es.onerror = async () => {
        // The browser retries on its own; if the session ended, stop and
        // go back to sign-in.
        try {
          const s = await api.session();
          if (s.state !== "setup" && s.state !== "admin" && !closed) {
            es?.close();
            onSignedOut();
          }
        } catch {
          /* launcher unreachable: keep retrying */
        }
      };
    };
    connect();
    const watchdog = setInterval(() => setStale(Date.now() - last.current > 8000), 2000);
    return () => {
      closed = true;
      es?.close();
      clearInterval(watchdog);
    };
  }, [onSignedOut]);

  return (
    <ControlCenter
      session={session}
      snap={snap}
      stale={stale}
      onSession={onSession}
      onSignedOut={onSignedOut}
      applySnapshot={setSnap}
    />
  );
}
