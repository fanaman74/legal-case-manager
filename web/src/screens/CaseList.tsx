import { useMemo, useState, type FormEvent } from "react";
import { api, type Case, type Person } from "../api";
import { formatDate, plural } from "../format";
import { useApp, useLoad, useTitle } from "../hooks";
import { href, navigate } from "../router";
import { Badge, Button, EmptyState, ErrorPanel, Field, FormError, Icon, Link, Modal, Skeleton } from "../ui";

type SortKey = "name" | "reference" | "client" | "file_count" | "created_at";

export function CaseList() {
  const { user, reloadCases } = useApp();
  const isAdmin = user.role === "admin";
  const [archived, setArchived] = useState(false);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({ key: "created_at", asc: false });
  const [creating, setCreating] = useState(false);
  const { data, error, loading, reload } = useLoad(() => api.cases(archived), [archived]);
  useTitle("Cases");

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = (data?.items ?? []).filter((c) => !needle || [c.name, c.reference, c.client].some((v) => v.toLowerCase().includes(needle)));
    const dir = sort.asc ? 1 : -1;
    return [...list].sort((a, b) => {
      const x = a[sort.key] ?? "";
      const y = b[sort.key] ?? "";
      return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { sensitivity: "base", numeric: true })) * dir;
    });
  }, [data, query, sort]);

  const total = data?.items.length ?? 0;

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">Cases</h1>
          {data && <p className="page__meta">{plural(total, archived ? "case" : "open case")}{archived ? ", including archived" : ""}</p>}
        </div>
        {isAdmin && (
          <Button variant="primary" icon="plus" onClick={() => setCreating(true)}>
            New case
          </Button>
        )}
      </header>

      {total > 0 || query || archived ? (
        <div className="toolbar">
          <label className="search">
            <Icon name="search" />
            <span className="sr-only">Search cases</span>
            <input className="input input--search" type="search" placeholder="Search by name, reference or client" value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <label className="check-inline">
            <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
            Show archived cases
          </label>
        </div>
      ) : null}

      {error && <ErrorPanel message={error} onRetry={reload} />}
      {!data && loading && <Skeleton />}
      {data && total === 0 && !archived && (
        isAdmin ? (
          <EmptyState icon="case" title="No cases yet." action={<Button variant="primary" icon="plus" onClick={() => setCreating(true)}>Create your first case</Button>}>
            Each case keeps its own files, people and history.
          </EmptyState>
        ) : (
          <EmptyState icon="lock" title="No cases are assigned to you yet.">Ask the Admin to add you to a case.</EmptyState>
        )
      )}
      {data && total > 0 && rows.length === 0 && <p className="empty-inline">No cases match “{query}”.</p>}
      {data && rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <SortHeader label="Name" k="name" sort={sort} setSort={setSort} />
                <SortHeader label="Reference" k="reference" sort={sort} setSort={setSort} />
                <SortHeader label="Client" k="client" sort={sort} setSort={setSort} />
                <SortHeader label="Files" k="file_count" sort={sort} setSort={setSort} num />
                <th scope="col" className="num">People</th>
                <SortHeader label="Created" k="created_at" sort={sort} setSort={setSort} />
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="row--link">
                  <th scope="row">
                    <Link className="row__title" to={href({ page: "case", id: c.id, tab: "files", folder: "" })}>{c.name}</Link>
                  </th>
                  <td className="mono-cell">{c.reference}</td>
                  <td>{c.client}</td>
                  <td className="num">{(c.file_count ?? 0).toLocaleString()}</td>
                  <td className="num">{(c.member_count ?? 0).toLocaleString()}</td>
                  <td className="nowrap">{formatDate(c.created_at)}</td>
                  <td>{c.status === "archived" ? <Badge tone="idle" icon="archive">Archived</Badge> : <Badge tone="ok" icon="ok">Open</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {isAdmin && (
        <NewCaseDialog
          open={creating}
          onOpenChange={setCreating}
          onCreated={(c) => {
            void reloadCases();
            void reload();
            navigate(href({ page: "case", id: c.id, tab: "files", folder: "" }));
          }}
        />
      )}
    </div>
  );
}

function SortHeader({ label, k, sort, setSort, num }: { label: string; k: SortKey; sort: { key: SortKey; asc: boolean }; setSort: (s: { key: SortKey; asc: boolean }) => void; num?: boolean }) {
  const active = sort.key === k;
  return (
    <th scope="col" className={num ? "num" : undefined} aria-sort={active ? (sort.asc ? "ascending" : "descending") : "none"}>
      <button className="sort" onClick={() => setSort({ key: k, asc: active ? !sort.asc : k !== "created_at" && k !== "file_count" })}>
        {label}
        <span className={`sort__arrow${active ? " sort__arrow--on" : ""}`} aria-hidden="true">{active && !sort.asc ? "↓" : "↑"}</span>
      </button>
    </th>
  );
}

function NewCaseDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (c: Case) => void }) {
  const [name, setName] = useState("");
  const [reference, setReference] = useState("");
  const [client, setClient] = useState("");
  const [description, setDescription] = useState("");
  const [members, setMembers] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const people = useLoad(() => (open ? api.people() : Promise.resolve({ items: [] as Person[] })), [open]);
  const assignable = (people.data?.items ?? []).filter((p) => p.role !== "admin" && p.active);

  const reset = () => {
    setName("");
    setReference("");
    setClient("");
    setDescription("");
    setMembers([]);
    setError(null);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const c = await api.createCase({ name: name.trim(), reference: reference.trim(), client: client.trim(), description, member_ids: members });
      reset();
      onOpenChange(false);
      onCreated(c);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
      title="New case"
      wide
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type="submit" form="new-case" variant="primary" busy={busy} disabled={!name.trim()}>Create case</Button>
        </>
      }
    >
      <form id="new-case" className="form-grid form-grid--wide" onSubmit={submit} noValidate>
        <FormError message={error} />
        <Field id="case-name" label="Name">
          <input id="case-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Smith v Jones" maxLength={200} autoFocus />
        </Field>
        <div className="form-row">
          <Field id="case-ref" label="Reference (optional)">
            <input id="case-ref" className="input" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="2026/014" maxLength={100} />
          </Field>
          <Field id="case-client" label="Client (optional)">
            <input id="case-client" className="input" value={client} onChange={(e) => setClient(e.target.value)} maxLength={200} />
          </Field>
        </div>
        <Field id="case-desc" label="Description (optional)">
          <textarea id="case-desc" className="input textarea" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={5000} />
        </Field>
        <fieldset className="fieldset">
          <legend>People with access</legend>
          {people.loading && !people.data ? (
            <p className="hint">Loading people…</p>
          ) : assignable.length === 0 ? (
            <p className="hint">Nobody else has an account yet. You can add people later from People, then give them access from the case's People tab.</p>
          ) : (
            <ul className="checklist">
              {assignable.map((p) => (
                <li key={p.id}>
                  <label className="check-inline">
                    <input type="checkbox" checked={members.includes(p.id)} onChange={(e) => setMembers(e.target.checked ? [...members, p.id] : members.filter((m) => m !== p.id))} />
                    {p.display_name} <span className="muted">· {p.role === "editor" ? "Editor" : "Read-only"}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          <p className="hint">You see every case as Admin, so you don't need to add yourself.</p>
        </fieldset>
      </form>
    </Modal>
  );
}
