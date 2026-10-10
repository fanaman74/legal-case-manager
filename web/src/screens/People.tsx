import { useState, type FormEvent } from "react";
import { api, type Person, type Role } from "../api";
import { formatDateTime, roleLabel } from "../format";
import { useApp, useLoad, useTitle } from "../hooks";
import { ActionMenu, Badge, Button, CopyButton, ErrorPanel, Field, FormError, Modal, Skeleton, type MenuItem } from "../ui";

type Editing = { kind: "add" } | { kind: "edit"; p: Person } | { kind: "cases"; p: Person } | { kind: "password"; p: Person; password: string; isNew: boolean } | null;

export function People() {
  const { cases, toast } = useApp();
  const { data, error, loading, reload } = useLoad(() => api.people(), []);
  const [editing, setEditing] = useState<Editing>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useTitle("People");
  const caseName = (id: string) => cases.find((c) => c.id === id)?.name;

  const act = async (fn: () => Promise<unknown>, done: string) => {
    setProblem(null);
    try {
      await fn();
      toast(done);
      await reload();
    } catch (e) {
      setProblem((e as Error).message);
    }
  };

  const menu = (p: Person): MenuItem[] =>
    p.role === "admin"
      ? []
      : [
          { label: "Change name or role", onSelect: () => setEditing({ kind: "edit", p }) },
          { label: "Choose cases", onSelect: () => setEditing({ kind: "cases", p }) },
          {
            label: "Reset password",
            icon: "key",
            onSelect: () => {
              setProblem(null);
              api.resetPassword(p.id).then(
                (r) => { setEditing({ kind: "password", p: r.user, password: r.temporary_password, isNew: false }); void reload(); },
                (e: Error) => setProblem(e.message),
              );
            },
          },
          ...(p.locked ? [{ label: "Unlock account", icon: "lock" as const, onSelect: () => void act(() => api.unlock(p.id), `${p.display_name} can try signing in again.`) }] : []),
          "separator",
          p.active
            ? { label: "Switch off account", danger: true, onSelect: () => void act(() => api.editPerson(p.id, { active: false }), `${p.display_name} is signed out and can't sign in.`) }
            : { label: "Switch account back on", onSelect: () => void act(() => api.editPerson(p.id, { active: true }), `${p.display_name} can sign in again.`) },
        ];

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">People</h1>
          <p className="page__meta">Everyone who can sign in. Editors upload and edit in their cases; Read-only people view and download.</p>
        </div>
        <Button variant="primary" icon="plus" onClick={() => setEditing({ kind: "add" })}>Add person</Button>
      </header>
      {(error || problem) && <ErrorPanel message={(problem ?? error)!} onRetry={problem ? undefined : reload} />}
      {!data && loading && <Skeleton rows={4} />}
      {data && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Username</th>
                <th scope="col">Role</th>
                <th scope="col">Cases</th>
                <th scope="col">Last sign-in</th>
                <th scope="col">Status</th>
                <th scope="col"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((p) => (
                <tr key={p.id}>
                  <th scope="row">{p.display_name}</th>
                  <td className="mono-cell">{p.username}</td>
                  <td>{roleLabel[p.role]}</td>
                  <td className="cell-detail">
                    {p.role === "admin" ? <span className="muted">All cases</span> : p.case_ids.length === 0 ? <span className="muted">None yet</span> : (
                      <span className="truncate" title={p.case_ids.map(caseName).filter(Boolean).join(", ")}>
                        {p.case_ids.map(caseName).filter(Boolean).join(", ") || `${p.case_ids.length} archived`}
                      </span>
                    )}
                  </td>
                  <td className="nowrap">{p.last_login_at ? formatDateTime(p.last_login_at) : <span className="muted">Never</span>}</td>
                  <td className="nowrap">
                    {!p.active ? <Badge tone="idle" icon="stopped">Switched off</Badge>
                      : p.locked ? <Badge tone="warn" icon="lock">Locked out</Badge>
                      : p.must_change_password ? <Badge tone="progress" icon="pending">Waiting for first sign-in</Badge>
                      : <Badge tone="ok" icon="ok">Active</Badge>}
                  </td>
                  <td className="row-actions">
                    {p.role === "admin" ? <span className="hint">Managed in the Control Center</span> : <ActionMenu label={`Actions for ${p.display_name}`} items={menu(p)} />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && data.items.length === 1 && <p className="hint">Only you have an account so far. Add the people you work with, then give them access to cases.</p>}

      <PersonDialog
        editing={editing?.kind === "add" || editing?.kind === "edit" ? editing : null}
        onClose={() => setEditing(null)}
        onAdded={(p, password) => { setEditing({ kind: "password", p, password, isNew: true }); void reload(); }}
        onSaved={() => { setEditing(null); toast("Changes saved."); void reload(); }}
      />
      <CasesDialog
        person={editing?.kind === "cases" ? editing.p : null}
        onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); toast("Case access saved."); void reload(); }}
      />
      <Modal
        open={editing?.kind === "password"}
        onOpenChange={(o) => !o && setEditing(null)}
        title={editing?.kind === "password" && editing.isNew ? `${editing.p.display_name} can now sign in` : "New temporary password"}
        description="Give this temporary password to them in person or by phone, not by email. It's shown only once. They choose their own password the first time they sign in."
        footer={<Button variant="primary" onClick={() => setEditing(null)}>Done</Button>}
      >
        {editing?.kind === "password" && (
          <dl className="kv">
            <dt>Username</dt>
            <dd className="mono">{editing.p.username}</dd>
            <dt>Temporary password</dt>
            <dd className="hash"><span className="mono temp-password">{editing.password}</span><CopyButton text={editing.password} what="temporary password" /></dd>
            <dt>Address</dt>
            <dd className="mono">{location.origin}</dd>
          </dl>
        )}
      </Modal>
    </div>
  );
}

