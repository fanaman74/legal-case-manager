import { Fragment, useState } from "react";
import type { Service, Snapshot } from "../api";
import { ago, bytes, clock, duration, elapsed } from "../format";
import type { RunAction } from "../screens/ControlCenter";
import { Button, ConfirmDialog, Icon, ProblemPanel, Section, ServiceBadge, useNow } from "../ui";

type Pending = { kind: "stop_all" } | { kind: "stop"; service: Service } | null;

export function Services({ snap, run, isAdmin }: { snap: Snapshot; run: RunAction; isAdmin: boolean }) {
  const now = useNow();
  const [confirm, setConfirm] = useState<Pending>(null);
  const op = snap.operations.find((o) => o.state === "running");
  const lastDone = snap.operations.find((o) => o.state !== "running");
  const showLast = lastDone?.finishedAt && now - Date.parse(lastDone.finishedAt) < 120_000;
  const running = snap.services.filter((s) => s.state === "running").length;
  const busy = !!op;
  const dockerDown = !snap.docker.ok;
  const busyTitle = busy ? "Wait for the current action to finish." : dockerDown ? "Start Docker Desktop first." : undefined;

  const doConfirm = () => {
    if (!confirm) return;
    if (confirm.kind === "stop_all") void run("stack.stop_all");
    else void run("service.stop", { service: confirm.service.id });
    setConfirm(null);
  };

  return (
    <Section
      id="services"
      title="Services"
      meta={`${running} of ${snap.services.length} running`}
      actions={
        <>
          <Button variant="primary" icon="play" onClick={() => void run("stack.start_all")} disabled={busy || dockerDown} title={busyTitle}>
            Start all
          </Button>
          {isAdmin && (
            <Button icon="stop" onClick={() => setConfirm({ kind: "stop_all" })} disabled={busy || dockerDown || running === 0} title={busyTitle}>
              Stop all
            </Button>
          )}
        </>
      }
    >
      <div className="opstrip" aria-live="polite">
        {op ? (
          <p className="opstrip__row opstrip__row--running">
            <span className="tone--progress"><Icon name="progress" /></span>
            <span>{op.message}{op.action === "stack.start_all" ? " The first start can take a few minutes." : ""}</span>
            <span className="mono opstrip__timer">{elapsed(op.startedAt, now)}</span>
          </p>
        ) : showLast && lastDone ? (
          <p className={`opstrip__row opstrip__row--${lastDone.state}`}>
            <span className={lastDone.state === "succeeded" ? "tone--ok" : "tone--error"}>
              <Icon name={lastDone.state === "succeeded" ? "ok" : "error"} />
            </span>
            <span>{lastDone.message}</span>
            <span className="mono opstrip__timer">{clock(lastDone.finishedAt!)}</span>
          </p>
        ) : null}
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Service</th>
              <th scope="col">State</th>
              <th scope="col" className="num">Uptime</th>
              <th scope="col">Version</th>
              <th scope="col" className="num">CPU</th>
              <th scope="col" className="num">Memory</th>
              <th scope="col">Last check</th>
              {isAdmin && <th scope="col"><span className="sr-only">Actions</span></th>}
            </tr>
          </thead>
          <tbody>
            {snap.services.map((s) => (
              <Fragment key={s.id}>
                <tr className={s.state === "error" ? "row--error" : undefined}>
                  <th scope="row">
                    <div className="svc">
                      <span className="svc__name">{s.name}</span>
                      <span className="svc__purpose">{s.purpose}</span>
                    </div>
                  </th>
                  <td>
                    <ServiceBadge state={s.state} label={s.detail} />
                    {s.autoRestartAt && (
                      <span className="svc__note">Restarted automatically at {clock(s.autoRestartAt)} after a crash</span>
                    )}
                  </td>
                  <td className="num mono">{s.startedAt ? duration(now - Date.parse(s.startedAt)) : <Dash />}</td>
                  <td className="mono">{s.version || <Dash />}</td>
                  <td className="num mono">{s.usage ? `${s.usage.cpuPercent.toFixed(1)}%` : <Dash />}</td>
                  <td className="num mono">{s.usage ? bytes(s.usage.memoryBytes) : <Dash />}</td>
                  <td className="mono muted">{s.lastCheck && s.state !== "stopped" ? ago(s.lastCheck, now) : <Dash />}</td>
                  {isAdmin && (
                    <td className="row-actions">
                      <RowActions s={s} run={run} disabled={busy || dockerDown} title={busyTitle} onStop={() => setConfirm({ kind: "stop", service: s })} />
                    </td>
                  )}
                </tr>
                {s.problem && (
                  <tr className="row--problem">
                    <td colSpan={isAdmin ? 8 : 7}>
                      <ProblemPanel
                        compact
                        problem={s.problem}
                        onFix={s.problem.fixAction ? () => void run(s.problem!.fixAction!, { service: s.id }) : undefined}
                        fixBusy={busy}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      <ConfirmDialog
        open={!!confirm}
        title={confirm?.kind === "stop_all" ? "Stop all services?" : `Stop ${confirm?.kind === "stop" ? confirm.service.name : ""}?`}
        body={
          <p>
            {confirm?.kind === "stop_all" || (confirm?.kind === "stop" && confirm.service.id === "api")
              ? "People using the case manager lose access until you start the services again. Background jobs pause and resume when you restart."
              : "Work that depends on this service pauses until you start it again."}
          </p>
        }
        confirmLabel={confirm?.kind === "stop_all" ? "Stop all services" : "Stop service"}
        danger
        onConfirm={doConfirm}
        onCancel={() => setConfirm(null)}
      />
    </Section>
  );
}

function Dash() {
  return <span className="muted" aria-label="Not available">–</span>;
}

function RowActions({ s, run, disabled, title, onStop }: { s: Service; run: RunAction; disabled: boolean; title?: string; onStop: () => void }) {
  if (s.state === "stopped") {
    return (
      <Button size="sm" variant="quiet" icon="play" disabled={disabled} title={title} onClick={() => void run("service.start", { service: s.id })} aria-label={`Start ${s.name}`}>
        Start
      </Button>
    );
  }
  return (
    <span className="btn-group">
      <Button size="sm" variant="quiet" icon="restart" disabled={disabled} title={title} onClick={() => void run("service.restart", { service: s.id })} aria-label={`Restart ${s.name}`}>
        Restart
      </Button>
      <Button size="sm" variant="quiet" icon="stop" disabled={disabled || s.state === "starting"} title={title} onClick={onStop} aria-label={`Stop ${s.name}`}>
        Stop
      </Button>
    </span>
  );
}
