import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type CaseFile, type FileType } from "../api";
import { formatBytes, formatDate, formatDateTime, plural, typeLabel } from "../format";
import { useLoad } from "../hooks";
import { href, onLinkClick } from "../router";
import { fromDrop, fromInput } from "../upload";
import { Badge, Button, CopyButton, Drawer, ErrorPanel, FormError, Icon, Skeleton } from "../ui";
import { DropZone, UploadPanel, useUploader } from "./Uploader";

const PAGE = 500;
type Sort = { key: "name" | "folder" | "type" | "size" | "added"; asc: boolean };

export function FilesTab({ caseId, folder, canUpload, canEdit, onChanged }: { caseId: string; folder: string; canUpload: boolean; canEdit: boolean; onChanged: () => void }) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [type, setType] = useState<FileType | "">("");
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

  const params = useMemo(() => ({ q: debounced, folder, type, sort: sort.key, order: sort.asc ? "asc" : "desc" }), [debounced, folder, type, sort]);

  const load = useCallback(
    async (offset: number) => {
      try {
        const r = await api.files(caseId, { ...params, limit: PAGE, offset });
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

  const addFiles = (picked: ReturnType<typeof fromInput>) => {
    if (picked.length === 0) return;
    setShowUpload(true);
    uploader.add(picked);
  };

  const caseEmpty = folders.data !== null && folders.data.items.length === 0;
  const filtered = debounced || type || folder;

  return (
    <div className={`files${caseEmpty ? " files--with-next" : ""}`}>
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
            <span className="toolbar__count" aria-live="polite">{total !== null && (filtered ? `${plural(total, "file")} match` : plural(total, "file"))}</span>
            {canUpload && (
              <div className="toolbar__end btn-row">
                <Button icon="upload" onClick={() => fileInput.current?.click()}>Upload files</Button>
                <Button icon="folder" onClick={() => folderInput.current?.click()}>Upload folder</Button>
              </div>
            )}
          </div>
        )}

        {!caseEmpty && folders.data && (
          <p className="next-line"><Icon name="ok" /> Files uploaded. <span className="muted">Next: conversion to Markdown and search arrive in the next version.</span></p>
        )}
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

      {caseEmpty && <NextSteps canUpload={canUpload} onUpload={() => fileInput.current?.click()} />}

      {openFile && (
        <FileDrawer
          caseId={caseId}
          file={openFile}
          canEdit={canEdit}
          onClose={() => setOpenFile(null)}
          onChanged={(f) => {
            setOpenFile(f);
            setFiles((prev) => prev.map((p) => (p.id === f.id ? f : p)));
          }}
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

const statusBadge = (f: CaseFile) => (f.status === "stored" ? <Badge tone="ok" icon="ok">Stored</Badge> : <Badge tone="idle" icon="pending">{f.status}</Badge>);

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
            {header("Folder", "folder")}
            {header("Type", "type")}
            {header("Size", "size", "num")}
            {header("Added", "added")}
            <th scope="col">Added by</th>
            <th scope="col">Status</th>
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
                  <button className="linkish" onClick={() => onOpen(f)} title={f.name}>
                    <Icon name="file" />
                    <span className="truncate">{f.name}</span>
                  </button>
                  {f.tags.length > 0 && <span className="cell-tags">{f.tags.map((t) => <span key={t} className="chip chip--sm">{t}</span>)}</span>}
                  </div>
                </th>
                <td className="cell-folder"><span className="truncate" title={f.folder}>{f.folder || <span className="muted">Top level</span>}</span></td>
                <td>{typeLabel[f.type]}</td>
                <td className="num">{formatBytes(f.size)}</td>
                <td className="nowrap">{formatDate(f.added_at)}</td>
                <td className="nowrap">{f.added_by}</td>
                <td>{statusBadge(f)}</td>
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

function FileDrawer({ caseId, file, canEdit, onClose, onChanged }: { caseId: string; file: CaseFile; canEdit: boolean; onClose: () => void; onChanged: (f: CaseFile) => void }) {
  const [tag, setTag] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (tags: string[]) => {
    setBusy(true);
    setError(null);
    try {
      onChanged(await api.editFile(caseId, file.id, tags));
      setTag("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const path = [file.folder, file.name].filter(Boolean).join("/");
  return (
    <Drawer open onOpenChange={(o) => !o && onClose()} title={file.name}>
      <dl className="kv">
        <dt>Status</dt>
        <dd>{statusBadge(file)} <span className="muted">Conversion to Markdown arrives in the next version.</span></dd>
        <dt>Original path</dt>
        <dd className="mono break">{path}</dd>
        <dt>Type</dt>
        <dd>{typeLabel[file.type]} ({file.ext})</dd>
        <dt>Size</dt>
        <dd>{formatBytes(file.size)} <span className="muted">({file.size.toLocaleString()} bytes)</span></dd>
        <dt>Added</dt>
        <dd>{formatDateTime(file.added_at)}{file.added_by ? ` by ${file.added_by}` : ""}</dd>
        <dt>SHA-256</dt>
        <dd className="hash">
          <span className="mono break">{file.sha256}</span>
          <CopyButton text={file.sha256} what="SHA-256 hash" />
        </dd>
      </dl>
      <div className="drawer__section">
        <h3 className="h3">Tags</h3>
        <FormError message={error} />
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
      <div className="drawer__section">
        <a className="btn btn--primary btn--md" href={api.downloadUrl(caseId, file.id)} download>
          <Icon name="download" /><span>Download original</span>
        </a>
        <p className="hint">The file you download is byte for byte what was uploaded. Downloads are recorded in the case's activity.</p>
      </div>
    </Drawer>
  );
}

function NextSteps({ canUpload, onUpload }: { canUpload: boolean; onUpload: () => void }) {
  return (
    <aside className="next" aria-labelledby="next-title">
      <h2 id="next-title" className="next__title">What to do next</h2>
      <ol className="next__list">
        <li className="next__item next__item--current">
          <Icon name="pending" />
          <div>
            <p className="next__label">Upload files or a folder</p>
            {canUpload ? <button className="linkish" onClick={onUpload}>Choose files</button> : <p className="hint">An Editor or the Admin adds files.</p>}
          </div>
        </li>
        <li className="next__item next__item--later">
          <Icon name="lock" />
          <div>
            <p className="next__label">Convert to Markdown, then search</p>
            <p className="hint">Arrives in the next version.</p>
          </div>
        </li>
      </ol>
    </aside>
  );
}
