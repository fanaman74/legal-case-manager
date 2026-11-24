import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type CaseStatus, type FileStage, type StatusItem } from "../api";
import { formatDateTime, plural, typeLabel } from "../format";
import { href } from "../router";
import { Badge, ErrorPanel, Icon, Link, Skeleton } from "../ui";
import { OCR_MISSING } from "./FileStatus";

// Processing status: where every file in the case is in the pipeline that
// runs by itself after upload: conversion to Markdown, OCR of scanned pages,
// the exact-words index and the meaning index (embeddings by the local model).

const SHOW: { id: "all" | FileStage; label: string }[] = [
  { id: "all", label: "All files" },
  { id: "attention", label: "Needs attention" },
  { id: "working", label: "In progress" },
  { id: "ready", label: "Ready" },
];

export function StatusTab({ caseId }: { caseId: string }) {
  const [show, setShow] = useState<"all" | FileStage>("all");
  const [data, setData] = useState<CaseStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const n = ++seq.current;
    try {
      const d = await api.caseStatus(caseId, show);
      if (n !== seq.current) return;
      setData(d);
      setError(null);
    } catch (e) {
      if (n === seq.current) setError((e as Error).message);
    }
  }, [caseId, show]);

  const working = data?.summary.stages.working ?? 0;
  useEffect(() => {
    void load();
    // Quick updates while files are moving through; slow ones once settled.
    const t = setInterval(() => void load(), working > 0 ? 3000 : 20000);
    return () => clearInterval(t);
  }, [load, working]);

  if (error && !data) return <ErrorPanel message={error} onRetry={() => void load()} />;
  if (!data) return <Skeleton rows={8} />;
  const s = data.summary;
  if (s.total === 0) {
    return (
      <div className="empty">
        <Icon name="file" />
        <p className="empty__title">No files yet.</p>
        <p className="empty__text">Once files are uploaded, this page shows each one being converted to Markdown, read with OCR where it's scanned, and indexed for search and Ask.</p>
      </div>
    );
  }

  return (
    <div className="status">
      <Headline data={data} />
      <div className="status__cards">
        <Card
          title="Converted to Markdown"
          done={s.markdown.done}
          of={s.total}
          detail={[
            s.markdown.working > 0 && `${plural(s.markdown.working, "file")} converting`,
            s.markdown.failed > 0 && <span className="tone--error">{plural(s.markdown.failed, "file")} failed</span>,
          ]}
        />
        <Card
          title="Scanned pages read (OCR)"
          done={s.ocr.done}
          of={s.ocr.needed}
          none="No scanned pages found."
          detail={[
            s.ocr.working > 0 && `${plural(s.ocr.working, "file")} being read`,
            s.ocr.unavailable > 0 && <span className="tone--warn" title={OCR_MISSING}>{plural(s.ocr.unavailable, "file")} wait for OCR to be installed</span>,
            s.ocr.failed > 0 && <span className="tone--warn">{plural(s.ocr.failed, "file")} couldn't be read</span>,
          ]}
        />
        <Card
          title="Search by exact words"
          done={s.keyword.done}
          of={s.markdown.done}
          detail={[s.keyword.working > 0 && `${plural(s.keyword.working, "file")} being indexed`]}
        />
        <Card
          title="Search by meaning (embeddings)"
          done={s.meaning.done}
          of={s.markdown.done}
          detail={[
            s.meaning.working > 0 && `${plural(s.meaning.working, "file")} being embedded`,
            s.meaning.waiting_model > 0 && <span className="tone--warn">{plural(s.meaning.waiting_model, "file")} wait for the local AI model</span>,
            s.meaning.failed > 0 && <span className="tone--error">{plural(s.meaning.failed, "file")} failed</span>,
            `${s.passages.toLocaleString()} passages · model ${data.model}`,
          ]}
        />
      </div>

      <div className="toolbar">
        <div className="segmented" role="radiogroup" aria-label="Which files">
          {SHOW.map((o) => (
            <button key={o.id} type="button" role="radio" aria-checked={show === o.id} aria-pressed={show === o.id} className="segmented__item" onClick={() => setShow(o.id)}>
              {o.label} <span className="tabular muted">{(o.id === "all" ? s.total : s.stages[o.id]).toLocaleString()}</span>
            </button>
          ))}
        </div>
        <span className="toolbar__count muted" aria-live="polite">{working > 0 ? "Updates every few seconds." : "Updates by itself."}</span>
      </div>
      {error && <ErrorPanel message={error} onRetry={() => void load()} />}
      {data.items.length === 0 ? (
        <p className="empty-inline">{show === "attention" ? "Nothing needs attention." : show === "working" ? "Nothing in progress." : "No files here yet."}</p>
      ) : (
        <StatusTable caseId={caseId} items={data.items} matching={data.matching} />
      )}
    </div>
  );
}

