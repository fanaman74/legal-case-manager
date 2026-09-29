import { useState, type FormEvent } from "react";
import { api, type Session, type Snapshot, type WizardStep } from "../api";
import type { RunAction } from "../screens/ControlCenter";
import { Badge, Button, checkIcon, Icon, Section, stepIcon } from "../ui";

export function Wizard({
  snap,
  session,
  onSession,
  run,
  collapsed,
}: {
  snap: Snapshot;
  session: Session;
  onSession: (s: Session) => void;
  run: RunAction;
  collapsed: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const steps = snap.wizard;
  // The current step is the first that isn't done; nothing after it can start.
  const current = steps.findIndex((s) => s.state !== "done");
  const done = steps.filter((s) => s.state === "done").length;

  if (collapsed && !expanded) {
    const attention = steps.slice(0, 3).find((s) => s.state !== "done");
    return (
      <section id="setup" className={`setup-summary${attention ? " setup-summary--attention" : ""}`} aria-label="Setup">
        <Icon name={attention ? "warn" : "ok"} />
        <p>
          <strong>First-run setup: {done} of {steps.length} steps done.</strong>{" "}
          {attention ? `“${attention.title}” needs attention again. See below.` : "The rest become available as the app grows."}
        </p>
        <Button size="sm" variant="quiet" onClick={() => setExpanded(true)}>Show steps</Button>
      </section>
    );
  }

  return (
    <Section
      id="setup"
      title="First-run setup"
      meta={`${done} of ${steps.length} steps done`}
      actions={collapsed ? <Button size="sm" variant="quiet" onClick={() => setExpanded(false)}>Hide steps</Button> : undefined}
    >
      <ol className="steps">
        {steps.map((step, i) => (
          <Step
            key={step.id}
            step={step}
            current={i === current}
            locked={current >= 0 && i > current && step.state !== "done"}
            snap={snap}
            session={session}
            onSession={onSession}
            run={run}
          />
        ))}
      </ol>
    </Section>
  );
}

function Step({
  step,
  current,
  locked,
  snap,
  session,
  onSession,
  run,
}: {
  step: WizardStep;
  current: boolean;
  locked: boolean;
  snap: Snapshot;
  session: Session;
  onSession: (s: Session) => void;
  run: RunAction;
}) {
  const [tone, icon, label] = stepIcon[step.state];
  const showBody = current && step.state !== "unavailable";
  return (
    <li className={`step step--${step.state}${current ? " step--current" : ""}${locked ? " step--locked" : ""}`} aria-current={current ? "step" : undefined}>
      <div className="step__head">
        <span className="step__n" aria-hidden="true">{step.n}</span>
        <div className="step__titles">
          <h3 className="step__title">{step.title}</h3>
          {!(current && step.id === "services" && step.state !== "failed") && (
            <p className="step__detail">{locked && step.state !== "unavailable" ? "Finish the step above first." : step.detail}</p>
          )}
        </div>
        <Badge tone={tone} icon={icon}>{label}</Badge>
      </div>
      {showBody && (
        <div className="step__body">
          {step.id === "prerequisites" && <Prerequisites snap={snap} />}
          {step.id === "services" && <StartServices snap={snap} run={run} />}
          {step.id === "admin" && session.state === "setup" && <CreateAdmin onSession={onSession} />}
        </div>
      )}
    </li>
  );
}

function Prerequisites({ snap }: { snap: Snapshot }) {
  const rows = snap.checks.filter((c) => ["docker", "disk", "port"].includes(c.id));
  return (
    <>
      <ul className="mini-checks">
        {rows.map((c) => {
          const [tone, icon, label] = checkIcon[c.level];
          return (
            <li key={c.id}>
              <span className={`tone--${tone}`}><Icon name={icon} label={label} /></span>
              <span className="mini-checks__label">{c.label}</span>
              <span className="mini-checks__detail">{c.detail}{c.next ? ` ${c.next}` : ""}</span>
            </li>
          );
        })}
      </ul>
      <p className="hint">These checks re-run every few seconds. The step completes on its own once they pass.</p>
    </>
  );
}

function StartServices({ snap, run }: { snap: Snapshot; run: RunAction }) {
  const op = snap.operations.find((o) => o.state === "running");
  const running = snap.services.filter((s) => s.state === "running").length;
  return (
    <div className="step__action">
      <Button variant="primary" icon={op ? "progress" : "play"} onClick={() => void run("stack.start_all")} busy={!!op} disabled={!snap.docker.ok}>
        {op ? (op.action.endsWith("stop") || op.action.endsWith("stop_all") ? "Stopping" : "Starting services") : snap.wizard[1]?.state === "failed" ? "Try again" : "Start all services"}
      </Button>
      <span className="hint" aria-live="polite">{running} of {snap.services.length} running</span>
      {!snap.docker.ok && <span className="hint">Start Docker Desktop first.</span>}
    </div>
  );
}

function CreateAdmin({ onSession }: { onSession: (s: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mismatch = confirm.length > 0 && confirm !== password;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (password !== confirm) {
      setError("The two passwords don't match. Type the same password in both boxes.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onSession(await api.createAdmin(username, password));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form-grid" onSubmit={submit} noValidate>
      {error && <p className="field__error field__error--block" role="alert">{error}</p>}
      <div className="field">
        <label htmlFor="admin-username">Username</label>
        <input id="admin-username" className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
      </div>
      <div className="field">
        <label htmlFor="admin-password">Password</label>
        <input id="admin-password" type="password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" aria-describedby="admin-password-hint" />
        <p id="admin-password-hint" className="field__hint">At least 12 characters. A short sentence is easy to remember and hard to guess.</p>
      </div>
      <div className="field">
        <label htmlFor="admin-confirm">Type the password again</label>
        <input id="admin-confirm" type="password" className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" aria-invalid={mismatch || undefined} />
        {mismatch && <p className="field__hint field__hint--error">Doesn't match yet.</p>}
      </div>
      <div>
        <Button type="submit" variant="primary" busy={busy} disabled={!username || password.length < 12 || password !== confirm}>
          Create Admin account
        </Button>
      </div>
    </form>
  );
}
