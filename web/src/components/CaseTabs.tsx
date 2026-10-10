import { useEffect, useState, type FormEvent } from "react";
import { api, type AuditEntry, type Case } from "../api";
import { describeAction, formatDateTime, roleLabel, shortHash } from "../format";
import { useLoad } from "../hooks";
import { Badge, Button, EmptyState, ErrorPanel, Field, FormError, Modal, Skeleton } from "../ui";

export function DetailsTab({ c, canEdit, canRename, onSaved }: { c: Case; canEdit: boolean; canRename: boolean; onSaved: (c: Case) => void }) {
  const [form, setForm] = useState({ name: c.name, reference: c.reference, client: c.client, description: c.description, notes: c.notes });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setForm({ name: c.name, reference: c.reference, client: c.client, description: c.description, notes: c.notes }), [c]);
  const dirty = (Object.keys(form) as (keyof typeof form)[]).some((k) => form[k] !== c[k]);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const changes = Object.fromEntries((Object.keys(form) as (keyof typeof form)[]).filter((k) => form[k] !== c[k]).map((k) => [k, form[k]]));
      onSaved(await api.editCase(c.id, changes));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!canEdit) {
    return (
      <dl className="kv kv--wide">
        <dt>Name</dt><dd>{c.name}</dd>
        <dt>Reference</dt><dd className="mono">{c.reference || <span className="muted">None</span>}</dd>
        <dt>Client</dt><dd>{c.client || <span className="muted">None</span>}</dd>
        <dt>Description</dt><dd className="prewrap">{c.description || <span className="muted">None</span>}</dd>
        <dt>Notes</dt><dd className="prewrap">{c.notes || <span className="muted">None</span>}</dd>
        <dt>Created</dt><dd>{formatDateTime(c.created_at)}{c.created_by ? ` by ${c.created_by}` : ""}</dd>
      </dl>
    );
  }
  return (
    <form className="form-grid form-grid--wide" onSubmit={submit} noValidate>
      <FormError message={error} />
      <Field id="d-name" label="Name" hint={canRename ? undefined : "Only the Admin can rename a case."}>
        <input id="d-name" className="input" value={form.name} onChange={set("name")} disabled={!canRename} maxLength={200} />
      </Field>
      <div className="form-row">
        <Field id="d-ref" label="Reference">
          <input id="d-ref" className="input" value={form.reference} onChange={set("reference")} maxLength={100} />
        </Field>
        <Field id="d-client" label="Client">
          <input id="d-client" className="input" value={form.client} onChange={set("client")} maxLength={200} />
        </Field>
      </div>
      <Field id="d-desc" label="Description">
        <textarea id="d-desc" className="input textarea" rows={3} value={form.description} onChange={set("description")} maxLength={5000} />
      </Field>
      <Field id="d-notes" label="Notes" hint="Visible to everyone with access to this case.">
        <textarea id="d-notes" className="input textarea textarea--tall" rows={8} value={form.notes} onChange={set("notes")} maxLength={100000} />
      </Field>
      <p className="hint">Created {formatDateTime(c.created_at)}{c.created_by ? ` by ${c.created_by}` : ""}.</p>
      <div className="btn-row">
        <Button type="submit" variant="primary" busy={busy} disabled={!dirty || !form.name.trim()}>Save changes</Button>
        {dirty && <Button variant="quiet" onClick={() => setForm({ name: c.name, reference: c.reference, client: c.client, description: c.description, notes: c.notes })}>Discard changes</Button>}
      </div>
    </form>
  );
}