function Headline({ data }: { data: CaseStatus }) {
  const s = data.summary;
  if (s.stages.ready === s.total) {
    return (
      <p className="banner banner--ok" role="status">
        <Icon name="ok" /> All {plural(s.total, "file")} are converted, indexed and embedded. Search and Ask use every one of them.
      </p>
    );
  }
  const parts: string[] = [];
  if (s.markdown.working) parts.push(`${plural(s.markdown.working, "file")} converting`);
  if (s.ocr.working) parts.push(`${plural(s.ocr.working, "file")} with scans being read`);
  if (s.keyword.working || s.meaning.working) parts.push(`${plural(Math.max(s.keyword.working, s.meaning.working), "file")} being indexed`);
  if (s.meaning.waiting_model) parts.push(`${plural(s.meaning.waiting_model, "file")} waiting for the local AI model`);
  return (
    <p className={`banner ${s.stages.attention ? "banner--warn" : "banner--info"}`} role="status">
      <Icon name={s.stages.attention ? "warn" : "progress"} />
      <span>
        {s.stages.ready.toLocaleString()} of {plural(s.total, "file")} fully ready.
        {parts.length > 0 && ` ${parts.join(", ")}. This runs by itself.`}
        {s.stages.attention > 0 && ` ${plural(s.stages.attention, "file")} ${s.stages.attention === 1 ? "needs" : "need"} attention.`}
        {s.meaning.waiting_model > 0 && " Meaning search starts once the local AI model is running (Control Center, Components)."}
      </span>
    </p>
  );
}

function Card({ title, done, of, none, detail }: { title: string; done: number; of: number; none?: string; detail: ReactNode[] }) {
  const pct = of ? Math.round((done / of) * 100) : 100;
  const lines = detail.filter(Boolean);
  return (
    <section className="statcard" aria-label={title}>
      <h3 className="statcard__title">{title}</h3>
      {of === 0 && none ? (
        <p className="statcard__value statcard__value--none">{none}</p>
      ) : (
        <>
          <p className="statcard__value tabular">
            {done.toLocaleString()} <span className="muted">of {of.toLocaleString()}</span>
            {of > 0 && done === of && <Icon name="ok" label="complete" />}
          </p>
          <span className={`progress-line__bar${done === of ? " progress-line__bar--done" : ""}`} aria-hidden="true"><span style={{ width: `${pct}%` }} /></span>
        </>
      )}
      {lines.length > 0 && <ul className="statcard__detail">{lines.map((l, i) => <li key={i}>{l}</li>)}</ul>}
    </section>
  );
}

// ---------------------------------------------------------------------------

function StatusTable({ caseId, items, matching }: { caseId: string; items: StatusItem[]; matching: number }) {
  const scroller = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({ count: items.length, getScrollElement: () => scroller.current, estimateSize: () => 36, overscan: 12 });
  const rows = v.getVirtualItems();
  const padTop = rows.length ? rows[0].start : 0;
  const padBottom = rows.length ? v.getTotalSize() - rows[rows.length - 1].end : 0;
  return (
    <div className="table-wrap table-wrap--scroll" ref={scroller}>
      <table className="table table--compact table--sticky status-table">
        <thead>
          <tr>
            <th scope="col">File</th>
            <th scope="col">Folder</th>
            <th scope="col">Markdown</th>
            <th scope="col">Scanned pages (OCR)</th>
            <th scope="col">Exact words</th>
            <th scope="col">Meaning (embeddings)</th>
            <th scope="col" className="num">Passages</th>
            <th scope="col">Last processed</th>
          </tr>
        </thead>
        <tbody>
          {padTop > 0 && <tr aria-hidden="true"><td colSpan={8} style={{ height: padTop, padding: 0, border: 0 }} /></tr>}
          {rows.map((r) => {
            const f = items[r.index];
            const converted = f.status === "converted" || f.status === "needs_ocr";
            return (
              <tr key={f.id}>
                <th scope="row" className="cell-name">
                  <div className="cell-name__inner">
                    {converted ? (
                      <Link className="linkish" to={href({ page: "document", id: caseId, fileId: f.id, page_no: 1 })} title={f.name}>
                        <Icon name={f.type === "email" ? "mail" : "file"} />
                        <span className="truncate">{f.name}</span>
                      </Link>
                    ) : (
                      <span className="cell-name__plain" title={f.name}><Icon name={f.type === "email" ? "mail" : "file"} /><span className="truncate">{f.name}</span></span>
                    )}
                    {f.stage === "attention" && <span className="sr-only">Needs attention</span>}
                  </div>
                </th>
                <td data-label="Folder" className="cell-folder"><span className="truncate" title={f.folder}>{f.folder || <span className="muted">Top level</span>}</span></td>
                <td data-label="Markdown" className="nowrap"><MarkdownCell f={f} /></td>
                <td data-label="Scanned pages (OCR)" className="nowrap"><OcrCell f={f} /></td>
                <td data-label="Exact words" className="nowrap"><KeywordCell f={f} /></td>
                <td data-label="Meaning (embeddings)" className="nowrap"><MeaningCell f={f} /></td>
                <td data-label="Passages" className="num tabular">{converted && f.index_state ? f.passages.toLocaleString() : <span className="muted">–</span>}</td>
                <td data-label="Last processed" className="nowrap muted">{formatDateTime(f.indexed_at ?? f.converted_at) || "–"}</td>
              </tr>
            );
          })}
          {padBottom > 0 && <tr aria-hidden="true"><td colSpan={8} style={{ height: padBottom, padding: 0, border: 0 }} /></tr>}
        </tbody>
      </table>
      {items.length < matching && <p className="table-more">Showing the first {items.length.toLocaleString()} of {matching.toLocaleString()} files.</p>}
    </div>
  );
}

