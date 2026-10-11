import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Mail, type MailFilters, type MailRow, type Mailbox, type Permissions } from "../api";
import { formatBytes, formatDate, formatDateTime } from "../format";
import { useApp, useLoad } from "../hooks";
import { href } from "../router";
import { Badge, Button, EmptyState, ErrorPanel, FormError, Icon, Link, Modal, Skeleton } from "../ui";
import { marked } from "./SearchTab";

/** Outlook mailboxes in this case: upload, search, preview, Add to case. */
export function EmailTab({ caseId, perms }: { caseId: string; perms: Permissions }) {
  const list = useLoad(() => api.mailboxes(caseId), [caseId]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [upload, setUpload] = useState<{ name: string; size: number; sent: number; ctrl: AbortController } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const items = list.data?.items ?? [];
  const busy = items.some((m) => m.status === "queued" || m.status === "indexing");

  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => void list.reload(), 2500);
    return () => clearInterval(t);
  }, [busy, list]);
  useEffect(() => {
    if (!chosen && items.length) setChosen((items.find((m) => m.status === "ready") ?? items[0]).id);
  }, [items, chosen]);

  const start = async (file: File) => {
    setProblem(null);
    const ctrl = new AbortController();
    setUpload({ name: file.name, size: file.size, sent: 0, ctrl });
    try {
      const mb = await api.uploadMailbox(caseId, file, (sent) => setUpload((u) => (u ? { ...u, sent } : u)), ctrl.signal);
      await list.reload();
      setChosen(mb.id);
    } catch (e) {
      if ((e as Error).name !== "AbortError") setProblem((e as Error).message);
    } finally {
      setUpload(null);
    }
  };

  if (list.error && !list.data) return <ErrorPanel message={list.error} onRetry={list.reload} />;
  if (!list.data) return <Skeleton rows={6} />;
  const current = items.find((m) => m.id === chosen) ?? null;

  return (
    <div className="email">
      <input ref={picker} type="file" accept=".pst,.ost" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void start(f); }} />
      <div className="email__bar">
        {items.length > 0 && (
          <label className="field field--inline">
            <span className="sr-only">Mailbox</span>
            <select className="input input--select" value={chosen ?? ""} onChange={(e) => setChosen(e.target.value)}>
              {items.map((m) => <option key={m.id} value={m.id}>{m.name}{m.status === "ready" ? ` (${m.emails.toLocaleString()} items)` : ""}</option>)}
            </select>
          </label>
        )}
        {perms.upload && <Button icon="upload" onClick={() => picker.current?.click()} disabled={!!upload}>Add a mailbox (.pst)</Button>}
        {current && <MailboxStatus m={current} />}
        {current && perms.manage && current.status !== "queued" && current.status !== "indexing" && <RemoveMailbox caseId={caseId} m={current} onRemoved={() => { setChosen(null); void list.reload(); }} />}
      </div>
      {upload && (
        <div className="progress-line" role="status">
          <Icon name="upload" />
          <span className="progress-line__text">Uploading {upload.name}: {formatBytes(upload.sent)} of {formatBytes(upload.size)}</span>
          <span className="progress-line__bar" aria-hidden="true"><span style={{ width: `${Math.round((upload.sent / Math.max(upload.size, 1)) * 100)}%` }} /></span>
          <Button size="sm" variant="quiet" onClick={() => upload.ctrl.abort()}>Cancel</Button>
        </div>
      )}
      <FormError message={problem} />
      {items.length === 0 && !upload && (
        <EmptyState icon="mail" title="No mailboxes in this case yet." action={perms.upload ? <Button variant="primary" icon="upload" onClick={() => picker.current?.click()}>Add a mailbox (.pst)</Button> : undefined}>
          Add an Outlook data file (.pst or .ost) to search every email in it, then add the ones that matter to the case. The mailbox stays in this case and only its members can search it.
        </EmptyState>
      )}
      {current?.status === "ready" && <MailboxView key={current.id} caseId={caseId} m={current} canAdd={perms.edit} onAdded={() => void list.reload()} />}
      {current?.status === "failed" && <ErrorPanel message={current.error} />}
      {current && (current.status === "queued" || current.status === "indexing") && (
        <EmptyState icon="progress" title="Reading this mailbox.">Every email is being indexed for search. You can leave this page; it carries on in the background.</EmptyState>
      )}
    </div>
  );
}

