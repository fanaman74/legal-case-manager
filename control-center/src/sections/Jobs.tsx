import type { JobKind, Snapshot } from "../api";
import { Icon, Section } from "../ui";

// Which kinds of background work exist, in the order shown.
const KINDS: { id: string; label: string; idle: string }[] = [
  { id: "convert", label: "Converting files", idle: "Nothing waiting. New uploads are converted to Markdown here." },
  { id: "index", label: "Indexing for search", idle: "Nothing waiting. Converted files are indexed for search here." },
  { id: "pst", label: "Reading mailboxes", idle: "Nothing waiting. Uploaded PST files are read here." },
  { id: "export", label: "Exporting cases", idle: "Nothing waiting." },
];

function describe(k: JobKind): string {
  const parts: string[] = [];
  if (k.running) parts.push(k.total ? `working on ${k.done.toLocaleString()} of ${k.total.toLocaleString()}` : `${k.running.toLocaleString()} running`);
  if (k.queued) parts.push(`${k.queued.toLocaleString()} waiting`);
  if (k.failed) parts.push(`${k.failed.toLocaleString()} failed`);
  return parts.join(" · ");
}

export function Jobs({ snap }: { snap: Snapshot }) {
  const jobs = snap.jobs;
  const worker = snap.services.find((s) => s.id === "worker");
  const appUrl = snap.lan.appUrls[0] ?? "";
  const failed = jobs ? Object.values(jobs.jobs).reduce((n, k) => n + k.failed, 0) : 0;
  const waiting = jobs ? Object.values(jobs.jobs).reduce((n, k) => n + k.queued + k.running, 0) : 0;
  const meta = !jobs ? "No report yet" : failed ? `${failed} failed` : waiting ? `${waiting.toLocaleString()} in progress` : "All done";

  return (
    <Section id="jobs" title="Background jobs" meta={meta}>
      {!jobs ? (
        <p className="empty-inline">
          {worker?.state === "running"
            ? "The background worker hasn't reported yet. This page updates within a few seconds."
            : "Start the Background worker to convert and index files. Its progress shows here."}
        </p>
      ) : (
        <ul className="checks" aria-live="polite">
          {!jobs.fresh && (
            <li className="check check--warn">
              <span className="check__icon tone--warn"><Icon name="warn" label="Warning" /></span>
              <span className="check__label">Out of date</span>
              <span className="check__detail">
                The worker last reported at {new Date(jobs.updated).toLocaleTimeString()}.
                <span className="check__next">If the Background worker is stopped, start it in Services.</span>
              </span>
              <span />
            </li>
          )}
          {KINDS.filter((k) => k.id !== "export" || jobs.jobs[k.id]).map((k) => {
            const counts = jobs.jobs[k.id] ?? { queued: 0, running: 0, failed: 0, done: 0, total: 0 };
            const busy = counts.running > 0 || counts.queued > 0;
            const tone = counts.failed ? "warn" : busy ? "progress" : "ok";
            const icon = counts.failed ? "warn" : busy ? "pending" : "ok";
            return (
              <li key={k.id} className={`check check--${counts.failed ? "warn" : "pass"}`}>
                <span className={`check__icon tone--${tone}`}><Icon name={icon} label={counts.failed ? "Some failed" : busy ? "In progress" : "Idle"} /></span>
                <span className="check__label">{k.label}</span>
                <span className="check__detail">
                  <span className="tabular">{describe(counts) || k.idle}</span>
                  {counts.failed > 0 && (
                    <span className="check__next">
                      {appUrl ? (
                        <>
                          See which files failed and retry them on the <a href={`${appUrl.replace(/\/$/, "")}/jobs`} target="_blank" rel="noreferrer">Jobs page</a> of the web app.
                        </>
                      ) : (
                        "See which files failed and retry them on the Jobs page of the web app."
                      )}
                    </span>
                  )}
                </span>
                <span />
              </li>
            );
          })}
          {jobs.index && (
            <li className={`check check--${jobs.index.problem ? "warn" : "pass"}`}>
              <span className={`check__icon tone--${jobs.index.problem ? "warn" : "ok"}`}><Icon name={jobs.index.problem ? "warn" : "ok"} label={jobs.index.problem ? "Warning" : "Passed"} /></span>
              <span className="check__label">Search index</span>
              <span className="check__detail">
                <span className="tabular">
                  {jobs.index.cases.toLocaleString()} {jobs.index.cases === 1 ? "case" : "cases"}, {jobs.index.chunks.toLocaleString()} passages
                  {jobs.index.model ? `, model ${jobs.index.model}` : ""}
                </span>
                {jobs.index.problem && <span className="check__next">{jobs.index.problem}</span>}
              </span>
              <span />
            </li>
          )}
        </ul>
      )}
    </Section>
  );
}
