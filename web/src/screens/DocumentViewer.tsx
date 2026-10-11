import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type CaseFile } from "../api";
import { FileStatusBadge } from "../components/FileStatus";
import { formatBytes, formatDateTime, typeLabel } from "../format";
import { useApp, useLoad, useTitle } from "../hooks";
import { renderMarkdown, splitPages } from "../markdown";
import { href, navigate } from "../router";
import { Button, CopyButton, EmptyState, ErrorPanel, Icon, Link, Skeleton } from "../ui";

/** Original and converted Markdown side by side, kept on the same page. */
export function DocumentViewer({ caseId, fileId, page }: { caseId: string; fileId: string; page: number }) {
  const { cases, toast } = useApp();
  const file = useLoad(() => api.file(caseId, fileId), [caseId, fileId]);
  const doc = useLoad(() => api.markdown(caseId, fileId), [caseId, fileId]);
  const theCase = useLoad(() => api.case(caseId), [caseId]);
  const caseName = theCase.data?.name ?? cases.find((c) => c.id === caseId)?.name ?? "Case";
  useTitle(file.data?.name ?? "Document");
  const [current, setCurrent] = useState(page);
  const [showFm, setShowFm] = useState(false);
  const [pane, setPane] = useState<"original" | "markdown">("markdown");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const origRef = useRef<HTMLDivElement>(null);
  const mdRef = useRef<HTMLDivElement>(null);
  // Which pane the person is scrolling, so the other one follows without a loop.
  const driver = useRef<"original" | "markdown" | null>(null);

  const sections = useMemo(() => (doc.data ? splitPages(doc.data.body) : []), [doc.data]);
  const pages = sections.filter((s) => s.page !== null).map((s) => s.page as number);
  const pageCount = Math.max(file.data?.previews ?? 0, pages.length ? Math.max(...pages) : 0);

  const scrollTo = useCallback((n: number, which: "both" | "original" | "markdown" = "both") => {
    const go = (root: HTMLElement | null) => {
      const el = root?.querySelector<HTMLElement>(`[data-page="${n}"]`);
      if (!root || !el) return;
      // Scroll inside the pane only, so the page header never moves; on narrow
      // screens the panes don't scroll and the window does.
      if (root.scrollHeight > root.clientHeight) root.scrollTop = el.offsetTop - 8;
      else if (root.offsetParent) el.scrollIntoView({ block: "start" });
    };
    if (which !== "markdown") go(origRef.current);
    if (which !== "original") go(mdRef.current);
  }, []);

  // Keep the address on the page being read, so it can be shared.
  useEffect(() => {
    const to = href({ page: "document", id: caseId, fileId, page_no: current });
    if (location.pathname + location.search !== to) history.replaceState(null, "", to);
  }, [current, caseId, fileId]);

  // Start at the page in the address once everything has loaded.
  const started = useRef(false);
  useEffect(() => {
    if (started.current || !doc.data || !file.data) return;
    started.current = true;
    if (page > 1) requestAnimationFrame(() => scrollTo(page));
  }, [doc.data, file.data, page, scrollTo]);

  // Watch which page is at the top of each pane.
  useEffect(() => {
    const roots = [origRef.current, mdRef.current];
    const observers = roots.map((root, i) => {
      if (!root) return null;
      const which = i === 0 ? "original" : "markdown";
      const io = new IntersectionObserver(
        (entries) => {
          if (driver.current !== which) return;
          const top = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
          const n = Number((top?.target as HTMLElement | undefined)?.dataset.page);
          if (n) {
            setCurrent(n);
            scrollTo(n, which === "original" ? "markdown" : "original");
          }
        },
        { root, rootMargin: "0px 0px -70% 0px" },
      );
      root.querySelectorAll("[data-page]").forEach((el) => io.observe(el));
      return io;
    });
    return () => observers.forEach((o) => o?.disconnect());
  }, [sections, pageCount, scrollTo]);

  const go = useCallback(
    (n: number) => {
      if (!pageCount) return;
      const next = Math.min(Math.max(n, 1), pageCount);
      driver.current = null;
      setCurrent(next);
      scrollTo(next);
    },
    [pageCount, scrollTo],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [contenteditable]") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "j") go(current + 1);
      else if (e.key === "k") go(current - 1);
      else if (e.key === "Escape") navigate(href({ page: "case", id: caseId, tab: "files", folder: "" }));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, current, caseId]);

  if (file.error && !file.data) {
    return file.error.includes("don't have access") || file.error.includes("isn't in this case") ? (
      <EmptyState icon="lock" title="You don't have access to this document." action={<Link to="/">Go to your cases</Link>}>
        It may have been deleted, or you haven't been added to its case.
      </EmptyState>
    ) : (
      <ErrorPanel message={file.error} onRetry={file.reload} />
    );
  }
  if (!file.data) return <div className="page"><div className="skeleton__title" /><Skeleton rows={10} /></div>;
  const f = file.data;
  const back = href({ page: "case", id: caseId, tab: "files", folder: f.folder });
  const canEdit = theCase.data?.permissions?.edit ?? false;

  const reconvert = async (ocr: boolean) => {
    setBusy(true);
    setProblem(null);
    try {
      await api.reconvert(caseId, fileId, ocr);
      toast(ocr ? "OCR started. The document updates when it's done." : "Conversion started again.");
      void file.reload();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="viewer">
      <header className="viewer__head">
        <div className="viewer__title">
          <Link to={back} className="viewer__back"><Icon name="back" /> <span>{caseName}</span></Link>
          <h1 className="viewer__name" title={f.name}>{f.name}</h1>
          <p className="viewer__meta">
            <FileStatusBadge file={f} />
            <span>{typeLabel[f.type]}</span>
            {f.page_count !== null && <span>{f.page_count.toLocaleString()} {f.page_count === 1 ? "page" : "pages"}</span>}
            {f.ocr && <span className="badge badge--idle"><Icon name="ocr" /><span>OCR</span></span>}
            <span className="mono" title={`SHA-256 ${f.sha256}`}>SHA-256 {f.sha256.slice(0, 12)}…</span>
            <CopyButton text={f.sha256} label="Copy hash" what="SHA-256 hash" />
          </p>
        </div>
        <div className="viewer__actions btn-row">
          {pageCount > 1 && (
            <div className="pager" role="group" aria-label="Pages">
              <Button size="sm" variant="quiet" icon="back" aria-label="Previous page (K)" disabled={current <= 1} onClick={() => go(current - 1)} />
              <span className="pager__label tabular">Page {current} of {pageCount}</span>
              <Button size="sm" variant="quiet" icon="chevronRight" aria-label="Next page (J)" disabled={current >= pageCount} onClick={() => go(current + 1)} />
            </div>
          )}
          <Button size="sm" variant="quiet" onClick={() => setShowFm((v) => !v)}>{showFm ? "Hide front matter" : "Front matter"}</Button>
          {canEdit && f.type === "pdf" && f.scanned_pages.length > 0 && <Button size="sm" icon="ocr" busy={busy} onClick={() => void reconvert(true)}>Run OCR</Button>}
          {canEdit && <Button size="sm" icon="refresh" variant="quiet" busy={busy} onClick={() => void reconvert(false)}>Convert again</Button>}
          <a className="btn btn--secondary btn--sm" href={api.downloadUrl(caseId, fileId)} download><Icon name="download" /><span>Download original</span></a>
        </div>
      </header>

      {problem && <ErrorPanel message={problem} />}
      {f.warnings.length > 0 && (
        <ul className="viewer__warnings" aria-label="Conversion warnings">
          {f.warnings.map((w) => <li key={w}><Icon name="warn" /> {w}</li>)}
        </ul>
      )}
      {showFm && doc.data && <pre className="frontmatter" aria-label="Front matter">{doc.data.front_matter}</pre>}

      <div className="viewer__tabs" role="tablist" aria-label="Show">
        <button role="tab" aria-selected={pane === "original"} className="viewer__tab" onClick={() => setPane("original")}>Original</button>
        <button role="tab" aria-selected={pane === "markdown"} className="viewer__tab" onClick={() => setPane("markdown")}>Markdown</button>
      </div>

      <div className={`viewer__split viewer__split--${pane}`}>
        <section className="viewer__pane viewer__pane--original" aria-label="Original" ref={origRef} onWheel={() => (driver.current = "original")} onTouchStart={() => (driver.current = "original")} onKeyDown={() => (driver.current = "original")} tabIndex={0}>
          <Original caseId={caseId} file={f} pageCount={f.previews} />
        </section>
        <section className="viewer__pane viewer__pane--markdown" aria-label="Converted Markdown" ref={mdRef} onWheel={() => (driver.current = "markdown")} onTouchStart={() => (driver.current = "markdown")} onKeyDown={() => (driver.current = "markdown")} tabIndex={0}>
          {doc.error && !doc.data ? (
            doc.error.includes("hasn't been converted") ? (
              <EmptyState icon="pending" title="This file is still being converted.">It opens here as soon as it's ready. <Button size="sm" variant="quiet" onClick={doc.reload}>Check again</Button></EmptyState>
            ) : (
              <ErrorPanel message={doc.error} onRetry={doc.reload} />
            )
          ) : !doc.data ? (
            <Skeleton rows={12} />
          ) : (
            <article className="reader">
              {sections.map((s, i) => (
                <div key={i} data-page={s.page ?? undefined} className="reader__page">
                  {s.page !== null && <p className="page-marker" aria-label={`Page ${s.page}`}><span>Page {s.page}</span></p>}
                  {s.text ? <div dangerouslySetInnerHTML={{ __html: renderMarkdown(s.text) }} /> : <p className="muted">No text on this page.</p>}
                </div>
              ))}
            </article>
          )}
        </section>
      </div>
    </div>
  );
}