function MailboxStatus({ m }: { m: Mailbox }) {
  if (m.status === "ready") return <span className="hint">{formatBytes(m.size)} · {m.added_to_case.toLocaleString()} added to the case</span>;
  if (m.status === "failed") return <Badge tone="error" icon="error">Couldn't be read</Badge>;
  return (
    <span className="progress-line progress-line--inline" role="status">
      <Icon name="progress" />
      <span className="progress-line__text">{m.total ? `Reading ${m.done.toLocaleString()} of ${m.total.toLocaleString()} items` : "Waiting to be read"}</span>
      <span className="progress-line__bar" aria-hidden="true"><span style={{ width: `${m.total ? Math.round((m.done / m.total) * 100) : 0}%` }} /></span>
    </span>
  );
}

function RemoveMailbox({ caseId, m, onRemoved }: { caseId: string; m: Mailbox; onRemoved: () => void }) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useApp();
  return (
    <>
      <Button size="sm" variant="quiet" icon="error" onClick={() => setOpen(true)}>Remove mailbox</Button>
      <Modal open={open} onOpenChange={setOpen} title={`Remove ${m.name}?`} footer={
        <>
          <Button onClick={() => setOpen(false)}>Keep it</Button>
          <Button variant="danger" onClick={async () => {
            try {
              await api.deleteMailbox(caseId, m.id);
              toast(`${m.name} was removed.`);
              setOpen(false);
              onRemoved();
            } catch (e) {
              setError((e as Error).message);
            }
          }}>Remove mailbox</Button>
        </>
      }>
        <p>The mailbox file and its search index are deleted from this computer. The {m.added_to_case.toLocaleString()} emails already added to the case stay in the case.</p>
        <FormError message={error} />
      </Modal>
    </>
  );
}

const ROW = 60;

