import { useEffect, useState } from "react";
import { api, type AuditEntry } from "../api";
import { AuditTable } from "../components/CaseTabs";
import { useApp, useTitle } from "../hooks";
import { Badge, Button, ErrorPanel, Skeleton } from "../ui";

const ACTIONS: [string, string][] = [
  ["", "All actions"],
  ["auth.", "Sign-ins and passwords"],
  ["user.", "People"],
  ["case.", "Cases"],
  ["file.", "Files"],
  ["file.download", "Downloads"],
];

export function AuditLog() {
  const { cases } = useApp();
  const [filters, setFilters] = useState({ username: "", action: "", case_id: "", since: "", until: "" });
  const [items, setItems] = useState<AuditEntry[] | null>(null);
  const [check, setCheck] = useState<{ entries: number; intact: boolean; first_bad_entry: number | null } | null>(null);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useTitle("Audit log");

  const load = async (before?: number) => {
    try {
      // "until" is inclusive for people: up to the end of that day.
      const until = filters.until ? new Date(new Date(filters.until).getTime() + 86_400_000).toISOString().slice(0, 10) : "";
      const r = await api.audit({ ...filters, until, before });
      setItems((prev) => (before ? [...(prev ?? []), ...r.items] : r.items));
      setCheck(r.tamper_check);
      setMore(r.items.length === 200);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  useEffect(() => {
    const t = setTimeout(() => void load(), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);

  const set = (k: keyof typeof filters) => (e: { target: { value: string } }) => setFilters({ ...filters, [k]: e.target.value });

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">Audit log</h1>
          <p className="page__meta">Who signed in, opened, uploaded, downloaded or changed what, and when. Entries can't be edited.</p>
        </div>
      </header>
      {check && (
        check.intact ? (
          <p className="banner banner--ok" role="status"><Badge tone="ok" icon="shield">Tamper check passed</Badge> All {check.entries.toLocaleString()} entries are intact.</p>
        ) : (
          <p className="banner banner--error" role="alert">
            <Badge tone="error" icon="error">Tamper check failed</Badge>
            Entry {check.first_bad_entry} or one before it was changed or removed outside the app. Keep a copy of the data folder as it is now and restore app.db from a backup to compare.
          </p>
        )
      )}
      <div className="toolbar toolbar--wrap">
        <label className="field">
          <span>Person</span>
          <input className="input input--sm" value={filters.username} onChange={set("username")} placeholder="Username" />
        </label>
        <label className="field">
          <span>Action</span>
          <select className="input input--sm input--select" value={filters.action} onChange={set("action")}>
            {ACTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Case</span>
          <select className="input input--sm input--select" value={filters.case_id} onChange={set("case_id")}>
            <option value="">All cases</option>
            {cases.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label className="field">
          <span>From</span>
          <input className="input input--sm" type="date" value={filters.since} onChange={set("since")} />
        </label>
        <label className="field">
          <span>To</span>
          <input className="input input--sm" type="date" value={filters.until} onChange={set("until")} />
        </label>
      </div>
      {error && <ErrorPanel message={error} onRetry={() => void load()} />}
      {!items && !error && <Skeleton rows={8} />}
      {items && <AuditTable items={items} showCase />}
      {items && more && <Button onClick={() => void load(items[items.length - 1].id)}>Show older entries</Button>}
    </div>
  );
}
