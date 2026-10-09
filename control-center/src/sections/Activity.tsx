import { useEffect, useState } from "react";
import { api, type AuditEntry } from "../api";
import { dateTime } from "../format";
import { Section } from "../ui";

const labels: Record<string, string> = {
  "launcher.start": "Launcher started",
  "launcher.stop": "Launcher stopped",
  "auth.setup_code": "Setup code entered",
  "auth.create_admin": "Admin account created",
  "auth.login": "Signed in",
  "auth.logout": "Signed out",
  "service.start": "Start service",
  "service.stop": "Stop service",
  "service.restart": "Restart service",
  "stack.start_all": "Start all services",
  "stack.stop_all": "Stop all services",
  "diagnostics.bundle": "Download diagnostics",
  "cert.renew": "Renew certificate",
  "launcher.set_lan_binding": "Change network access",
  "action.rejected": "Rejected request",
};

const outcomes: Record<string, string> = {
  requested: "Requested",
  succeeded: "Done",
  failed: "Failed",
  denied: "Refused",
};

/** Recent launcher audit entries. The full audit log arrives with the web app. */
export function Activity({ generatedAt }: { generatedAt: string }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Refresh every ~10 seconds, driven by the live snapshot.
  const tick = Math.floor(Date.parse(generatedAt) / 10_000);

  useEffect(() => {
    api.audit().then(
      (r) => {
        setEntries(r.entries);
        setError(null);
      },
      (e: Error) => setError(e.message),
    );
  }, [tick]);

  return (
    <Section id="activity" title="Activity" meta="The last 50 entries in the launcher's audit log">
      {error ? (
        <p className="field__error field__error--block" role="alert">{error}</p>
      ) : !entries ? (
        <p className="muted">Loading…</p>
      ) : entries.length === 0 ? (
        <p className="empty-inline">Nothing recorded yet. Every sign-in and service action will appear here.</p>
      ) : (
        <div className="table-wrap">
          <table className="table table--compact">
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Who</th>
                <th scope="col">What</th>
                <th scope="col">Target</th>
                <th scope="col">Result</th>
                <th scope="col">From</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => (
                <tr key={`${e.time}-${i}`}>
                  <td className="mono nowrap">{dateTime(e.time)}</td>
                  <td>{e.actor}</td>
                  <td className="nowrap">{labels[e.action] ?? e.action}</td>
                  <td className="mono">{e.target || "–"}</td>
                  <td className={`outcome outcome--${e.outcome}`}>
                    <span className="outcome__text" title={e.detail || undefined}>
                      {outcomes[e.outcome] ?? e.outcome}{e.detail ? `: ${e.detail}` : ""}
                    </span>
                  </td>
                  <td className="mono muted">{e.ip}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
