import { useEffect, useState } from "react";
import { api, type Job } from "../api";
import { formatDateTime } from "../format";
import { useLoad, useTitle } from "../hooks";
import { href } from "../router";
import { Badge, Button, EmptyState, ErrorPanel, Icon, Link, Skeleton } from "../ui";

const STATES: { id: string; label: string }[] = [
  { id: "", label: "All" },
  { id: "failed", label: "Failed" },
  { id: "running", label: "Running" },
  { id: "queued", label: "Waiting" },
  { id: "done", label: "Done" },
];

function stateBadge(j: Job) {
  switch (j.state) {
    case "running":
      return <Badge tone="progress" icon="progress">{j.total ? `${j.done.toLocaleString()} of ${j.total.toLocaleString()}` : "Running"}</Badge>;
    case "queued":
      return <Badge tone="idle" icon="pending">Waiting</Badge>;
    case "failed":
      return <Badge tone="error" icon="error">Failed</Badge>;
    default:
      return <Badge tone="ok" icon="ok">Done</Badge>;
  }
}

/** Background work across every case (Admin). */
export function Jobs() {
  useTitle("Jobs");
  const [state, setState] = useState("");
  const { data, error, reload } = useLoad(() => api.jobs(state || undefined), [state]);
  const [busy, setBusy] = useState<number | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    const t = setInterval(() => void reload(), 5000);
    return () => clearInterval(t);
  }, [reload]);

  const retry = async (id: number) => {
    setBusy(id);
    setProblem(null);
    try {
      await api.retryJob(id);
      await reload();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const counts = data ? Object.values(data.counts) : [];
  const failed = counts.reduce((n, c) => n + c.failed, 0);
  const waiting = counts.reduce((n, c) => n + c.queued + c.running, 0);

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1 className="page__title">Jobs</h1>
          <p className="page__meta">Conversion, indexing and other background work in every case. {data && (failed ? `${failed} failed.` : waiting ? `${waiting.toLocaleString()} in progress.` : "Nothing waiting.")}</p>
        </div>
      </header>
      <div className="segmented" role="group" aria-label="Show">
        {STATES.map((s) => (
          <button key={s.id} className="segmented__item" aria-pressed={state === s.id} onClick={() => setState(s.id)}>{s.label}</button>
        ))}
      </div>
      {problem && <ErrorPanel message={problem} />}
      {error && !data && <ErrorPanel message={error} onRetry={reload} />}
      {!data && !error && <Skeleton rows={8} />}
      {data && data.items.length === 0 && (
        <EmptyState icon="ok" title={state === "failed" ? "No failed jobs." : "No jobs to show."}>Files are converted and indexed in the background as they are uploaded.</EmptyState>
      )}
      {data && data.items.length > 0 && (
        <div className="table-wrap">
          <table className="table table--compact">
            <thead>
              <tr>
                <th scope="col">Work</th>
                <th scope="col">File</th>
                <th scope="col">Case</th>
                <th scope="col">Status</th>
                <th scope="col">Started</th>
                <th scope="col"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((j) => (
                <tr key={j.id}>
                  <td className="nowrap">{j.label}</td>
                  <td className="cell-folder">
                    {j.file && j.case_id && j.file_id ? <span className="truncate" title={j.file}>{j.file}</span> : <span className="muted">Whole case</span>}
                    {j.state === "failed" && j.error && <span className="cell-error">{j.error}</span>}
                  </td>
                  <td className="nowrap">{j.case_id && j.case_name ? <Link to={href({ page: "case", id: j.case_id, tab: "files", folder: "" })}>{j.case_name}</Link> : <span className="muted">Deleted</span>}</td>
                  <td>{stateBadge(j)}</td>
                  <td className="nowrap">{formatDateTime(j.started_at ?? j.created_at)}</td>
                  <td>{j.state === "failed" && <Button size="sm" icon="refresh" busy={busy === j.id} onClick={() => void retry(j.id)}>Retry</Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="hint"><Icon name="lock" /> Only the Admin sees this page. Other people see a short status line at the top of the app.</p>
    </div>
  );
}
