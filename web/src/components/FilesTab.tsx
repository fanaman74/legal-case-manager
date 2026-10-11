import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type CaseFile, type FileType, type Progress } from "../api";
import { formatBytes, formatDate, formatDateTime, plural, typeLabel } from "../format";
import { useLoad } from "../hooks";
import { href, onLinkClick } from "../router";
import { fromDrop, fromInput } from "../upload";
import { Button, CopyButton, Drawer, ErrorPanel, FormError, Icon, Link, Skeleton } from "../ui";
import { FileStatusBadge } from "./FileStatus";
import { DropZone, UploadPanel, useUploader } from "./Uploader";

const PAGE = 500;
type Sort = { key: "name" | "folder" | "type" | "size" | "added"; asc: boolean };

export function FilesTab({ caseId, folder, canUpload, canEdit, onChanged }: { caseId: string; folder: string; canUpload: boolean; canEdit: boolean; onChanged: () => void }) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [type, setType] = useState<FileType | "">("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<Sort>({ key: "added", asc: false });
  const [files, setFiles] = useState<CaseFile[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openFile, setOpenFile] = useState<CaseFile | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const folders = useLoad(() => api.folders(caseId), [caseId]);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 250);
    return () => clearTimeout(t);
  }, [query]);

  const params = useMemo(() => ({ q: debounced, folder, type, status, sort: sort.key, order: sort.asc ? "asc" : "desc" }), [debounced, folder, type, status, sort]);
  const loaded = useRef(0);
  loaded.current = files.length;

  const load = useCallback(
    async (offset: number, limit = PAGE) => {
      try {
        const r = await api.files(caseId, { ...params, limit, offset });
        setFiles((prev) => (offset === 0 ? r.items : [...prev, ...r.items]));
        setTotal(r.total);
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [caseId, params],
  );
  useEffect(() => {
    void load(0);
  }, [load]);

  // Refresh the list as uploads land, at most twice a second.
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onStored = useCallback(() => {
    if (refreshTimer.current) return;
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      void load(0);
      void folders.reload();
      onChanged();
    }, 500);
  }, [load, folders, onChanged]);
  const uploader = useUploader(caseId, onStored);
  const openRef = useRef<CaseFile | null>(null);
  openRef.current = openFile;
  const progress = useProgress(caseId, () => {
    // Statuses changed: refresh the rows on screen and the open file.
    void load(0, Math.max(PAGE, Math.min(loaded.current, 1000)));
    const f = openRef.current;
    if (f) void api.file(caseId, f.id).then((fresh) => setOpenFile((cur) => (cur?.id === fresh.id ? fresh : cur)), () => undefined);
  });

  const addFiles = (picked: ReturnType<typeof fromInput>) => {
    if (picked.length === 0) return;
    setShowUpload(true);
    uploader.add(picked);
  };

  const caseEmpty = folders.data !== null && folders.data.items.length === 0;
  const filtered = debounced || type || folder || status;

  return (
    <div className="files files--with-next">
      <aside className="files__tree" aria-label="Folders">
        <FolderTree caseId={caseId} folders={folders.data?.items ?? []} current={folder} />
      </aside>

      <div className="files__main">
        {canUpload && (
          <>
            <input ref={fileInput} type="file" multiple hidden onChange={(e) => { addFiles(fromInput(e.target.files!)); e.target.value = ""; }} />
            <input ref={folderInput} type="file" multiple hidden {...{ webkitdirectory: "" }} onChange={(e) => { addFiles(fromInput(e.target.files!)); e.target.value = ""; }} />
          </>
        )}
        {showUpload && uploader.items.length > 0 && (
          <UploadPanel
            items={uploader.items}
            onRetry={uploader.retry}
            onCancel={uploader.cancel}
            onClose={() => { setShowUpload(false); uploader.clear(); }}
            onShowFile={(id) => void api.file(caseId, id).then(setOpenFile, (e: Error) => setError(e.message))}
          />
        )}

        {!caseEmpty && (
          <div className="toolbar">
            <label className="search">
              <Icon name="search" />
              <span className="sr-only">Search file names</span>
              <input className="input input--search" type="search" placeholder="Search file names" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            <label className="field--inline">
              <span className="sr-only">File type</span>
              <select className="input input--select" value={type} onChange={(e) => setType(e.target.value as FileType | "")}>
                <option value="">All types</option>
                {Object.entries(typeLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            <label className="field--inline">
              <span className="sr-only">Status</span>
              <select className="input input--select" value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">All statuses</option>
                <option value="converted">Converted</option>
                <option value="warnings">With warnings</option>
                <option value="scanned">Scanned, needs OCR</option>
                <option value="failed">Failed</option>
                <option value="queued">Waiting</option>
              </select>
            </label>
            <span className="toolbar__count" aria-live="polite">{total !== null && (filtered ? `${plural(total, "file")} match` : plural(total, "file"))}</span>
            {canUpload && (
              <div className="toolbar__end btn-row">
                <Button icon="upload" onClick={() => fileInput.current?.click()}>Upload files</Button>
                <Button icon="folder" onClick={() => folderInput.current?.click()}>Upload folder</Button>
              </div>
            )}
          </div>
        )}

        {!caseEmpty && progress.data && <ProgressLine progress={progress.data} />}
        {error && <ErrorPanel message={error} onRetry={() => void load(0)} />}
        {total === null && !error && <Skeleton rows={8} />}
        {caseEmpty && total === 0 && (canUpload ? (
          <DropZone empty onFiles={(dt) => void fromDrop(dt).then(addFiles)} onPickFiles={() => fileInput.current?.click()} onPickFolder={() => folderInput.current?.click()} />
        ) : (
          <div className="empty"><Icon name="file" /><p className="empty__title">No files yet.</p><p className="empty__text">Files appear here once an Editor or the Admin uploads them.</p></div>
        ))}
        {!caseEmpty && total === 0 && <p className="empty-inline">No files match. Clear the search or choose another folder.</p>}
        {total !== null && total > 0 && (
          <FileTable
            files={files}
            total={total}
            sort={sort}
            setSort={setSort}
            onOpen={setOpenFile}
            onMore={() => void load(files.length)}
            dropTarget={canUpload ? (dt) => void fromDrop(dt).then(addFiles) : undefined}
          />
        )}
      </div>

      <NextSteps
        caseId={caseId}
        progress={progress.data}
        canUpload={canUpload}
        canEdit={canEdit}
        onUpload={() => fileInput.current?.click()}
        onFilter={(s) => setStatus(s)}
        onQueued={() => void progress.reload()}
      />

      {openFile && (
        <FileDrawer
          caseId={caseId}
          file={openFile}
          canEdit={canEdit}
          onClose={() => setOpenFile(null)}
          onChanged={(f) => {
            setOpenFile(f);
            setFiles((prev) => prev.map((p) => (p.id === f.id ? f : p)));
            void progress.reload();
          }}
          onOpenOther={(id) => void api.file(caseId, id).then(setOpenFile, (e: Error) => setError(e.message))}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

type Node = { name: string; path: string; own: number; total: number; children: Node[] };

function buildTree(folders: { path: string; files: number }[]): Node {
  const root: Node = { name: "All files", path: "", own: 0, total: 0, children: [] };
  for (const f of folders) {
    let node = root;
    root.total += f.files;
    if (!f.path) {
      root.own += f.files;
      continue;
    }
    const parts = f.path.split("/");
    parts.forEach((part, i) => {
      let child = node.children.find((c) => c.name === part);
      if (!child) {
        child = { name: part, path: parts.slice(0, i + 1).join("/"), own: 0, total: 0, children: [] };
        node.children.push(child);
      }
      child.total += f.files;
      if (i === parts.length - 1) child.own += f.files;
      node = child;
    });
  }
  const sortRec = (n: Node) => {
    n.children.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }));
    n.children.forEach(sortRec);
  };
  sortRec(root);
  return root;
}

function FolderTree({ caseId, folders, current }: { caseId: string; folders: { path: string; files: number }[]; current: string }) {
  const root = useMemo(() => buildTree(folders), [folders]);
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const link = (path: string) => href({ page: "case", id: caseId, tab: "files", folder: path });

  const render = (n: Node, depth: number) => {
    const isOpen = !closed.has(n.path) || (current + "/").startsWith(n.path + "/") && n.path !== current;
    const active = n.path === current;
    return (
      <li key={n.path || "/"}>
        <div className={`tree__row${active ? " tree__row--active" : ""}`} style={{ paddingLeft: depth * 14 }}>
          {n.children.length > 0 && depth > 0 ? (
            <button
              className="tree__toggle"
              aria-label={`${isOpen ? "Collapse" : "Expand"} ${n.name}`}
              aria-expanded={isOpen}
              onClick={() => setClosed((s) => { const x = new Set(s); if (x.has(n.path)) x.delete(n.path); else x.add(n.path); return x; })}
            >
              <Icon name={isOpen ? "chevronDown" : "chevronRight"} />
            </button>
          ) : (
            <span className="tree__spacer" />
          )}
          <a className="tree__link" href={link(n.path)} onClick={onLinkClick} aria-current={active ? "page" : undefined}>
            <Icon name={depth === 0 ? "case" : active ? "folderOpen" : "folder"} />
            <span className="tree__name">{n.name}</span>
            <span className="tree__count">{n.total.toLocaleString()}</span>
          </a>
        </div>
        {n.children.length > 0 && isOpen && <ul>{n.children.map((c) => render(c, depth + 1))}</ul>}
      </li>
    );
  };
  return <ul className="tree">{render(root, 0)}</ul>;
}

// ---------------------------------------------------------------------------


function FileTable({ files, total, sort, setSort, onOpen, onMore, dropTarget }: { files: CaseFile[]; total: number; sort: Sort; setSort: (s: Sort) => void; onOpen: (f: CaseFile) => void; onMore: () => void; dropTarget?: (dt: DataTransfer) => void }) {
  const scroller = useRef<HTMLDivElement>(null);
  const [over, setOver] = useState(false);
  const v = useVirtualizer({ count: files.length, getScrollElement: () => scroller.current, estimateSize: () => 36, overscan: 12 });
  const rows = v.getVirtualItems();
  const padTop = rows.length ? rows[0].start : 0;
  const padBottom = rows.length ? v.getTotalSize() - rows[rows.length - 1].end : 0;

  useEffect(() => {
    const last = rows[rows.length - 1];
    if (last && last.index >= files.length - 20 && files.length < total) onMore();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows[rows.length - 1]?.index, files.length, total]);

  const header = (label: string, key: Sort["key"], cls?: string) => {
    const active = sort.key === key;
    return (
      <th scope="col" className={cls} aria-sort={active ? (sort.asc ? "ascending" : "descending") : "none"}>
        <button className="sort" onClick={() => setSort({ key, asc: active ? !sort.asc : key === "name" || key === "folder" || key === "type" })}>
          {label}
          <span className={`sort__arrow${active ? " sort__arrow--on" : ""}`} aria-hidden="true">{active && !sort.asc ? "↓" : "↑"}</span>
        </button>
      </th>
    );
  };

  return (
    <div
      className={`table-wrap table-wrap--scroll${over ? " table-wrap--drop" : ""}`}
      ref={scroller}
      onDragOver={dropTarget ? (e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setOver(true); } } : undefined}
      onDragLeave={() => setOver(false)}
      onDrop={dropTarget ? (e) => { e.preventDefault(); setOver(false); dropTarget(e.dataTransfer); } : undefined}
    >
      <table className="table table--compact table--sticky">
        <thead>
          <tr>
            {header("Name", "name")}
            <th scope="col">Status</th>
            {header("Folder", "folder")}
            {header("Type", "type")}
            {header("Size", "size", "num")}
            {header("Added", "added")}
            <th scope="col">Added by</th>
          </tr>
        </thead>
        <tbody>
          {padTop > 0 && <tr aria-hidden="true"><td colSpan={7} style={{ height: padTop, padding: 0, border: 0 }} /></tr>}
          {rows.map((r) => {
            const f = files[r.index];
            return (
              <tr key={f.id}>
                <th scope="row" className="cell-name">
                  <div className="cell-name__inner">
                  <button className="linkish" onClick={() => onOpen(f)} title={f.parent_id ? `${f.name} (email attachment)` : f.name}>
                    <Icon name={f.parent_id ? "clip" : f.type === "email" ? "mail" : "file"} />
                    <span className="truncate">{f.name}</span>
                  </button>
                  {f.tags.length > 0 && <span className="cell-tags">{f.tags.map((t) => <span key={t} className="chip chip--sm">{t}</span>)}</span>}
                  </div>
                </th>
                <td className="nowrap"><FileStatusBadge file={f} /></td>
                <td className="cell-folder"><span className="truncate" title={f.folder}>{f.folder || <span className="muted">Top level</span>}</span></td>
                <td>{typeLabel[f.type]}</td>
                <td className="num">{formatBytes(f.size)}</td>
                <td className="nowrap">{formatDate(f.added_at)}</td>
                <td className="nowrap">{f.added_by}</td>
              </tr>
            );
          })}
          {padBottom > 0 && <tr aria-hidden="true"><td colSpan={7} style={{ height: padBottom, padding: 0, border: 0 }} /></tr>}
        </tbody>
      </table>
      {files.length < total && <p className="table-more">Showing {files.length.toLocaleString()} of {total.toLocaleString()}. Scroll for more.</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------

function FileDrawer({ caseId, file, canEdit, onClose, onChanged, onOpenOther }: { caseId: string; file: CaseFile; canEdit: boolean; onClose: () => void; onChanged: (f: CaseFile) => void; onOpenOther: (id: string) => void }) {
  const [tag, setTag] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const act = async (fn: () => Promise<CaseFile>) => {
    setBusy(true);
    setError(null);
    try {
      onChanged(await fn());
      setTag("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const save = (tags: string[]) => act(() => api.editFile(caseId, file.id, tags));
  const path = [file.folder, file.name].filter(Boolean).join("/");
  const converted = file.status === "converted" || file.status === "needs_ocr";
  const canOcr = canEdit && file.type === "pdf" && file.scanned_pages.length > 0;
  return (
    <Drawer open onOpenChange={(o) => !o && onClose()} title={file.name}>
      <div className="drawer__section">
        <div className="btn-row">
          {converted && (
            <Link className="btn btn--primary btn--md" to={href({ page: "document", id: caseId, fileId: file.id, page_no: 1 })}>
              <Icon name="open" /><span>Open document</span>
            </Link>
          )}
          <a className={`btn btn--${converted ? "secondary" : "primary"} btn--md`} href={api.downloadUrl(caseId, file.id)} download>
            <Icon name="download" /><span>Download original</span>
          </a>
        </div>
      </div>
      <FormError message={error} />
      <dl className="kv">
        <dt>Status</dt>
        <dd className="kv__stack">
          <FileStatusBadge file={file} />
          {file.status === "failed" && <span>{file.error}</span>}
          {file.status === "queued" && <span className="muted">Waiting to be converted to Markdown.</span>}
          {file.status === "converting" && <span className="muted">Being converted to Markdown now.</span>}
        </dd>
        {file.warnings.length > 0 && (
          <>
            <dt>Warnings</dt>
            <dd>
              <ul className="warn-list">{file.warnings.map((w) => <li key={w}><Icon name="warn" /> {w}</li>)}</ul>
            </dd>
          </>
        )}
        {(file.status === "failed" || canOcr) && canEdit && (
          <>
            <dt><span className="sr-only">Actions</span></dt>
            <dd className="btn-row">
              {canOcr && <Button size="sm" icon="ocr" busy={busy} onClick={() => void act(() => api.reconvert(caseId, file.id, true))}>Run OCR</Button>}
              {file.status === "failed" && <Button size="sm" icon="refresh" busy={busy} onClick={() => void act(() => api.reconvert(caseId, file.id))}>Retry</Button>}
            </dd>
          </>
        )}
        {file.page_count !== null && (
          <>
            <dt>Pages</dt>
            <dd>{file.page_count.toLocaleString()}{file.ocr ? " · read with OCR" : ""}</dd>
          </>
        )}
        {file.email && (
          <>
            <dt>From</dt>
            <dd className="break">{file.email.from || <span className="muted">Not recorded</span>}</dd>
            <dt>Sent</dt>
            <dd>{formatDateTime(file.email.date) || <span className="muted">Not recorded</span>}</dd>
          </>
        )}
        {file.attachments.length > 0 && (
          <>
            <dt>Attachments</dt>
            <dd>
              <ul className="plain-list">
                {file.attachments.map((a) => (
                  <li key={a.doc_id}>
                    <button className="linkish" onClick={() => onOpenOther(a.doc_id)}><Icon name="clip" /> {a.name}</button>
                    {a.duplicate && <span className="muted"> (already in the case)</span>}
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
        {file.parent_id && (
          <>
            <dt>Attached to</dt>
            <dd><button className="linkish" onClick={() => onOpenOther(file.parent_id!)}><Icon name="mail" /> The email it came with</button></dd>
          </>
        )}
        {(file.status === "converted" || file.status === "needs_ocr") && (
          <>
            <dt>Search</dt>
            <dd>
              {file.search.state === "indexed" ? `Ready (${plural(file.search.passages, "passage")})`
                : file.search.state === "keyword_only" ? "Exact words only, until the local AI model is running"
                : file.search.state === "failed" ? "Couldn't be indexed"
                : "Being prepared"}
            </dd>
          </>
        )}
        <dt>Original path</dt>
        <dd className="mono break">{path}</dd>
        <dt>Type</dt>
        <dd>{typeLabel[file.type]} ({file.ext})</dd>
        <dt>Size</dt>
        <dd>{formatBytes(file.size)} <span className="muted">({file.size.toLocaleString()} bytes)</span></dd>
        <dt>Added</dt>
        <dd>{formatDateTime(file.added_at)}{file.added_by ? ` by ${file.added_by}` : ""}</dd>
        {file.converted_at && (
          <>
            <dt>Converted</dt>
            <dd>{formatDateTime(file.converted_at)}</dd>
          </>
        )}
        <dt>SHA-256</dt>
        <dd className="hash">
          <span className="mono break">{file.sha256}</span>
          <CopyButton text={file.sha256} what="SHA-256 hash" />
        </dd>
      </dl>
      <div className="drawer__section">
        <h3 className="h3">Tags</h3>
        <div className="chips">
          {file.tags.length === 0 && <span className="muted">No tags.</span>}
          {file.tags.map((t) => (
            <span key={t} className="chip">
              {t}
              {canEdit && (
                <button className="chip__remove" aria-label={`Remove tag ${t}`} disabled={busy} onClick={() => void save(file.tags.filter((x) => x !== t))}>
                  <Icon name="close" />
                </button>
              )}
            </span>
          ))}
        </div>
        {canEdit && (
          <form className="inline-form" onSubmit={(e) => { e.preventDefault(); if (tag.trim()) void save([...file.tags, tag.trim()]); }}>
            <label htmlFor="new-tag" className="sr-only">New tag</label>
            <input id="new-tag" className="input input--sm" value={tag} onChange={(e) => setTag(e.target.value)} placeholder="Add a tag" maxLength={50} />
            <Button size="sm" type="submit" busy={busy} disabled={!tag.trim()}>Add</Button>
          </form>
        )}
      </div>
      <p className="hint">The original is kept byte for byte as uploaded. Opening and downloading are recorded in the case's activity.</p>
    </Drawer>
  );
}

// ---------------------------------------------------------------------------
// Progress of conversion, polled while work is going on.

function pending(p: Progress | null): number {
  if (!p) return 0;
  return (p.files.queued ?? 0) + (p.files.converting ?? 0);
}

function useProgress(caseId: string, onChange: () => void) {
  const [data, setData] = useState<Progress | null>(null);
  const last = useRef("");
  const changed = useRef(onChange);
  changed.current = onChange;
  const reload = useCallback(async () => {
    try {
      const p = await api.progress(caseId);
      const key = JSON.stringify([p.files, p.warnings, p.scanned]);
      if (last.current && key !== last.current) changed.current();
      last.current = key;
      setData(p);
    } catch {
      // the file list shows its own error
    }
  }, [caseId]);
  const busy = pending(data) > 0;
  useEffect(() => {
    void reload();
    const t = setInterval(() => void reload(), busy ? 2500 : 15000);
    return () => clearInterval(t);
  }, [reload, busy]);
  return { data, reload };
}

function ProgressLine({ progress }: { progress: Progress }) {
  const total = progress.total;
  const waiting = pending(progress);
  if (total === 0) return null;
  if (waiting > 0) {
    const done = total - waiting;
    return (
      <div className="progress-line" role="status">
        <Icon name="progress" />
        <span className="progress-line__text">Converting <span className="tabular">{done.toLocaleString()} of {total.toLocaleString()}</span> files</span>
        <span className="progress-line__bar" aria-hidden="true"><span style={{ width: `${Math.round((done / total) * 100)}%` }} /></span>
      </div>
    );
  }
  const problems = (progress.files.failed ?? 0) + (progress.files.needs_ocr ?? 0);
  if (problems > 0) {
    return (
      <p className="next-line next-line--warn" role="status">
        <Icon name="warn" /> {(total - problems).toLocaleString()} of {plural(total, "file")} converted to Markdown. {plural(problems, "file")} {problems === 1 ? "needs" : "need"} attention.
      </p>
    );
  }
  return (
    <p className="next-line" role="status">
      <Icon name="ok" /> All {plural(total, "file")} converted to Markdown.
    </p>
  );
}

function NextSteps({ caseId, progress, canUpload, canEdit, onUpload, onFilter, onQueued }: {
  caseId: string;
  progress: Progress | null;
  canUpload: boolean;
  canEdit: boolean;
  onUpload: () => void;
  onFilter: (status: string) => void;
  onQueued: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const total = progress?.total ?? 0;
  const waiting = pending(progress);
  const failed = progress?.files.failed ?? 0;
  const scanned = progress?.scanned ?? 0;
  const warnings = progress?.warnings ?? 0;
  const review = failed + scanned + warnings;
  const run = async (what: "ocr_scanned" | "retry_failed") => {
    setBusy(what);
    setError(null);
    try {
      await api.bulkConvert(caseId, what);
      onQueued();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const step = (state: "done" | "current" | "later") => `next__item next__item--${state}`;
  const icon = (state: "done" | "current" | "later") => (state === "done" ? "ok" : state === "current" ? "pending" : "lock");
  const s1 = total > 0 ? "done" : "current";
  const s2 = total === 0 ? "later" : waiting > 0 ? "current" : "done";
  const s3 = total === 0 || waiting > 0 ? "later" : review > 0 ? "current" : "done";
  const indexing = progress?.index?.waiting ?? 0;

  if (!progress) return <aside className="next" aria-hidden="true" />;
  return (
    <aside className="next" aria-labelledby="next-title">
      <h2 id="next-title" className="next__title">What to do next</h2>
      <FormError message={error} />
      <ol className="next__list">
        <li className={step(s1)}>
          <Icon name={icon(s1)} />
          <div>
            <p className="next__label">Upload files or a folder</p>
            {s1 === "done" ? <p className="hint">{plural(total, "file")} in this case.</p> : canUpload ? <button className="linkish" onClick={onUpload}>Choose files</button> : <p className="hint">An Editor or the Admin adds files.</p>}
          </div>
        </li>
        <li className={step(s2)}>
          <Icon name={icon(s2)} />
          <div>
            <p className="next__label">Convert to Markdown</p>
            <p className="hint">{s2 === "current" ? `${(total - waiting).toLocaleString()} of ${total.toLocaleString()} done. This runs by itself.` : s2 === "done" ? "Done." : "Starts by itself after upload."}</p>
          </div>
        </li>
        <li className={step(s3)}>
          <Icon name={s3 === "current" ? "warn" : icon(s3)} />
          <div>
            <p className="next__label">Review problems</p>
            {s3 !== "current" && <p className="hint">{s3 === "done" ? "Nothing to review." : "Any failures or warnings show here."}</p>}
            {s3 === "current" && (
              <ul className="next__actions">
                {failed > 0 && (
                  <li>
                    <button className="linkish" onClick={() => onFilter("failed")}>{plural(failed, "file")} failed</button>
                    {canEdit && <Button size="sm" variant="quiet" busy={busy === "retry_failed"} onClick={() => void run("retry_failed")}>Retry all</Button>}
                  </li>
                )}
                {scanned > 0 && (
                  <li>
                    <button className="linkish" onClick={() => onFilter("scanned")}>{plural(scanned, "scanned PDF")} need OCR</button>
                    {canEdit && <Button size="sm" variant="quiet" busy={busy === "ocr_scanned"} onClick={() => void run("ocr_scanned")}>Run OCR on all</Button>}
                  </li>
                )}
                {warnings > 0 && (
                  <li><button className="linkish" onClick={() => onFilter("warnings")}>{plural(warnings, "file")} with warnings</button></li>
                )}
              </ul>
            )}
          </div>
        </li>
        <li className={step(total > 0 && waiting === 0 ? "current" : "later")}>
          <Icon name={total > 0 && waiting === 0 ? "search" : "lock"} />
          <div>
            <p className="next__label">Search the case</p>
            {total > 0 && waiting === 0 ? (
              <>
                <Link to={href({ page: "case", id: caseId, tab: "search", folder: "" })}>Search the documents</Link>
                {indexing > 0 && <p className="hint">{plural(indexing, "file")} still being prepared for search.</p>}
              </>
            ) : (
              <p className="hint">Opens once files are converted.</p>
            )}
          </div>
        </li>
      </ol>
    </aside>
  );
}
