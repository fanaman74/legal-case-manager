import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { CheckLevel, Problem, ServiceState, StepState } from "./api";

// ---------------------------------------------------------------------------
// Icons. Each state has its own shape so it never relies on colour alone.

const paths = {
  ok: <><circle cx="8" cy="8" r="6.5" /><path d="M5 8.2l2 2 4-4.2" /></>,
  running: <><circle cx="8" cy="8" r="6.5" /><circle cx="8" cy="8" r="3" fill="currentColor" stroke="none" /></>,
  progress: <><circle cx="8" cy="8" r="6.5" /><path d="M8 1.5a6.5 6.5 0 010 13z" fill="currentColor" /></>,
  warn: <><path d="M8 1.8l6.5 12H1.5z" /><path d="M8 6.5v3.2M8 11.6v.1" /></>,
  error: <><path d="M5.3 1.5h5.4l3.8 3.8v5.4l-3.8 3.8H5.3l-3.8-3.8V5.3z" /><path d="M6 6l4 4M10 6l-4 4" /></>,
  stopped: <rect x="3" y="3" width="10" height="10" rx="1" />,
  pending: <><circle cx="8" cy="8" r="6.5" strokeDasharray="2.2 2" /></>,
  lock: <><rect x="3" y="7" width="10" height="7.5" rx="1" /><path d="M5.2 7V5a2.8 2.8 0 015.6 0v2" /></>,
  copy: <><rect x="5.5" y="5.5" width="8" height="8" rx="1" /><path d="M3.5 10.5h-1v-8h8v1" /></>,
  download: <><path d="M8 2v8M4.5 7L8 10.5 11.5 7M2.5 13.5h11" /></>,
  refresh: <><path d="M13.5 8a5.5 5.5 0 11-1.6-3.9" /><path d="M13.5 2v3.5H10" /></>,
  play: <path d="M5 3l8 5-8 5z" />,
  stop: <rect x="4" y="4" width="8" height="8" rx="1" />,
  restart: <><path d="M2.5 8a5.5 5.5 0 105.5-5.5H5.5" /><path d="M7 1L5 2.5 7 4" /></>,
  external: <><path d="M9 2h5v5M14 2L7.5 8.5M12 9.5V14H2V4h4.5" /></>,
  signout: <><path d="M6 2.5H3v11h3M10.5 5L13.5 8l-3 3M13.5 8H6" /></>,
  docker: <><rect x="2" y="7" width="12" height="6" rx="1" /><path d="M4 7V5h2v2M7 7V5h2v2M7 5V3h2v2" /></>,
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, label }: { name: IconName; label?: string }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {paths[name]}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Badges

type Tone = "ok" | "progress" | "warn" | "error" | "idle";

export function Badge({ tone, icon, children }: { tone: Tone; icon: IconName; children: ReactNode }) {
  return (
    <span className={`badge badge--${tone}`}>
      <Icon name={icon} />
      <span>{children}</span>
    </span>
  );
}

export function ServiceBadge({ state, label }: { state: ServiceState; label: string }) {
  const map: Record<ServiceState, [Tone, IconName]> = {
    running: ["ok", "running"],
    starting: ["progress", "progress"],
    stopped: ["idle", "stopped"],
    error: ["error", "error"],
  };
  const [tone, icon] = map[state];
  return <Badge tone={tone} icon={icon}>{label}</Badge>;
}

export const checkIcon: Record<CheckLevel, [Tone, IconName, string]> = {
  pass: ["ok", "ok", "Passed"],
  warn: ["warn", "warn", "Warning"],
  fail: ["error", "error", "Failed"],
  pending: ["idle", "pending", "Not yet"],
};

export const stepIcon: Record<StepState, [Tone, IconName, string]> = {
  done: ["ok", "ok", "Done"],
  in_progress: ["progress", "progress", "In progress"],
  failed: ["error", "error", "Failed"],
  todo: ["idle", "pending", "To do"],
  unavailable: ["idle", "lock", "Not available yet"],
};

// ---------------------------------------------------------------------------
// Buttons

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "quiet" | "danger";
  size?: "md" | "sm";
  icon?: IconName;
  disabled?: boolean;
  title?: string;
  type?: "button" | "submit";
  busy?: boolean;
  "aria-label"?: string;
};

export function Button({ children, onClick, variant = "secondary", size = "md", icon, disabled, title, type = "button", busy, ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={`btn btn--${variant} btn--${size}`}
      onClick={onClick}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      title={title}
      aria-label={rest["aria-label"]}
    >
      {icon && <Icon name={icon} />}
      <span>{children}</span>
    </button>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt("Copy this address:", text);
    }
  };
  return (
    <Button size="sm" variant="secondary" icon="copy" onClick={copy} aria-label={`${label} ${text}`}>
      <span className="copy-label" aria-live="polite">{copied ? "Copied" : label}</span>
    </Button>
  );
}

// ---------------------------------------------------------------------------
// Problem panel: what happened, why, next step, optional fix button.

export function ProblemPanel({ problem, onFix, fixBusy, compact }: { problem: Problem; onFix?: () => void; fixBusy?: boolean; compact?: boolean }) {
  return (
    <div className={`problem${compact ? " problem--compact" : ""}`} role="alert">
      <Icon name="error" />
      <div className="problem__body">
        <p className="problem__what">{problem.what}</p>
        <p><span className="problem__label">Why: </span>{problem.why}</p>
        <p><span className="problem__label">Next step: </span>{problem.next}</p>
        {problem.fixAction && onFix && (
          <div className="problem__actions">
            <Button variant="primary" size="sm" icon="restart" onClick={onFix} busy={fixBusy}>
              {problem.fixLabel ?? "Fix"}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Confirm dialog (native <dialog>: focus trap and Escape come for free).

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  danger,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="dialog" aria-labelledby={titleId} onCancel={(e) => { e.preventDefault(); onCancel(); }}>
      <h2 id={titleId} className="dialog__title">{title}</h2>
      <div className="dialog__body">{body}</div>
      <div className="dialog__actions">
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant={danger ? "danger" : "primary"} onClick={onConfirm}>{confirmLabel}</Button>
      </div>
    </dialog>
  );
}

// ---------------------------------------------------------------------------

export function Section({ id, title, meta, actions, children }: { id: string; title: string; meta?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className="section" aria-labelledby={`${id}-title`}>
      <header className="section__head">
        <div className="section__titles">
          <h2 id={`${id}-title`} className="section__title">{title}</h2>
          {meta && <span className="section__meta">{meta}</span>}
        </div>
        {actions && <div className="section__actions">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}
