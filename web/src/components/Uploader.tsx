import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useRef, useState } from "react";
import { formatBytes } from "../format";
import { type PickedFile, type UploadItem, unsupportedMessage, uploadOne } from "../upload";
import { Badge, Button, Icon } from "../ui";

const PARALLEL = 3;

/** Uploads a queue of files and shows each file's result. */
export function useUploader(caseId: string, onStored: () => void) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const nextKey = useRef(1);
  const running = useRef(0);
  const abort = useRef(new AbortController());
  const itemsRef = useRef(items);
  itemsRef.current = items;

  const pump = useCallback(() => {
    while (running.current < PARALLEL) {
      const next = itemsRef.current.find((i) => i.state === "waiting");
      if (!next) return;
      running.current++;
      const key = next.key;
      const update = (patch: Partial<UploadItem>) => {
        itemsRef.current = itemsRef.current.map((i) => (i.key === key ? ({ ...i, ...patch } as UploadItem) : i));
        setItems(itemsRef.current);
      };
      update({ state: "uploading" });
      void uploadOne(caseId, next, abort.current.signal).then((result) => {
        running.current--;
        update(result as Partial<UploadItem>);
        if (result.state === "stored") onStored();
        pump();
      });
    }
  }, [caseId, onStored]);

  const add = useCallback(
    (picked: PickedFile[]) => {
      const added: UploadItem[] = picked.map((p) => {
        const unsupported = unsupportedMessage(p.file.name);
        return { ...p, key: nextKey.current++, ...(unsupported ? { state: "unsupported", message: unsupported } : { state: "waiting" }) } as UploadItem;
      });
      // A new batch after the last one finished starts a fresh list.
      const busy = itemsRef.current.some((i) => i.state === "waiting" || i.state === "uploading");
      itemsRef.current = busy ? [...itemsRef.current, ...added] : added;
      setItems(itemsRef.current);
      pump();
    },
    [pump],
  );

  const retry = useCallback(
    (key?: number) => {
      itemsRef.current = itemsRef.current.map((i) => (i.state === "failed" && (key === undefined || i.key === key) ? ({ ...i, state: "waiting" } as UploadItem) : i));
      setItems(itemsRef.current);
      pump();
    },
    [pump],
  );

  const cancel = useCallback(() => {
    abort.current.abort();
    abort.current = new AbortController();
    itemsRef.current = itemsRef.current.map((i) => (i.state === "waiting" ? ({ ...i, state: "failed", message: "Upload cancelled." } as UploadItem) : i));
    setItems(itemsRef.current);
  }, []);

  const clear = useCallback(() => {
    itemsRef.current = [];
    setItems([]);
  }, []);

  useEffect(() => () => abort.current.abort(), []);

  return { items, add, retry, cancel, clear };
}

export function UploadPanel({ items, onRetry, onCancel, onClose, onShowFile }: { items: UploadItem[]; onRetry: (key?: number) => void; onCancel: () => void; onClose: () => void; onShowFile: (id: string) => void }) {
  const count = (s: UploadItem["state"]) => items.filter((i) => i.state === s).length;
  const done = items.length - count("waiting") - count("uploading");
  const busy = done < items.length;
  const stored = count("stored");
  const failed = count("failed");
  const dup = count("duplicate");
  const unsupported = count("unsupported");
  // Problems first, then the rest in order, so nothing that needs attention scrolls away.
  const rank = { failed: 0, unsupported: 1, duplicate: 2, uploading: 3, waiting: 4, stored: 5 } as const;
  const ordered = busy ? items : [...items].sort((a, b) => rank[a.state] - rank[b.state] || a.key - b.key);
  const scroller = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({ count: ordered.length, getScrollElement: () => scroller.current, estimateSize: () => 36, overscan: 8 });

  return (
    <section className="upload" aria-label="Upload progress">
      <header className="upload__head">
        <div>
          <p className="upload__title" aria-live="polite">
            {busy ? (
              <>Uploading <span className="count">{done.toLocaleString()}</span> of {items.length.toLocaleString()} files</>
            ) : (
              <>Upload finished: {stored.toLocaleString()} added</>
            )}
          </p>
          <p className="upload__summary">
            {[dup && `${dup} already in the case`, unsupported && `${unsupported} unsupported`, failed && `${failed} failed`].filter(Boolean).join(" · ") || (busy ? "Each file is checked as it arrives." : "No problems.")}
          </p>
        </div>
        <div className="btn-row">
          {failed > 0 && !busy && <Button size="sm" icon="refresh" onClick={() => onRetry()}>Retry failed</Button>}
          {busy ? <Button size="sm" onClick={onCancel}>Cancel remaining</Button> : <Button size="sm" variant="quiet" icon="close" onClick={onClose}>Close</Button>}
        </div>
      </header>
      <div className="progress" aria-hidden="true"><div className="progress__bar" style={{ width: `${items.length ? (done / items.length) * 100 : 0}%` }} /></div>
      <div className="upload__list" ref={scroller}>
        <div style={{ height: v.getTotalSize(), position: "relative" }}>
          {v.getVirtualItems().map((row) => {
            const it = ordered[row.index];
            return (
              <div key={it.key} className="upload__row" style={{ transform: `translateY(${row.start}px)` }}>
                <span className="upload__path" title={it.path}>{it.path}</span>
                <span className="upload__size">{formatBytes(it.file.size)}</span>
                <span className="upload__state">
                  {it.state === "waiting" && <Badge tone="idle" icon="pending">Waiting</Badge>}
                  {it.state === "uploading" && <Badge tone="progress" icon="progress">Uploading</Badge>}
                  {it.state === "stored" && <Badge tone="ok" icon="ok">Stored</Badge>}
                  {it.state === "duplicate" && (
                    <button className="linkish" onClick={() => onShowFile(it.existing.id)} title={`Same content as ${[it.existing.folder, it.existing.name].filter(Boolean).join("/")}`}>
                      <Badge tone="dup" icon="dup">Duplicate of {it.existing.name}</Badge>
                    </button>
                  )}
                  {it.state === "unsupported" && <Badge tone="warn" icon="warn" title={it.message}>Unsupported type</Badge>}
                  {it.state === "failed" && (
                    <>
                      <Badge tone="error" icon="error" title={it.message}>Failed</Badge>
                      <span className="upload__why">{it.message}</span>
                      <Button size="sm" variant="quiet" onClick={() => onRetry(it.key)} aria-label={`Retry ${it.path}`}>Retry</Button>
                    </>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

export function DropZone({ onFiles, onPickFiles, onPickFolder, empty }: { onFiles: (dt: DataTransfer) => void; onPickFiles: () => void; onPickFolder: () => void; empty: boolean }) {
  const [over, setOver] = useState(false);
  return (
    <div
      className={`dropzone${over ? " dropzone--over" : ""}${empty ? " dropzone--empty" : ""}`}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        onFiles(e.dataTransfer);
      }}
    >
      <Icon name="upload" />
      <p className="dropzone__title">{empty ? "No files yet." : "Add more files"}</p>
      <p className="dropzone__text">Drag files or a whole folder here, or choose them. Subfolders are kept.</p>
      <div className="btn-row">
        <Button variant={empty ? "primary" : "secondary"} icon="upload" onClick={onPickFiles}>Upload files</Button>
        <Button icon="folder" onClick={onPickFolder}>Upload folder</Button>
      </div>
      <p className="dropzone__hint">Word (.docx), PDF, Outlook (.msg) and .eml emails, text, and images (.png, .jpg, .tif). Up to 2 GB per file.</p>
    </div>
  );
}
