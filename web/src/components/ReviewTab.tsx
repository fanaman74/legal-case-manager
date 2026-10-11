import { useEffect, useState, type FormEvent } from "react";
import { api, type AiCall, type AiKind, type AiScope } from "../api";
import { formatDateTime } from "../format";
import { useApp, useLoad } from "../hooks";
import { Button, ErrorPanel, Icon, Skeleton } from "../ui";
import { AiBlock, NoProviders, ProviderBadge, useAiRequest } from "./Ai";

type ReviewKind = Exclude<AiKind, "ask">;

const TASKS: { id: ReviewKind; label: string; hint: string }[] = [
  { id: "summary", label: "Summarise", hint: "What the documents are, who is involved, what happened." },
  { id: "chronology", label: "Chronology", hint: "Dated events in order, each with its source." },
  { id: "relevance", label: "Check relevance to an issue", hint: "Which passages support or cut against an issue you describe." },
  { id: "extract", label: "Extract parties, dates and amounts", hint: "Three lists, each item cited." },
  { id: "tags", label: "Suggest tags and document type", hint: "Suggestions only: you accept each tag." },
];

function scopeText(scope: AiScope, fileName?: string): string {
  if (scope.type === "folder") return `Folder ${scope.folder}`;
  if (scope.type === "file") return fileName ?? "One file";
  return "Whole case";
}

/** Reviews of a file, a folder or the whole case. Run only when someone asks,
 * and saved for everyone in the case. */
