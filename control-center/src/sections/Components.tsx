import type { Component, ComponentState, Snapshot } from "../api";
import { elapsed } from "../format";
import type { RunAction } from "../screens/ControlCenter";
import { Button, Icon, Section, useNow, type IconName, type Tone } from "../ui";

const stateIcon: Record<ComponentState, [Tone, IconName, string]> = {
  installed: ["ok", "ok", "Installed"],
  missing: ["idle", "pending", "Not installed"],
  outdated: ["warn", "warn", "Update ready"],
  installing: ["progress", "progress", "Installing"],
  failed: ["error", "error", "Failed"],
};

const actionLabel: Record<ComponentState, string> = {
  installed: "Reinstall",
  missing: "Install",
  outdated: "Update",
  installing: "Installing",
  failed: "Try again",
};

// Components lists what the app needs on this computer and installs or
// repairs it. The launcher also installs anything missing when it starts.
export function Components({ snap, run }: { snap: Snapshot; run: RunAction }) {
  const now = useNow();
  const op = snap.operations.find((o) => o.state === "running");
  const installing = op && (op.action === "component.install" || op.action === "components.install_missing");
  const todo = snap.components.filter((c) => c.canInstall && c.state !== "installed");
  const ready = snap.components.filter((c) => c.state === "installed").length;
  const busyTitle = op ? "Wait for the current action to finish." : undefined;

  return (
    <Section
      id="components"
      title="Components"
      meta={todo.length ? `${ready} of ${snap.components.length} installed` : "Everything is installed"}
      actions={
        todo.length > 0 ? (
          <Button variant="primary" icon={installing ? "progress" : "download"} busy={!!installing} disabled={!!op} title={busyTitle} onClick={() => void run("components.install_missing")}>
            {installing ? "Installing" : todo.length === snap.components.length ? "Install everything" : "Install what's missing"}
          </Button>
        ) : undefined
      }
    >
      {installing && op && (
        <div className="opstrip" aria-live="polite">
          <p className="opstrip__row opstrip__row--running">
            <span className="tone--progress"><Icon name="progress" /></span>
            <span>{op.message} You can close this page; installing carries on.</span>
            <span className="mono opstrip__timer">{elapsed(op.startedAt, now)}</span>
          </p>
        </div>
      )}
      <ul className="checks components">
        {snap.components.map((c) => (
          <ComponentRow key={c.id} c={c} run={run} disabled={!!op} title={busyTitle} />
        ))}
      </ul>
      <p className="hint">
        Downloads come from each program's official release page and are checked against a fixed checksum before they're used. The launcher installs anything missing each time it starts.
      </p>
    </Section>
  );
}

function ComponentRow({ c, run, disabled, title }: { c: Component; run: RunAction; disabled: boolean; title?: string }) {
  const [tone, icon, label] = stateIcon[c.state];
  const level = c.state === "installed" ? "pass" : c.state === "failed" ? "fail" : c.state === "outdated" ? "warn" : "pending";
  return (
    <li className={`check check--${level}`}>
      <span className={`check__icon tone--${tone}`}><Icon name={icon} label={label} /></span>
      <span className="check__label">
        {c.name}
        {c.version && <span className="components__version mono">{c.version}</span>}
        <span className="check__next">{c.purpose}</span>
      </span>
      <span className="check__detail" aria-live={c.state === "installing" ? "polite" : undefined}>{c.detail}</span>
      <span className="check__action">
        {c.canInstall && c.state !== "installing" && (
          <Button
            size="sm"
            variant={c.state === "installed" ? "quiet" : "secondary"}
            icon={c.state === "installed" ? "refresh" : "download"}
            disabled={disabled}
            title={title}
            onClick={() => void run("component.install", { component: c.id })}
            aria-label={`${actionLabel[c.state]} ${c.name}`}
          >
            {actionLabel[c.state]}
          </Button>
        )}
      </span>
    </li>
  );
}