function MailboxView({ caseId, m, canAdd, onAdded }: { caseId: string; m: Mailbox; canAdd: boolean; onAdded: () => void }) {
  const { toast } = useApp();
  const details = useLoad(() => api.mailbox(caseId, m.id), [caseId, m.id]);
  const [text, setText] = useState("");
  const [filters, setFilters] = useState<MailFilters>({ sort: "newest" });
  const [rows, setRows] = useState<MailRow[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [withAttachments, setWithAttachments] = useState(true);
  const [adding, setAdding] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  const load = useCallback(async (offset: number, signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.searchMailbox(caseId, m.id, filters, offset, signal);
      setRows((prev) => (offset ? [...prev, ...r.items] : r.items));
      setTotal(r.total);
      if (!offset) {
        setSelected(new Set());
        setOpen(r.items[0]?.id ?? null);
        scroller.current?.scrollTo({ top: 0 });
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [caseId, m.id, filters]);

  useEffect(() => {
    const ctrl = new AbortController();
    void load(0, ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  const v = useVirtualizer({ count: rows.length, getScrollElement: () => scroller.current, estimateSize: () => ROW, overscan: 12 });
  const items = v.getVirtualItems();
  useEffect(() => {
    const last = items[items.length - 1];
    if (last && last.index >= rows.length - 20 && rows.length < total && !loading) void load(rows.length);
  }, [items, rows.length, total, loading, load]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setFilters((f) => ({ ...f, q: text.trim() || undefined, sort: text.trim() ? "relevance" : f.sort === "relevance" ? "newest" : f.sort }));
  };
  const set = (k: keyof MailFilters, val: string | boolean | undefined) => setFilters((f) => ({ ...f, [k]: val || undefined }));

  const toggle = (index: number, shift: boolean) => {
    const id = rows[index].id;
    setSelected((prev) => {
      const next = new Set(prev);
      if (shift && anchor !== null) {
        const [a, b] = [Math.min(anchor, index), Math.max(anchor, index)];
        const on = !prev.has(id);
        for (let i = a; i <= b; i++) if (!rows[i].in_case) (on ? next.add(rows[i].id) : next.delete(rows[i].id));
      } else if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setAnchor(index);
  };

  const add = async () => {
    setAdding(true);
    try {
      const r = await api.addMail(caseId, m.id, [...selected], withAttachments);
      toast(r.queued ? `Adding ${r.queued.toLocaleString()} ${r.queued === 1 ? "email" : "emails"} to the case. They appear on the Files tab under Email as they're converted.` : "Those emails are already in the case.");
      setSelected(new Set());
      onAdded();
      setTimeout(() => {
        void load(0);
        onAdded();
      }, 3000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAdding(false);
    }
  };

  const selectable = rows.filter((r) => !r.in_case);
  const allChosen = selectable.length > 0 && selectable.every((r) => selected.has(r.id));

  return (
    <div className="mailbox">
      <form className="search__form" role="search" onSubmit={submit}>
        <div className="search__row">
          <label className="search__box">
            <span className="sr-only">Search this mailbox</span>
            <Icon name="search" />
            <input className="search__input" type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder={`Search ${m.emails.toLocaleString()} items in ${m.name}`} maxLength={500} />
          </label>
          <Button type="submit" variant="primary" busy={loading && !rows.length}>Search</Button>
        </div>
        <div className="search__filters search__filters--open">
          <label className="field field--inline"><span>From</span><input className="input input--sm" value={filters.sender ?? ""} onChange={(e) => set("sender", e.target.value)} placeholder="Name or address" /></label>
          <label className="field field--inline"><span>To or Cc</span><input className="input input--sm" value={filters.to ?? ""} onChange={(e) => set("to", e.target.value)} placeholder="Name or address" /></label>
          <label className="field field--inline">
            <span>Folder</span>
            <select className="input input--select input--sm" value={filters.folder ?? ""} onChange={(e) => set("folder", e.target.value)}>
              <option value="">All folders</option>
              {details.data?.folders?.map((f) => <option key={f.path} value={f.path}>{f.path} ({f.messages.toLocaleString()})</option>)}
            </select>
          </label>
          <label className="field field--inline"><span>Sent between</span><input className="input input--sm" type="date" value={filters.date_from ?? ""} onChange={(e) => set("date_from", e.target.value)} /></label>
          <label className="field field--inline"><span>and</span><input className="input input--sm" type="date" value={filters.date_to ?? ""} onChange={(e) => set("date_to", e.target.value)} /></label>
          <label className="check-inline"><input type="checkbox" checked={!!filters.attachments} onChange={(e) => set("attachments", e.target.checked)} /> Has attachments</label>
          <label className="field field--inline">
            <span>Order</span>
            <select className="input input--select input--sm" value={filters.sort ?? "newest"} onChange={(e) => set("sort", e.target.value)}>
              {filters.q && <option value="relevance">Best match</option>}
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
          </label>
        </div>
      </form>
      <FormError message={error} />

      <div className="mailbox__split">
        <section className="mailbox__list" aria-label="Emails">
          <div className="mailbox__listhead">
            <label className="check-inline">
              <input type="checkbox" checked={allChosen} disabled={!canAdd || !selectable.length}
                onChange={(e) => setSelected(e.target.checked ? new Set(selectable.map((r) => r.id)) : new Set())} />
              <span>{total.toLocaleString()} {total === 1 ? "item" : "items"}{rows.length < total ? `, ${rows.length.toLocaleString()} loaded` : ""}</span>
            </label>
          </div>
          <div className="mailbox__scroll" ref={scroller}>
            {rows.length === 0 && !loading && <p className="hint mailbox__empty">No emails match. Try fewer words or clear a filter.</p>}
            <div style={{ height: v.getTotalSize(), position: "relative" }}>
              {items.map((it) => {
                const r = rows[it.index];
                return (
                  <div key={r.id} className={`mailrow${open === r.id ? " mailrow--open" : ""}${selected.has(r.id) ? " mailrow--chosen" : ""}`} style={{ transform: `translateY(${it.start}px)`, height: ROW }}>
                    <input type="checkbox" aria-label={`Choose ${r.subject || "(no subject)"}`} checked={selected.has(r.id)} disabled={!canAdd || !!r.in_case}
                      onClick={(e) => { e.preventDefault(); toggle(it.index, e.shiftKey); }} onChange={() => undefined} />
                    <button className="mailrow__main" onClick={() => setOpen(r.id)}>
                      <span className="mailrow__top">
                        <span className="mailrow__from truncate">{r.sender || "(no sender)"}</span>
                        <span className="mailrow__date">{formatDate(r.date)}</span>
                      </span>
                      <span className="mailrow__subject truncate">
                        {r.attachments > 0 && <Icon name="clip" />}
                        {r.in_case && <span className="chip chip--sm">In case</span>}
                        {r.kind !== "email" && <span className="chip chip--sm">{r.kind}</span>}
                        <span className="truncate">{r.subject || "(no subject)"}</span>
                      </span>
                      <span className="mailrow__snippet truncate">{marked(r.snippet)}</span>
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
          {canAdd && selected.size > 0 && (
            <div className="mailbox__actions" role="region" aria-label="Add to case">
              <strong>{selected.size.toLocaleString()} chosen</strong>
              <label className="check-inline"><input type="checkbox" checked={withAttachments} onChange={(e) => setWithAttachments(e.target.checked)} /> Include attachments</label>
              <Button variant="primary" icon="plus" busy={adding} onClick={() => void add()}>Add to case</Button>
            </div>
          )}
        </section>
        <section className="mailbox__preview" aria-label="Email">
          {open !== null ? <Preview key={open} caseId={caseId} mailbox={m.id} id={open} terms={filters.q} /> : <p className="hint">Choose an email to read it here.</p>}
        </section>
      </div>
    </div>
  );
}

function Preview({ caseId, mailbox, id, terms }: { caseId: string; mailbox: string; id: number; terms?: string }) {
  const { data, error } = useLoad<Mail>(() => api.mail(caseId, mailbox, id), [caseId, mailbox, id]);
  if (error) return <ErrorPanel message={error} />;
  if (!data) return <Skeleton rows={6} />;
  const words = (terms ?? "").replace(/["*]/g, " ").split(/\s+/).filter((w) => w.length > 2 && w !== "OR" && !w.startsWith("-"));
  const body = words.length
    ? marked(data.body.replace(new RegExp(`\\b(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\w*`, "gi"), "\u0002$&\u0003"))
    : data.body;
  return (
    <article className="mail">
      <h2 className="mail__subject">{data.subject || "(no subject)"}</h2>
      {data.in_case && <p className="banner banner--idle"><Icon name="ok" /> In the case. <Link to={href({ page: "document", id: caseId, fileId: data.in_case, page_no: 1 })}>Open it</Link></p>}
      <dl className="kv">
        <dt>From</dt><dd className="break">{data.sender || "Not recorded"}</dd>
        <dt>To</dt><dd className="break">{data.to.join("; ") || "Not recorded"}</dd>
        {data.cc.length > 0 && <><dt>Cc</dt><dd className="break">{data.cc.join("; ")}</dd></>}
        <dt>Sent</dt><dd>{formatDateTime(data.date) || "Not recorded"}</dd>
        <dt>Folder</dt><dd className="break">{data.folder || "Top level"}</dd>
        {data.attachments.length > 0 && (
          <>
            <dt>Attachments</dt>
            <dd className="kv__stack">{data.attachments.map((a, i) => <span key={i}><Icon name="clip" /> {a.name} <span className="muted">{a.embedded ? "attached email" : formatBytes(a.size)}</span></span>)}</dd>
          </>
        )}
      </dl>
      <div className="mail__body">{body}</div>
    </article>
  );
}
