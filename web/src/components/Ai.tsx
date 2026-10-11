// Shared pieces for Ask and Review: where a request goes (provider badge),
// the confirmation step before anything leaves this computer, and the
// AI-generated block with numbered citations.
import { useCallback, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { api, type AiCall, type AiKind, type AiProviderChoice, type AiScope, type AiSource } from "../api";
import { formatDateTime, plural } from "../format";
import { renderMarkdown } from "../markdown";
import { href } from "../router";
import { Badge, Button, EmptyState, Icon, Link, Modal } from "../ui";

export function ProviderBadge({ p }: { p: Pick<AiProviderChoice, "external" | "destination"> }) {
  return p.external ? (
    <Badge tone="warn" icon="external" title="The passages are sent over the internet to this company.">Leaves this machine: {p.destination}</Badge>
  ) : (
    <Badge tone="ok" icon="lock" title="Nothing leaves this computer.">On this computer</Badge>
  );
}

/** Group passages by document, keeping their order. */
function byDocument(sources: AiSource[]): { file_id: string; name: string; path: string; items: AiSource[] }[] {
  const out: { file_id: string; name: string; path: string; items: AiSource[] }[] = [];
  for (const s of sources) {
    let d = out.find((x) => x.file_id === s.file_id);
    if (!d) out.push((d = { file_id: s.file_id, name: s.name, path: s.path, items: [] }));
    d.items.push(s);
  }
  return out;
}

const where = (s: AiSource) => [s.page ? `page ${s.page}` : "", s.heading].filter(Boolean).join(" · ");

/** The confirmation step. Shown before every request to a service off this
 * computer; there is no way to skip it. */
function ConfirmSend({ call, onAnswer }: { call: AiCall; onAnswer: (send: boolean) => void }) {
  const docs = byDocument(call.sources);
  const chars = call.sources.reduce((n, s) => n + s.text.length, 0);
  return (
    <Modal
      open
      onOpenChange={(o) => !o && onAnswer(false)}
      title={`Send to ${call.provider.name}?`}
      description={<>{call.kind === "ask" ? "To answer this question" : `For this ${call.label.toLowerCase()}`}, these passages will be sent to {call.provider.name} ({call.provider.model}).</>}
      wide
      footer={
        <>
          <Button onClick={() => onAnswer(false)}>Cancel</Button>
          <Button variant="primary" icon="send" onClick={() => onAnswer(true)}>Send to {call.provider.destination}</Button>
        </>
      }
    >
      <div className="gate">
        <p className="gate__warning"><Icon name="warn" /><span>Sends data to {call.provider.destination}. These passages leave this computer and are handled under {call.provider.destination}'s terms.</span></p>
        <p className="gate__summary">
          <strong>{plural(call.sources.length, "passage")} from {plural(docs.length, "file")}</strong>
          <span className="muted"> · about {Math.max(1, Math.round(chars / 1000)).toLocaleString()}k characters</span>
        </p>
        {call.note && <p className="hint">{call.note}</p>}
        <ul className="gate__files">
          {docs.map((d) => (
            <li key={d.file_id}>
              <details>
                <summary>
                  <Icon name="file" />
                  <span className="gate__name">{d.path || d.name}</span>
                  <span className="muted">{plural(d.items.length, "passage")}</span>
                </summary>
                <ol className="gate__passages">
                  {d.items.map((s) => (
                    <li key={s.n}>
                      <p className="gate__where"><span className="cite cite--static">{s.n}</span>{where(s) || "Passage"}</p>
                      <p className="gate__text">{s.text}</p>
                    </li>
                  ))}
                </ol>
              </details>
            </li>
          ))}
        </ul>
      </div>
    </Modal>
  );
}

/** Prepare, confirm when it leaves this computer, send. Returns the
 * finished request, or null if the person cancelled. */
export function useAiRequest(caseId: string) {
  const [gate, setGate] = useState<{ call: AiCall; answer: (send: boolean) => void } | null>(null);
  const run = useCallback(
    async (body: { kind: AiKind; question?: string; scope?: AiScope; provider?: string }, onPrepared?: (call: AiCall) => void): Promise<AiCall | null> => {
      const call = await api.aiPrepare(caseId, body);
      if (call.provider.external) {
        const send = await new Promise<boolean>((answer) => setGate({ call, answer }));
        setGate(null);
        if (!send) {
          await api.aiCancel(caseId, call.id).catch(() => undefined);
          return null;
        }
      }
      onPrepared?.(call);
      return api.aiSend(caseId, call.id, call.provider.external);
    },
    [caseId],
  );
  const dialog = gate ? <ConfirmSend call={gate.call} onAnswer={gate.answer} /> : null;
  return { run, dialog };
}

const CITE = /\[(\d{1,4}(?:\s*[,;–-]\s*\d{1,4})*)\]/g;

/** Numbers in a citation like [2, 4] or [5–7]. */
export function citeNumbers(group: string): number[] {
  const out: number[] = [];
  for (const part of group.split(/\s*[,;]\s*/)) {
    const [lo, hi] = part.split(/\s*[–-]\s*/).map(Number);
    if (hi !== undefined && hi >= lo && hi - lo < 50) for (let n = lo; n <= hi; n++) out.push(n);
    else out.push(lo);
  }
  return out;
}

/** The answer as HTML, with each [n] turned into a link to source n. Only
 * text between tags is touched; markdown-it has already escaped it. */
export function citedHtml(answer: string, count: number): string {
  return renderMarkdown(answer)
    .split(/(<[^>]+>)/)
    .map((part) =>
      part.startsWith("<")
        ? part
        : part.replace(CITE, (whole, group: string) => {
            const nums = citeNumbers(group).filter((n) => n >= 1 && n <= count);
            if (!nums.length) return whole;
            return `<span class="cites">${nums.map((n) => `<a href="#" class="cite" data-cite="${n}" aria-label="Source ${n}">${n}</a>`).join("")}</span>`;
          }),
    )
    .join("");
}

/** An AI answer: dashed teal outline, always labelled, with its sources. */
export function AiBlock({ call, caseId, children }: { call: AiCall; caseId: string; children?: ReactNode }) {
  const [open, setOpen] = useState<number | null>(null);
  const [all, setAll] = useState(false);
  // Without citations (tag suggestions), the passages sent stay folded away.
  const [folded, setFolded] = useState(call.cited.length === 0);
  const list = useRef<HTMLOListElement>(null);
  const cited = call.cited.length ? call.sources.filter((s) => call.cited.includes(s.n)) : [];
  const shown = all || !cited.length ? call.sources : cited;

  const onClick = (e: MouseEvent) => {
    const a = (e.target as HTMLElement).closest("a[data-cite]");
    if (!a) return;
    e.preventDefault();
    const n = Number(a.getAttribute("data-cite"));
    if (!cited.some((s) => s.n === n)) setAll(true);
    setOpen(n);
    requestAnimationFrame(() => list.current?.querySelector(`[data-source="${n}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
  };

  return (
    <article className="aiblock" aria-label={`${call.label}: AI-generated, unverified`}>
      <header className="aiblock__head">
        <span className="aiblock__label"><Icon name="spark" />AI-generated, unverified</span>
        <ProviderBadge p={call.provider} />
        <span className="aiblock__meta">{call.served_model || call.provider.model} · {formatDateTime(call.finished_at || call.created_at)}</span>
      </header>
      {call.state === "failed" ? (
        <p className="aiblock__error"><Icon name="error" /><span>{call.error}</span></p>
      ) : (
        <>
          {/* Rendered by markdown-it with HTML turned off: the answer can't add markup of its own. */}
          {call.kind !== "tags" && (
            <div className="aiblock__answer reader reader--compact" onClick={onClick} dangerouslySetInnerHTML={{ __html: citedHtml(call.answer, call.sources.length) }} />
          )}
          {call.cut_short && <p className="hint">The answer was cut short because it reached the length limit. Try a smaller scope.</p>}
          {children}
        </>
      )}
      {call.note && <p className="hint">{call.note}</p>}
      <section className="sources" aria-label="Sources">
        {!folded && <h3 className="eyebrow">{all || !cited.length ? `All ${plural(call.sources.length, "passage")} sent` : `${plural(cited.length, "source")} cited`}</h3>}
        {folded ? (
          <Button size="sm" variant="quiet" onClick={() => setFolded(false)}>Show the {plural(call.sources.length, "passage")} sent</Button>
        ) : (
          <ol className="sources__list" ref={list}>
            {shown.map((s) => (
              <li key={s.n} data-source={s.n} className={open === s.n ? "source source--open" : "source"}>
                <button type="button" className="source__toggle" aria-expanded={open === s.n} onClick={() => setOpen(open === s.n ? null : s.n)}>
                  <span className="cite cite--static">{s.n}</span>
                  <span className="source__name">{s.name}</span>
                  {where(s) && <span className="source__where">{where(s)}</span>}
                </button>
                {open === s.n && (
                  <div className="source__body">
                    <p className="source__text">{s.text}</p>
                    <Link to={href({ page: "document", id: caseId, fileId: s.file_id, page_no: s.page ?? 1 })} className="source__open">
                      Open {s.page ? `page ${s.page} of ` : ""}the document
                    </Link>
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
        {cited.length > 0 && cited.length < call.sources.length && (
          <Button size="sm" variant="quiet" onClick={() => setAll(!all)}>{all ? "Show only cited sources" : `Show all ${plural(call.sources.length, "passage")} sent`}</Button>
        )}
      </section>
    </article>
  );
}

export function NoProviders({ isAdmin }: { isAdmin: boolean }) {
  return (
    <EmptyState icon="spark" title="No AI service is set up yet">
      {isAdmin ? (
        <>Choose one in <Link to="/settings">Settings</Link>: a model on this computer, or an online service such as Claude.</>
      ) : (
        "Ask the Admin to set one up in Settings. Until then, Search finds passages by exact words and by meaning."
      )}
    </EmptyState>
  );
}