const Dash = () => <span className="muted">–</span>;

function MarkdownCell({ f }: { f: StatusItem }) {
  switch (f.status) {
    case "queued":
      return <Badge tone="idle" icon="pending">Waiting</Badge>;
    case "converting":
      return <Badge tone="progress" icon="progress">Converting</Badge>;
    case "failed":
      return <Badge tone="error" icon="error" title={f.error}>Failed</Badge>;
    default:
      if (f.warnings.length) return <Badge tone="warn" icon="warn" title={f.warnings.join(" ")}>Done, {plural(f.warnings.length, "warning")}</Badge>;
      return <Badge tone="ok" icon="ok" title={[`${typeLabel[f.type]}${f.page_count ? `, ${plural(f.page_count, "page")}` : ""}.`, ...f.notes].join(" ")}>Done</Badge>;
  }
}

function OcrCell({ f }: { f: StatusItem }) {
  switch (f.ocr_state) {
    case "waiting":
      return <Badge tone="idle" icon="pending" title={`${plural(f.scanned_pages, "scanned page")} to read.`}>Waiting</Badge>;
    case "running":
      return <Badge tone="progress" icon="progress">Reading now</Badge>;
    case "done":
      return <Badge tone="ok" icon="ok" title="Check names and figures against the original.">Read</Badge>;
    case "unavailable":
      return <Badge tone="warn" icon="warn" title={OCR_MISSING}>OCR not installed</Badge>;
    case "failed":
      return <Badge tone="warn" icon="warn" title={f.ocr_error}>Couldn't read</Badge>;
    default:
      return f.type === "image" && f.ocr ? <Badge tone="ok" icon="ok">Read</Badge> : <span className="muted">Not needed</span>;
  }
}

function indexing(f: StatusItem) {
  return f.jobs.index === "running" ? "running" : f.jobs.index === "queued" || f.index_state === "" ? "queued" : null;
}

function KeywordCell({ f }: { f: StatusItem }) {
  if (f.status !== "converted" && f.status !== "needs_ocr") return <Dash />;
  if (f.index_state === "indexed" || f.index_state === "keyword_only") return <Badge tone="ok" icon="ok">Indexed</Badge>;
  if (f.index_state === "failed") return <Badge tone="error" icon="error">Failed</Badge>;
  return indexing(f) === "running" ? <Badge tone="progress" icon="progress">Indexing</Badge> : <Badge tone="idle" icon="pending">Waiting</Badge>;
}

function MeaningCell({ f }: { f: StatusItem }) {
  if (f.status !== "converted" && f.status !== "needs_ocr") return <Dash />;
  const busy = indexing(f);
  if (busy === "running") return <Badge tone="progress" icon="progress">Embedding</Badge>;
  if (f.index_state === "indexed") return <Badge tone="ok" icon="ok" title={f.jobs.index ? "Being updated after OCR." : undefined}>{f.jobs.index ? "Done, updating" : "Done"}</Badge>;
  if (busy === "queued") return <Badge tone="idle" icon="pending">Waiting</Badge>;
  if (f.index_state === "keyword_only") return <Badge tone="warn" icon="warn" title="Starts by itself once the local AI model is running.">Waiting for AI model</Badge>;
  if (f.index_state === "failed") return <Badge tone="error" icon="error">Failed</Badge>;
  return <Dash />;
}
