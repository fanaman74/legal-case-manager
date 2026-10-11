import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, type AiCall } from "../api";
import { useApp, useLoad } from "../hooks";
import { Button, ErrorPanel, Icon, Skeleton } from "../ui";
import { AiBlock, NoProviders, ProviderBadge, useAiRequest } from "./Ai";

/** Ask a question about the case. Answers cite the passages they came
 * from; the passages are found by the case's own search. Each person's
 * questions are their own. */
export function AskTab({ caseId }: { caseId: string }) {
  const { user } = useApp();
  const choices = useLoad(() => api.aiChoices(), []);
  const past = useLoad(() => api.aiHistory(caseId, "ask"), [caseId]);
  const folders = useLoad(() => api.folders(caseId), [caseId]);
  const [asked, setAsked] = useState<AiCall[]>([]);
  const [pending, setPending] = useState<{ question: string; call: AiCall | null } | null>(null);
  const [question, setQuestion] = useState("");
  const [provider, setProvider] = useState("");
  const [folder, setFolder] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { run, dialog } = useAiRequest(caseId);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (choices.data && !provider) setProvider(choices.data.defaults.ask);
  }, [choices.data, provider]);
  const thread = [...(past.data?.items ?? [])].reverse().filter((c) => !asked.some((a) => a.id === c.id)).concat(asked);
  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [thread.length, pending]);

  if (choices.error) return <ErrorPanel message={choices.error} onRetry={() => void choices.reload()} />;
  if (!choices.data || past.loading && !past.data) return <Skeleton rows={4} />;
  if (!choices.data.providers.length) return <NoProviders isAdmin={user.role === "admin"} />;
  const chosen = choices.data.providers.find((p) => p.id === provider) ?? choices.data.providers[0];

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    const q = question.trim();
    if (q.length < 3 || pending) return;
    setError(null);
    setPending({ question: q, call: null });
    try {
      const call = await run({ kind: "ask", question: q, provider: chosen.id, scope: folder ? { type: "folder", folder } : { type: "case" } }, (c) => setPending({ question: q, call: c }));
      if (call) {
        setAsked((a) => [...a, call]);
        setQuestion("");
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(null);
      box.current?.focus();
    }
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  };

  return (
    <div className="ask">
      <div className="ask__thread" aria-live="polite">
        {thread.length === 0 && !pending && (
          <div className="ask__intro">
            <h2 className="h3">Ask about this case</h2>
            <p className="muted">Answers come only from the case's documents, with numbered citations you can check. They are AI-generated, so check the cited passages before relying on them.</p>
            <p className="hint">For example: <em>When was the steel due to arrive?</em> · <em>What did the haulier say about the delay?</em></p>
          </div>
        )}
        {thread.map((c) => (
          <div key={c.id} className="ask__turn">
            <p className="ask__question">{c.question}</p>
            <AiBlock call={c} caseId={caseId} />
          </div>
        ))}
        {pending && (
          <div className="ask__turn">
            <p className="ask__question">{pending.question}</p>
            <p className="aiblock aiblock--waiting" role="status">
              <Icon name="progress" />
              {pending.call ? `Waiting for ${pending.call.provider.name}: reading ${pending.call.sources.length} passages…` : "Finding the passages that answer this…"}
            </p>
          </div>
        )}
        <div ref={end} />
      </div>

      <form className="ask__box" onSubmit={submit}>
        {error && <ErrorPanel message={error} />}
        <label className="sr-only" htmlFor="ask-q">Your question</label>
        <textarea
          id="ask-q"
          ref={box}
          className="input textarea ask__input"
          rows={2}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKey}
          placeholder="Ask a question about this case's documents"
          maxLength={2000}
          autoFocus
        />
        <div className="ask__options">
          {choices.data.providers.length > 1 ? (
            <label className="field field--inline">
              <span>Using</span>
              <select className="input input--select input--sm" value={chosen.id} onChange={(e) => setProvider(e.target.value)}>
                {choices.data.providers.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.model})</option>)}
              </select>
            </label>
          ) : (
            <span className="hint">Using {chosen.name} ({chosen.model})</span>
          )}
          <ProviderBadge p={chosen} />
          <label className="field field--inline">
            <span>In</span>
            <select className="input input--select input--sm" value={folder} onChange={(e) => setFolder(e.target.value)}>
              <option value="">The whole case</option>
              {folders.data?.items.map((f) => <option key={f.path} value={f.path}>{f.path}</option>)}
            </select>
          </label>
          <Button type="submit" variant="primary" icon="send" busy={!!pending} disabled={question.trim().length < 3}>Ask</Button>
        </div>
      </form>
      {dialog}
    </div>
  );
}
