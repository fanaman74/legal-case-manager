import * as Tabs from "@radix-ui/react-tabs";
import { useState } from "react";
import { api, ApiError, type Case } from "../api";
import { ActivityTab, DetailsTab, PeopleTab } from "../components/CaseTabs";
import { FilesTab } from "../components/FilesTab";
import { useApp, useLoad, useTitle } from "../hooks";
import { href, navigate, type CaseTab } from "../router";
import { ActionMenu, Badge, Button, EmptyState, ErrorPanel, Field, FormError, Link, Modal, Skeleton, type MenuItem } from "../ui";

export function CasePage({ id, tab, folder }: { id: string; tab: CaseTab; folder: string }) {
  const { reloadCases, toast } = useApp();
  const { data: c, error, loading, reload, setData } = useLoad(() => api.case(id), [id]);
  const [deleting, setDeleting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  useTitle(c?.name ?? "Case");

  if (error && !c) {
    const missing = error.includes("don't have access");
    return missing ? (
      <EmptyState icon="lock" title="You don't have access to this case." action={<Link to="/">Go to your cases</Link>}>
        It may have been deleted, or you haven't been added to it. Ask the Admin if you need it.
      </EmptyState>
    ) : (
      <ErrorPanel message={error} onRetry={reload} />
    );
  }
  if (!c || (loading && !c)) return <div className="page"><div className="skeleton__title" /><Skeleton rows={8} /></div>;

  const perms = c.permissions ?? { edit: false, upload: false, manage: false };
  const updated = (next: Case) => {
    setData(next);
    void reloadCases();
  };
  const run = async (fn: () => Promise<Case>, done: string) => {
    setProblem(null);
    try {
      updated(await fn());
      toast(done);
    } catch (e) {
      setProblem((e as Error).message);
    }
  };

  const menu: MenuItem[] = [
    { label: "Edit details", icon: "file", onSelect: () => navigate(href({ page: "case", id, tab: "details", folder: "" })) },
    c.status === "active"
      ? { label: "Archive case", icon: "archive", onSelect: () => void run(() => api.archiveCase(id), "Case archived. It's read-only until restored.") }
      : { label: "Restore case", icon: "archive", onSelect: () => void run(() => api.restoreCase(id), "Case restored.") },
    "separator",
    { label: "Delete case…", icon: "error", danger: true, onSelect: () => setDeleting(true) },
  ];

  const tabHref = (t: CaseTab) => href({ page: "case", id, tab: t, folder: t === "files" ? folder : "" });

  return (
    <div className="page">
      <header className="page__head">
        <div className="case-head">
          <div className="case-head__title">
            <h1 className="page__title">{c.name}</h1>
            {c.status === "archived" && <Badge tone="idle" icon="archive">Archived</Badge>}
          </div>
          {(c.reference || c.client) && (
            <p className="page__meta">
              {c.reference && <span className="mono">{c.reference}</span>}
              {c.reference && c.client && <span aria-hidden="true"> · </span>}
              {c.client}
            </p>
          )}
        </div>
        {perms.manage && <ActionMenu label="Case actions" items={menu} trigger={<Button icon="more">Case actions</Button>} />}
      </header>
      {problem && <ErrorPanel message={problem} />}
      {c.status === "archived" && (
        <p className="banner banner--idle" role="status">
          This case is archived, so nobody can add or change anything in it. {perms.manage ? "Restore it from Case actions to make changes." : "The Admin can restore it."}
        </p>
      )}

      <Tabs.Root value={tab} onValueChange={(t) => navigate(tabHref(t as CaseTab))} activationMode="manual">
        <Tabs.List className="tabs" aria-label="Case sections">
          {(["files", "details", "people", "activity"] as CaseTab[]).map((t) => (
            <Tabs.Trigger key={t} value={t} className="tabs__tab">
              {{ files: "Files", details: "Details", people: "People", activity: "Activity" }[t]}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="files" className="tabs__panel">
          {tab === "files" && <FilesTab caseId={id} folder={folder} canUpload={perms.upload} canEdit={perms.edit} onChanged={() => void reloadCases()} />}
        </Tabs.Content>
        <Tabs.Content value="details" className="tabs__panel">
          {tab === "details" && <DetailsTab c={c} canEdit={perms.edit} canRename={perms.manage && c.status === "active"} onSaved={(next) => { updated(next); toast("Details saved."); }} />}
        </Tabs.Content>
        <Tabs.Content value="people" className="tabs__panel">
          {tab === "people" && <PeopleTab caseId={id} canManage={perms.manage} onChanged={() => void reloadCases()} />}
        </Tabs.Content>
        <Tabs.Content value="activity" className="tabs__panel">
          {tab === "activity" && <ActivityTab caseId={id} />}
        </Tabs.Content>
      </Tabs.Root>

      {perms.manage && (
        <DeleteCaseDialog
          c={c}
          open={deleting}
          onOpenChange={setDeleting}
          onDeleted={() => {
            void reloadCases();
            toast(`“${c.name}” was deleted.`);
            navigate("/", true);
          }}
        />
      )}
    </div>
  );
}

function DeleteCaseDialog({ c, open, onOpenChange, onDeleted }: { c: Case; open: boolean; onOpenChange: (o: boolean) => void; onDeleted: () => void }) {
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteCase(c.id, typed);
      onOpenChange(false);
      onDeleted();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          setTyped("");
          setError(null);
        }
        onOpenChange(o);
      }}
      title="Delete this case?"
      description={
        <>
          This permanently deletes <strong>{c.name}</strong>, all {c.file_count ? `${c.file_count} ` : ""}of its files and its notes from this computer. It can't be undone; only a backup brings it back. The audit log keeps a record that it existed. To keep the case but stop changes, archive it instead.
        </>
      }
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="danger" onClick={() => void submit()} busy={busy} disabled={typed.trim() !== c.name}>Delete case</Button>
        </>
      }
    >
      <FormError message={error} />
      <Field id="confirm-name" label={`Type the case name to confirm: ${c.name}`}>
        <input id="confirm-name" className="input" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
      </Field>
    </Modal>
  );
}