export function ReviewTab({ caseId, canEdit, file }: { caseId: string; canEdit: boolean; file?: string }) {
  const { user } = useApp();
  const choices = useLoad(() => api.aiChoices(), []);
  const history = useLoad(() => api.aiHistory(caseId, "review"), [caseId]);
  const folders = useLoad(() => api.folders(caseId), [caseId]);
  const files = useLoad(() => api.files(caseId, { limit: 1000, sort: "name", order: "asc" }), [caseId]);
  const [task, setTask] = useState<ReviewKind>("summary");
  const [scopeKey, setScopeKey] = useState(file ? "file" : "case");
  const [fileId, setFileId] = useState(file ?? "");
  const [issue, setIssue] = useState("");
  const [provider, setProvider] = useState("");
  const [busy, setBusy] = useState<AiCall | "preparing" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const { run, dialog } = useAiRequest(caseId);

  useEffect(() => {
    if (choices.data && !provider) setProvider(choices.data.defaults.review);
  }, [choices.data, provider]);

  if (choices.error) return <ErrorPanel message={choices.error} onRetry={() => void choices.reload()} />;
  if (!choices.data) return <Skeleton rows={4} />;
  if (!choices.data.providers.length) return <NoProviders isAdmin={user.role === "admin"} />;
  const chosen = choices.data.providers.find((p) => p.id === provider) ?? choices.data.providers[0];
  const ready = (files.data?.items ?? []).filter((f) => f.search?.state === "indexed" || f.search?.state === "keyword_only");
  const names = Object.fromEntries((files.data?.items ?? []).map((f) => [f.id, f.folder ? `${f.folder}/${f.name}` : f.name]));

  const scope = (): AiScope =>
    scopeKey === "file" ? { type: "file", file_id: fileId } : scopeKey.startsWith("folder:") ? { type: "folder", folder: scopeKey.slice(7) } : { type: "case" };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy("preparing");
    try {
      const call = await run({ kind: task, question: task === "relevance" ? issue : "", scope: scope(), provider: chosen.id }, (c) => setBusy(c));
      if (call) {
        history.setData((h) => ({ items: [call, ...(h?.items ?? []).filter((x) => x.id !== call.id)] }));
        setOpenId(call.id);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const accept = async (call: AiCall, fid: string, tag: string) => {
    try {
      const next = await api.aiAccept(caseId, call.id, fid, tag);
      history.setData((h) => ({ items: (h?.items ?? []).map((x) => (x.id === next.id ? next : x)) }));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const items = history.data?.items ?? [];
  const blocked = task === "relevance" && issue.trim().length < 3 || scopeKey === "file" && !fileId;

  return (
    <div className="review">
      <form className="review__form" onSubmit={submit}>
        <fieldset className="review__tasks">
          <legend className="eyebrow">What to do</legend>
          {TASKS.map((t) => (
            <label key={t.id} className={`task${task === t.id ? " task--on" : ""}`}>
              <input type="radio" name="task" value={t.id} checked={task === t.id} onChange={() => setTask(t.id)} />
              <span className="task__label">{t.label}</span>
              <span className="task__hint">{t.hint}</span>
            </label>
          ))}
        </fieldset>
        <div className="review__settings">
          {task === "relevance" && (
            <label className="field">
              <span className="field__label">The issue</span>
              <textarea className="input textarea" rows={3} value={issue} onChange={(e) => setIssue(e.target.value)} maxLength={2000}
                placeholder="For example: Did the Defendant know about the delay before 1 March 2024?" />
            </label>
          )}
          <label className="field">
            <span className="field__label">Documents</span>
            <select className="input input--select" value={scopeKey} onChange={(e) => setScopeKey(e.target.value)}>
              <option value="case">The whole case</option>
              {folders.data?.items.map((f) => <option key={f.path} value={`folder:${f.path}`}>Folder: {f.path}</option>)}
              <option value="file">One file…</option>
            </select>
          </label>
          {scopeKey === "file" && (
            <label className="field">
              <span className="field__label">File</span>
              <select className="input input--select" value={fileId} onChange={(e) => setFileId(e.target.value)}>
                <option value="">Choose a file</option>
                {ready.map((f) => <option key={f.id} value={f.id}>{names[f.id]}</option>)}
              </select>
            </label>
          )}
          <label className="field">
            <span className="field__label">AI service</span>
            <select className="input input--select" value={chosen.id} onChange={(e) => setProvider(e.target.value)} disabled={choices.data.providers.length < 2}>
              {choices.data.providers.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.model})</option>)}
            </select>
          </label>
          <div className="review__go">
            <ProviderBadge p={chosen} />
            <Button type="submit" variant="primary" icon="spark" busy={!!busy} disabled={blocked}>Run review</Button>
          </div>
          {busy && (
            <p className="hint" role="status">
              {busy === "preparing" ? "Choosing the passages to include…" : `Waiting for ${busy.provider.name}: reading ${busy.sources.length} passages. Long reviews can take a few minutes.`}
            </p>
          )}
        </div>
      </form>
      {error && <ErrorPanel message={error} />}

      <section className="review__history" aria-label="Review history">
        <h2 className="h3">Review history</h2>
        {history.loading && !history.data ? (
          <Skeleton rows={3} />
        ) : items.length === 0 ? (
          <p className="muted">No reviews yet. Reviews run only when someone asks, and everyone in the case can read them here.</p>
        ) : (
          <ul className="review__list">
            {items.map((c, i) => {
              const open = openId ? openId === c.id : i === 0;
              return (
                <li key={c.id} className="review__item">
                  <button type="button" className="review__title" aria-expanded={open} onClick={() => setOpenId(open ? "" : c.id)}>
                    <Icon name={c.state === "failed" ? "error" : "spark"} />
                    <span className="review__name">{c.label}</span>
                    <span className="review__scope">{scopeText(c.scope, c.scope.type === "file" ? names[c.scope.file_id] : undefined)}{c.kind === "relevance" ? `: “${c.question}”` : ""}</span>
                    <span className="review__who">{c.username} · {formatDateTime(c.created_at)}</span>
                  </button>
                  {open && (
                    <AiBlock call={c} caseId={caseId}>
                      {c.suggestions && <Suggestions call={c} canEdit={canEdit} onAccept={(fid, tag) => void accept(c, fid, tag)} />}
                    </AiBlock>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
      {dialog}
    </div>
  );
}

function Suggestions({ call, canEdit, onAccept }: { call: AiCall; canEdit: boolean; onAccept: (fileId: string, tag: string) => void }) {
  return (
    <div className="suggest">
      {!canEdit && <p className="hint">Your role can't change tags in this case, so these are for reading only.</p>}
      <ul className="suggest__list">
        {call.suggestions!.map((s) => {
          const done = call.accepted[s.file_id] ?? [];
          const tags = [...(s.document_type ? [s.document_type] : []), ...s.tags.filter((t) => t !== s.document_type)];
          return (
            <li key={s.file_id} className="suggest__file">
              <p className="suggest__name"><Icon name="file" />{s.name}{s.document_type && <span className="muted"> · looks like: {s.document_type}</span>}</p>
              <div className="suggest__tags">
                {tags.map((t) =>
                  done.includes(t) ? (
                    <span key={t} className="chip chip--done"><Icon name="ok" />{t}</span>
                  ) : (
                    <span key={t} className="chip chip--suggested">
                      {t}
                      {canEdit && <button type="button" className="chip__accept" onClick={() => onAccept(s.file_id, t)} aria-label={`Add the tag ${t} to ${s.name}`}>Accept</button>}
                    </span>
                  ),
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