export function PeopleTab({ caseId, canManage, onChanged }: { caseId: string; canManage: boolean; onChanged: () => void }) {
  const { data, error, loading, reload, setData } = useLoad(() => api.members(caseId), [caseId]);
  const [editing, setEditing] = useState(false);
  return (
    <div className="stack">
      <div className="stack__head">
        <p className="muted">{canManage ? "People listed here can open this case. The Admin sees every case." : "People who can open this case, besides the Admin."}</p>
        {canManage && <Button icon="user" onClick={() => setEditing(true)}>Change who has access</Button>}
      </div>
      {error && <ErrorPanel message={error} onRetry={reload} />}
      {!data && loading && <Skeleton rows={3} />}
      {data && data.items.length === 0 && (
        <EmptyState icon="user" title="Only the Admin has access." action={canManage ? <Button onClick={() => setEditing(true)}>Give people access</Button> : undefined}>
          {canManage ? "Add Editors to let them upload and edit, or Read-only people to let them view and download." : undefined}
        </EmptyState>
      )}
      {data && data.items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">Name</th><th scope="col">Username</th><th scope="col">Role</th><th scope="col">Account</th></tr></thead>
            <tbody>
              {data.items.map((m) => (
                <tr key={m.id}>
                  <th scope="row">{m.display_name}</th>
                  <td className="mono-cell">{m.username}</td>
                  <td>{roleLabel[m.role]}</td>
                  <td>{m.active ? <Badge tone="ok" icon="ok">Active</Badge> : <Badge tone="idle" icon="stopped">Switched off</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {canManage && data && (
        <AccessDialog
          open={editing}
          onOpenChange={setEditing}
          caseId={caseId}
          current={data.items.map((m) => m.id)}
          onSaved={(items) => {
            setData({ items });
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function AccessDialog({ open, onOpenChange, caseId, current, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; caseId: string; current: number[]; onSaved: (items: Awaited<ReturnType<typeof api.members>>["items"]) => void }) {
  const people = useLoad(() => (open ? api.people() : Promise.resolve(null)), [open]);
  const [chosen, setChosen] = useState<number[]>(current);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) setChosen(current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const list = (people.data?.items ?? []).filter((p) => p.role !== "admin");
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      onSaved((await api.setMembers(caseId, chosen)).items);
      onOpenChange(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Who has access to this case"
      description="Ticked people can open this case. Removing someone takes effect immediately."
      footer={<><Button onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" busy={busy} onClick={() => void save()}>Save access</Button></>}
    >
      <FormError message={error} />
      {people.loading && !people.data && <Skeleton rows={3} />}
      {people.data && list.length === 0 && <p className="hint">Nobody else has an account yet. Add people from the People page first.</p>}
      <ul className="checklist">
        {list.map((p) => (
          <li key={p.id}>
            <label className="check-inline">
              <input type="checkbox" checked={chosen.includes(p.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, p.id] : chosen.filter((x) => x !== p.id))} />
              {p.display_name} <span className="muted">· {roleLabel[p.role]}{p.active ? "" : " · switched off"}</span>
            </label>
          </li>
        ))}
      </ul>
    </Modal>
  );
}

export function ActivityTab({ caseId }: { caseId: string }) {
  const [items, setItems] = useState<AuditEntry[] | null>(null);
  const [more, setMore] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = async (before?: number) => {
    try {
      const r = await api.activity(caseId, before);
      setItems((prev) => (before ? [...(prev ?? []), ...r.items] : r.items));
      setMore(r.items.length === 100);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);
  if (error && !items) return <ErrorPanel message={error} onRetry={() => void load()} />;
  if (!items) return <Skeleton rows={6} />;
  return (
    <div className="stack">
      <AuditTable items={items} showCase={false} />
      {more && <Button onClick={() => void load(items[items.length - 1]?.id)}>Show older activity</Button>}
    </div>
  );
}

/** Hashes are kept whole in the log; the table shows a short form. */
const shortenHashes = (s: string) => s.replace(/\b[0-9a-f]{64}\b/g, shortHash);

export function AuditTable({ items, showCase }: { items: AuditEntry[]; showCase: boolean }) {
  if (items.length === 0) return <p className="empty-inline">Nothing recorded yet.</p>;
  return (
    <div className="table-wrap">
      <table className="table table--compact">
        <thead>
          <tr>
            <th scope="col">When</th>
            <th scope="col">Who</th>
            <th scope="col">What</th>
            {showCase && <th scope="col">Case</th>}
            <th scope="col">Details</th>
          </tr>
        </thead>
        <tbody>
          {items.map((e) => (
            <tr key={e.id} className={e.outcome !== "ok" ? "row--refused" : undefined}>
              <td className="nowrap">{formatDateTime(e.at)}</td>
              <td className="nowrap">{e.username}</td>
              <td className="nowrap">
                {describeAction(e.action)}
                {e.outcome === "denied" && <span className="outcome outcome--denied"> · denied</span>}
                {e.outcome === "refused" && <span className="outcome outcome--refused"> · not done</span>}
              </td>
              {showCase && <td className="cell-case"><span className="truncate">{e.case_name ?? (e.case_id ? <span className="muted">Deleted case</span> : "")}</span></td>}
              <td className="cell-detail">
                <span className="outcome__text" title={[e.target, e.detail].filter(Boolean).join(" · ")}>
                  {e.target}
                  {e.target && e.detail ? <span className="muted"> · {shortenHashes(e.detail)}</span> : shortenHashes(e.detail)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