function PersonDialog({ editing, onClose, onAdded, onSaved }: { editing: { kind: "add" } | { kind: "edit"; p: Person } | null; onClose: () => void; onAdded: (p: Person, password: string) => void; onSaved: () => void }) {
  const { cases } = useApp();
  const p = editing?.kind === "edit" ? editing.p : null;
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Exclude<Role, "admin">>("editor");
  const [caseIds, setCaseIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [openedFor, setOpenedFor] = useState<Editing | undefined>(undefined);
  if (editing !== openedFor) {
    setOpenedFor(editing);
    setName(p?.display_name ?? "");
    setUsername(p?.username ?? "");
    setRole(p && p.role !== "admin" ? p.role : "editor");
    setCaseIds([]);
    setError(null);
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (p) {
        await api.editPerson(p.id, { display_name: name.trim(), role });
        onSaved();
      } else {
        const r = await api.addPerson({ username: username.trim(), display_name: name.trim(), role, case_ids: caseIds });
        onAdded(r.user, r.temporary_password);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={!!editing}
      onOpenChange={(o) => !o && onClose()}
      title={p ? `Change ${p.display_name}` : "Add person"}
      wide={!p}
      footer={<><Button onClick={onClose}>Cancel</Button><Button type="submit" form="person-form" variant="primary" busy={busy} disabled={!name.trim() || (!p && !username.trim())}>{p ? "Save" : "Add person"}</Button></>}
    >
      <form id="person-form" className="form-grid form-grid--wide" onSubmit={submit} noValidate>
        <FormError message={error} />
        <div className="form-row">
          <Field id="p-name" label="Name">
            <input id="p-name" className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} autoFocus />
          </Field>
          {!p && (
            <Field id="p-username" label="Username" hint="Letters, numbers, dots, dashes, _ or @.">
              <input id="p-username" className="input" value={username} onChange={(e) => setUsername(e.target.value)} maxLength={64} autoCapitalize="none" spellCheck={false} aria-describedby="p-username-hint" />
            </Field>
          )}
        </div>
        <fieldset className="fieldset">
          <legend>Role</legend>
          <label className="radio">
            <input type="radio" name="role" checked={role === "editor"} onChange={() => setRole("editor")} />
            <span><strong>Editor</strong><span className="muted"> · uploads files, tags them and edits case details in their cases</span></span>
          </label>
          <label className="radio">
            <input type="radio" name="role" checked={role === "readonly"} onChange={() => setRole("readonly")} />
            <span><strong>Read-only</strong><span className="muted"> · views and downloads files in their cases, changes nothing</span></span>
          </label>
          {p && <p className="hint">Changing the role signs them out so the new role applies at once.</p>}
        </fieldset>
        {!p && (
          <fieldset className="fieldset">
            <legend>Cases they can open</legend>
            {cases.length === 0 ? <p className="hint">No open cases yet. You can give access later from each case's People tab.</p> : (
              <ul className="checklist">
                {cases.map((c) => (
                  <li key={c.id}>
                    <label className="check-inline">
                      <input type="checkbox" checked={caseIds.includes(c.id)} onChange={(e) => setCaseIds(e.target.checked ? [...caseIds, c.id] : caseIds.filter((x) => x !== c.id))} />
                      {c.name}{c.reference && <span className="muted"> · {c.reference}</span>}
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </fieldset>
        )}
      </form>
    </Modal>
  );
}

function CasesDialog({ person, onClose, onSaved }: { person: Person | null; onClose: () => void; onSaved: () => void }) {
  const all = useLoad(() => (person ? api.cases(true) : Promise.resolve(null)), [person]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [openedFor, setOpenedFor] = useState<Person | null | undefined>(undefined);
  if (person !== openedFor) {
    setOpenedFor(person);
    setChosen(person?.case_ids ?? []);
    setError(null);
  }
  const save = async () => {
    if (!person) return;
    setBusy(true);
    setError(null);
    try {
      await api.setPersonCases(person.id, chosen);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={!!person}
      onOpenChange={(o) => !o && onClose()}
      title={person ? `Cases ${person.display_name} can open` : ""}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} onClick={() => void save()}>Save</Button></>}
    >
      <FormError message={error} />
      {!all.data && <Skeleton rows={3} />}
      {all.data && all.data.items.length === 0 && <p className="hint">There are no cases yet.</p>}
      <ul className="checklist">
        {all.data?.items.map((c) => (
          <li key={c.id}>
            <label className="check-inline">
              <input type="checkbox" checked={chosen.includes(c.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, c.id] : chosen.filter((x) => x !== c.id))} />
              {c.name}{c.status === "archived" && <span className="muted"> · archived</span>}
            </label>
          </li>
        ))}
      </ul>
    </Modal>
  );
}
