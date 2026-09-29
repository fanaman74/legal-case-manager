import { useCallback, useEffect, useState } from "react";
import { api, type ActionName, type Session, type Snapshot } from "../api";
import { Activity } from "../sections/Activity";
import { Checks } from "../sections/Checks";
import { Logs } from "../sections/Logs";
import { Network } from "../sections/Network";
import { Services } from "../sections/Services";
import { Wizard } from "../sections/Wizard";
import { Icon } from "../ui";

export type RunAction = (action: ActionName, extra?: { service?: string; enabled?: boolean }) => Promise<string | null>;

export function ControlCenter({
  session,
  snap,
  stale,
  onSession,
  onSignedOut,
}: {
  session: Session;
  snap: Snapshot | null;
  stale: boolean;
  onSession: (s: Session) => void;
  onSignedOut: () => void;
  applySnapshot: (s: Snapshot) => void;
}) {
  const isAdmin = session.state === "admin";
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const run: RunAction = useCallback(async (action, extra) => {
    setNotice(null);
    try {
      const res = await api.action(action, extra);
      if ("message" in res && !("id" in res)) {
        setNotice({ tone: "ok", text: res.message });
        return res.message;
      }
      return null;
    } catch (e) {
      setNotice({ tone: "error", text: (e as Error).message });
      return null;
    }
  }, []);

  useEffect(() => {
    if (!notice || notice.tone !== "ok") return;
    const t = setTimeout(() => setNotice(null), 8000);
    return () => clearTimeout(t);
  }, [notice]);

  const signOut = async () => {
    try {
      await api.logout();
    } finally {
      onSignedOut();
    }
  };

  // Once the Admin exists, setup has been completed at least once: keep the
  // wizard collapsed even if a service is later stopped.
  const setupDone = !!snap?.hasAdmin;
  const running = snap?.services.filter((s) => s.state === "running").length ?? 0;
  const total = snap?.services.length ?? 6;
  const problems = snap?.checks.filter((c) => c.level === "fail" || c.level === "warn").length ?? 0;
  const stepsDone = snap?.wizard.filter((s) => s.state === "done").length ?? 0;

  const nav = [
    { id: "setup", label: "Setup", meta: snap ? `${stepsDone} of ${snap.wizard.length}` : "" },
    { id: "services", label: "Services", meta: snap ? `${running} of ${total}` : "", alert: snap?.services.some((s) => s.state === "error") },
    { id: "checks", label: "System checks", meta: problems ? String(problems) : "", alert: problems > 0 },
    ...(isAdmin
      ? [
          { id: "network", label: "Network" },
          { id: "logs", label: "Logs" },
          { id: "activity", label: "Activity" },
        ]
      : []),
  ];

  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <header className="topbar">
        <div className="topbar__brand">
          <span className="brand__mark" aria-hidden="true" />
          <span className="brand__name">Case File Manager</span>
          <span className="brand__sep" aria-hidden="true">/</span>
          <span className="brand__area">Control Center</span>
        </div>
        <div className="topbar__right">
          {snap && (
            <span className={`engine engine--${snap.runtime.ok ? "ok" : "down"}`}>
              <Icon name="engine" />
              {snap.runtime.ok ? snap.runtime.name : "Installation needs repair"}
            </span>
          )}
          <span className="topbar__user">{isAdmin ? session.user : "Setup"}</span>
          <button className="btn btn--quiet btn--sm" onClick={() => void signOut()}>
            <Icon name="signout" />
            <span>Sign out</span>
          </button>
        </div>
      </header>

      <nav className="rail" aria-label="Sections">
        <div className="rail__inner">
        <ul>
          {nav.map((n) => (
            <li key={n.id}>
              <a href={`#${n.id}`} className="rail__link">
                <span>{n.label}</span>
                {n.meta && (
                  <span className={`rail__meta${n.alert ? " rail__meta--alert" : ""}`}>
                    {n.alert && <Icon name="warn" />}
                    {n.meta}
                  </span>
                )}
              </a>
            </li>
          ))}
        </ul>
        {snap && <p className="rail__foot mono">Launcher {snap.launcherVersion}</p>}
        </div>
      </nav>

      <main id="main" className="content" tabIndex={-1}>
        {stale && (
          <div className="banner banner--warn" role="status">
            <Icon name="warn" />
            <span>Lost contact with the launcher. Trying to reconnect. The information below may be out of date.</span>
          </div>
        )}
        {notice && (
          <div className={`banner banner--${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"}>
            <Icon name={notice.tone === "ok" ? "ok" : "error"} />
            <span>{notice.text}</span>
            <button className="banner__close" onClick={() => setNotice(null)} aria-label="Dismiss">×</button>
          </div>
        )}
        {snap?.runtime.problem && (
          <div className="banner banner--error banner--problem" role="alert">
            <Icon name="error" />
            <div>
              <p className="banner__title">{snap.runtime.problem.what}</p>
              <p>{snap.runtime.problem.why} {snap.runtime.problem.next}</p>
            </div>
          </div>
        )}

        {!snap ? (
          <LoadingSkeleton />
        ) : (
          <>
            <Wizard snap={snap} session={session} onSession={onSession} run={run} collapsed={setupDone && isAdmin} />
            <Services snap={snap} run={run} isAdmin={isAdmin} />
            <Checks snap={snap} run={run} isAdmin={isAdmin} />
            {isAdmin && (
              <>
                <Network snap={snap} run={run} />
                <Logs snap={snap} />
                <Activity generatedAt={snap.generatedAt} />
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Loading status">
      <div className="skeleton__title" />
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="skeleton__row" />
      ))}
    </div>
  );
}
