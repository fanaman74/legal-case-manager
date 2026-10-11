import { useEffect, useState } from "react";
import { api, type CaseExport } from "../api";
import { formatBytes, formatDate, formatDateTime, plural, shortHash } from "../format";
import { useLoad } from "../hooks";
import { Badge, Button, ErrorPanel, FormError, Icon, Modal, Skeleton } from "../ui";

/** Export a case as one ZIP (Admin). The worker builds it; this shows how
 * it's going and offers the download. */
export function ExportDialog({ caseId, caseName, open, onOpenChange }: { caseId: string; caseName: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const list = useLoad(() => (open ? api.exports(caseId) : Promise.resolve(null)), [caseId, open]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const items = list.data?.items ?? [];
  const working = items.some((e) => e.state === "queued" || e.state === "building");

  useEffect(() => {
    if (!open || !working) return;
    const t = setInterval(() => void list.reload(), 2000);
    return () => clearInterval(t);
  }, [open, working, list.reload]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.newExport(caseId);
      await list.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async (x: CaseExport) => {
    setError(null);
    try {
      await api.deleteExport(caseId, x.id);
      await list.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Export case as ZIP"
      wide
      description={
        <>
          One ZIP with everything in <strong>{caseName}</strong>: the original files byte for byte, the Markdown, the case details and
          file list (JSON), saved AI reviews, and the case's audit trail, with a list of SHA-256 checksums.
        </>
      }
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Close</Button>
          <Button variant="primary" icon="download" busy={busy} disabled={working || list.loading && !list.data} onClick={() => void start()}>
            {items.length ? "Make a new export" : "Make the export"}
          </Button>
        </>
      }
    >
      <FormError message={error} />
      {list.error ? (
        <ErrorPanel message={list.error} onRetry={() => void list.reload()} />
      ) : !list.data ? (
        <Skeleton rows={2} />
      ) : items.length === 0 ? (
        <p className="muted">
          The export is made in the background, so you can close this window meanwhile. It contains confidential material: store and share
          it as you would the case file. Exports are kept here for {list.data.keep_days} days, then deleted.
        </p>
      ) : (
        <ul className="exports">
          {items.map((x) => (
            <li key={x.id} className="exports__item">
              <ExportState x={x} />
              <div className="exports__info">
                <p>
                  {formatDateTime(x.created_at)} <span className="muted">by {x.username}</span>
                </p>
                {x.state === "ready" && (
                  <p className="hint">
                    {formatBytes(x.size ?? 0)} · {plural(x.files ?? 0, "file")} · SHA-256 <span className="mono" title={x.sha256 ?? ""}>{shortHash(x.sha256 ?? "")}</span> · kept until {formatDate(x.expires_at)}
                  </p>
                )}
                {x.state === "building" && x.progress && x.progress.total > 0 && (
                  <span className="progress-line__bar" aria-hidden="true"><span style={{ width: `${Math.round((x.progress.done / x.progress.total) * 100)}%` }} /></span>
                )}
                {x.state === "failed" && <p className="hint">{x.error}</p>}
              </div>
              <div className="btn-row">
                {x.state === "ready" && (
                  <a className="btn btn--primary btn--sm" href={api.exportUrl(caseId, x.id)} download>
                    <Icon name="download" /><span>Download</span>
                  </a>
                )}
                {(x.state === "ready" || x.state === "failed") && (
                  <Button size="sm" variant="quiet" onClick={() => void remove(x)} aria-label={`Delete the export made ${formatDateTime(x.created_at)}`}>Delete</Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}

function ExportState({ x }: { x: CaseExport }) {
  switch (x.state) {
    case "queued":
      return <Badge tone="idle" icon="pending">Waiting</Badge>;
    case "building":
      return <Badge tone="progress" icon="progress">Making the ZIP</Badge>;
    case "ready":
      return <Badge tone="ok" icon="ok">Ready</Badge>;
    default:
      return <Badge tone="error" icon="error">Failed</Badge>;
  }
}
