import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, type SearchFilters, type SearchMode, type SearchResponse, type SearchResult } from "../api";
import { formatDate, typeLabel } from "../format";
import { useLoad } from "../hooks";
import { href, navigate } from "../router";
import { Button, EmptyState, ErrorPanel, Icon, Link, Skeleton } from "../ui";

const MODES: { id: SearchMode; label: string; hint: string }[] = [
  { id: "hybrid", label: "Best match", hint: "Exact words and similar meaning together." },
  { id: "keyword", label: "Exact words", hint: "Only passages with these words (or their forms: deliver, delivered)." },
  { id: "semantic", label: "Similar meaning", hint: "Passages about the same thing, even in other words." },
];

/** Search inside one case. Nothing here leaves this computer: the meaning
 * search uses the local model. */
export function SearchTab({ caseId, q }: { caseId: string; q: string }) {
  const [text, setText] = useState(q);
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [filters, setFilters] = useState<SearchFilters>({});
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const folders = useLoad(() => api.folders(caseId), [caseId]);

  useEffect(() => setText(q), [q]);
  useEffect(() => {
    if (!q) {
      setResult(null);
      setError(null);
      input.current?.focus();
      return;
    }
    const ctrl = new AbortController();
    setBusy(true);
    setError(null);
    api
      .search(caseId, q, mode, filters, ctrl.signal)
      .then((r) => setResult(r))
      .catch((e: Error) => {
        if (e.name !== "AbortError") {
          setError(e.message);
          setResult(null);
        }
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setBusy(false);
      });
    return () => ctrl.abort();
  }, [caseId, q, mode, filters]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    navigate(href({ page: "case", id: caseId, tab: "search", folder: "", q: text.trim() }));
  };
  const setFilter = (k: keyof SearchFilters, v: string) => setFilters((f) => ({ ...f, [k]: v || undefined }));
  const filterCount = Object.values(filters).filter(Boolean).length;

  return (
    <div className="search">
      <form className="search__form" role="search" onSubmit={submit}>
        <div className="search__row">
          <label className="search__box">
            <span className="sr-only">Search this case</span>
            <Icon name="search" />
            <input ref={input} className="search__input" type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search the documents in this case" maxLength={500} autoComplete="off" />
          </label>
          <Button type="submit" variant="primary" busy={busy}>Search</Button>
        </div>
        <div className="search__options">
          <div className="segmented" role="radiogroup" aria-label="How to match">
            {MODES.map((m) => (
              <button key={m.id} type="button" role="radio" aria-checked={mode === m.id} aria-pressed={mode === m.id} className="segmented__item" title={m.hint} onClick={() => setMode(m.id)}>
                {m.label}
              </button>
            ))}
          </div>
          <Button size="sm" variant="quiet" icon="tag" aria-expanded={showFilters} onClick={() => setShowFilters((s) => !s)}>
            {filterCount ? `Filters (${filterCount})` : "Filters"}
          </Button>
          <p className="search__tips hint">
            <span><code>"exact phrase"</code></span> <span><code>steel OR iron</code></span> <span><code>-draft</code> leaves out</span> <span><code>arbitrat*</code> any ending</span>
          </p>
        </div>
        {showFilters && (
          <div className="search__filters">
            <label className="field field--inline">
              <span>Type</span>
              <select className="input input--select input--sm" value={filters.type ?? ""} onChange={(e) => setFilter("type", e.target.value)}>
                <option value="">Any</option>
                {Object.entries(typeLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            <label className="field field--inline">
              <span>Folder</span>
              <select className="input input--select input--sm" value={filters.folder ?? ""} onChange={(e) => setFilter("folder", e.target.value)}>
                <option value="">All folders</option>
                {folders.data?.items.map((f) => <option key={f.path} value={f.path}>{f.path}</option>)}
              </select>
            </label>
            <label className="field field--inline">
              <span>Dated from</span>
              <input className="input input--sm" type="date" value={filters.date_from ?? ""} onChange={(e) => setFilter("date_from", e.target.value)} />
            </label>
            <label className="field field--inline">
              <span>to</span>
              <input className="input input--sm" type="date" value={filters.date_to ?? ""} onChange={(e) => setFilter("date_to", e.target.value)} />
            </label>
            <label className="field field--inline">
              <span>Tag</span>
              <input className="input input--sm" value={filters.tag ?? ""} onChange={(e) => setFilter("tag", e.target.value.trim())} maxLength={50} placeholder="Any" />
            </label>
            {filterCount > 0 && <Button size="sm" variant="quiet" onClick={() => setFilters({})}>Clear filters</Button>}
            {(filters.date_from || filters.date_to) && <p className="hint">Documents without a date are left out while a date is chosen.</p>}
          </div>
        )}
      </form>

      {error && <ErrorPanel message={error} />}
      {!q && !error && (
        <EmptyState icon="search" title="Search every document in this case.">
          Best match finds the exact words and passages that mean the same thing, so "late shipment" also finds "the goods were not delivered on time". Results show the page each passage is on.
        </EmptyState>
      )}
      {q && busy && !result && <Skeleton rows={6} />}
      {result && <Results caseId={caseId} q={q} r={result} stale={busy} />}
    </div>
  );
}

function Results({ caseId, q, r, stale }: { caseId: string; q: string; r: SearchResponse; stale: boolean }) {
  const notes: ReactNode[] = [];
  if (r.meaning === "unavailable") notes.push("Similar-meaning search isn't available right now because the local AI model isn't running. These are exact-word matches only. The Admin can check Local AI models in the Control Center.");
  if (r.index.waiting) notes.push(`${r.index.waiting.toLocaleString()} ${r.index.waiting === 1 ? "file is" : "files are"} still being prepared for search, so results may be incomplete.`);
  if (r.index.keyword_only && r.meaning !== "unavailable") notes.push(`${r.index.keyword_only.toLocaleString()} ${r.index.keyword_only === 1 ? "file" : "files"} can only be found by exact words for now.`);
  return (
    <section className={`results${stale ? " results--stale" : ""}`} aria-busy={stale} aria-label="Search results">
      {notes.map((n, i) => <p key={i} className="banner banner--idle" role="status"><Icon name="warn" /> {n}</p>)}
      <p className="results__count" role="status">
        {r.documents === 0 ? "No matches." : `${r.documents.toLocaleString()} ${r.documents === 1 ? "document" : "documents"}${r.documents > r.results.length ? `, showing the best ${r.results.length}` : ""}`}
      </p>
      {r.documents === 0 && (
        <p className="hint">Try fewer words, check the spelling, or switch to Similar meaning. Scanned PDFs need OCR before their text can be found.</p>
      )}
      <ol className="results__list">
        {r.results.map((res) => <ResultCard key={res.file.id} caseId={caseId} q={q} res={res} />)}
      </ol>
    </section>
  );
}

function ResultCard({ caseId, q, res }: { caseId: string; q: string; res: SearchResult }) {
  const f = res.file;
  const open = (page: number | null) => href({ page: "document", id: caseId, fileId: f.id, page_no: page ?? 1, q });
  return (
    <li className="result">
      <div className="result__head">
        <Link to={open(res.hits[0]?.page ?? null)} className="result__name">
          <Icon name={f.type === "email" ? "mail" : "file"} /> <span>{f.name}</span>
        </Link>
        <span className="result__meta">
          {f.folder && <span>{f.folder}</span>}
          <span>{typeLabel[f.type]}</span>
          {f.doc_date && <span>{formatDate(f.doc_date)}</span>}
        </span>
      </div>
      <ul className="result__hits">
        {res.hits.map((h) => (
          <li key={h.passage} className="hit">
            <div className="hit__where">
              {h.page !== null ? <Link to={open(h.page)} className="hit__page">Page {h.page}</Link> : <Link to={open(null)} className="hit__page">Open</Link>}
              {h.heading && <span className="hit__heading">{h.heading}</span>}
              <span className="hit__how">{h.found_by.includes("words") && h.found_by.includes("meaning") ? "Words and meaning" : h.found_by.includes("words") ? "Exact words" : "Similar meaning"}</span>
            </div>
            <p className="hit__snippet">{marked(h.snippet)}</p>
          </li>
        ))}
      </ul>
      {res.more > 0 && <p className="hint">{res.more} more {res.more === 1 ? "match" : "matches"} in this document.</p>}
    </li>
  );
}

/** The server marks matched words with \u0002 … \u0003; render them as <mark>
 * without ever treating document text as HTML. */
export function marked(snippet: string): ReactNode[] {
  const out: ReactNode[] = [];
  let i = 0;
  for (const part of snippet.split("\u0002")) {
    const end = part.indexOf("\u0003");
    if (i++ === 0 || end < 0) {
      out.push(part.replaceAll("\u0003", ""));
      continue;
    }
    out.push(<mark key={i}>{part.slice(0, end)}</mark>, part.slice(end + 1));
  }
  return out;
}