function Original({ caseId, file, pageCount }: { caseId: string; file: CaseFile; pageCount: number }) {
  if (pageCount > 0) {
    return (
      <div className="pages">
        {Array.from({ length: pageCount }, (_, i) => i + 1).map((n) => (
          <figure key={n} className="pageimg" data-page={n}>
            <img src={api.pageUrl(caseId, file.id, n)} alt={`Page ${n} of the original`} loading="lazy" decoding="async" width={1100} height={1556} />
            <figcaption className="pageimg__num">Page {n}</figcaption>
          </figure>
        ))}
      </div>
    );
  }
  if (file.email) {
    const e = file.email;
    return (
      <div className="original-card">
        <h2 className="h3"><Icon name="mail" /> Email</h2>
        <dl className="kv">
          <dt>From</dt><dd className="break">{e.from || "Not recorded"}</dd>
          <dt>To</dt><dd className="break">{e.to.join("; ") || "Not recorded"}</dd>
          {e.cc.length > 0 && <><dt>Cc</dt><dd className="break">{e.cc.join("; ")}</dd></>}
          <dt>Sent</dt><dd>{formatDateTime(e.date) || "Not recorded"}</dd>
          <dt>Subject</dt><dd>{e.subject || "(no subject)"}</dd>
        </dl>
        {file.attachments.length > 0 && (
          <>
            <h3 className="h3">Attachments</h3>
            <ul className="plain-list">
              {file.attachments.map((a) => (
                <li key={a.doc_id}><Link to={href({ page: "document", id: caseId, fileId: a.doc_id, page_no: 1 })}><Icon name="clip" /> {a.name}</Link></li>
              ))}
            </ul>
          </>
        )}
        <p className="hint">The email's original file is kept unchanged. Download it to open it in Outlook or your mail program.</p>
      </div>
    );
  }
  return (
    <div className="original-card">
      <h2 className="h3"><Icon name="file" /> {typeLabel[file.type]} document</h2>
      <p>
        {file.type === "word" ? "Word documents can't be shown as pages here." : "This file has no page pictures."} Download the original to open it
        {file.type === "word" ? " in Word" : ""}. The Markdown beside it is what search and AI read.
      </p>
      <dl className="kv">
        <dt>Size</dt><dd>{formatBytes(file.size)}</dd>
        {file.title && <><dt>Title</dt><dd>{file.title}</dd></>}
        {file.doc_date && <><dt>Created</dt><dd>{formatDateTime(file.doc_date)}</dd></>}
      </dl>
    </div>
  );
}
