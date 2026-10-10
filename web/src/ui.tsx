import * as Dialog from "@radix-ui/react-dialog";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { useState, type ReactNode } from "react";
import { onLinkClick } from "./router";

// ---------------------------------------------------------------------------
// Icons. Each state has its own shape, so it never relies on colour alone.

const paths = {
  ok: <><circle cx="8" cy="8" r="6.5" /><path d="M5 8.2l2 2 4-4.2" /></>,
  progress: <><circle cx="8" cy="8" r="6.5" /><path d="M8 1.5a6.5 6.5 0 010 13z" fill="currentColor" /></>,
  warn: <><path d="M8 1.8l6.5 12H1.5z" /><path d="M8 6.5v3.2M8 11.6v.1" /></>,
  error: <><path d="M5.3 1.5h5.4l3.8 3.8v5.4l-3.8 3.8H5.3l-3.8-3.8V5.3z" /><path d="M6 6l4 4M10 6l-4 4" /></>,
  stopped: <rect x="3" y="3" width="10" height="10" rx="1" />,
  pending: <circle cx="8" cy="8" r="6.5" strokeDasharray="2.2 2" />,
  dup: <><rect x="2" y="2" width="8.5" height="8.5" rx="1" /><rect x="5.5" y="5.5" width="8.5" height="8.5" rx="1" /></>,
  lock: <><rect x="3" y="7" width="10" height="7.5" rx="1" /><path d="M5.2 7V5a2.8 2.8 0 015.6 0v2" /></>,
  archive: <><rect x="1.5" y="2.5" width="13" height="3.5" rx="0.8" /><path d="M2.5 6v7.5h11V6M6.5 9h3" /></>,
  copy: <><rect x="5.5" y="5.5" width="8" height="8" rx="1" /><path d="M3.5 10.5h-1v-8h8v1" /></>,
  download: <><path d="M8 2v8M4.5 7L8 10.5 11.5 7M2.5 13.5h11" /></>,
  upload: <><path d="M8 11V2.5M4.5 6L8 2.5 11.5 6M2.5 13.5h11" /></>,
  folder: <path d="M1.5 4a1 1 0 011-1h3.6l1.5 1.6h5.9a1 1 0 011 1v7.4a1 1 0 01-1 1H2.5a1 1 0 01-1-1z" />,
  folderOpen: <><path d="M1.5 12.5V4a1 1 0 011-1h3.6l1.5 1.6h5a1 1 0 011 1v1.4" /><path d="M1.5 12.5l2-5.2a1 1 0 01.9-.6h9.6l-2.2 5.8z" /></>,
  file: <><path d="M3.5 1.5h6l3 3v10h-9z" /><path d="M9.5 1.5v3h3" /></>,
  plus: <path d="M8 3v10M3 8h10" />,
  search: <><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5l3.5 3.5" /></>,
  more: <><circle cx="3.5" cy="8" r="0.6" fill="currentColor" /><circle cx="8" cy="8" r="0.6" fill="currentColor" /><circle cx="12.5" cy="8" r="0.6" fill="currentColor" /></>,
  chevronDown: <path d="M4 6l4 4 4-4" />,
  chevronRight: <path d="M6 4l4 4-4 4" />,
  close: <path d="M4 4l8 8M12 4l-8 8" />,
  external: <><path d="M9 2h5v5M14 2L7.5 8.5M12 9.5V14H2V4h4.5" /></>,
  signout: <><path d="M6 2.5H3v11h3M10.5 5L13.5 8l-3 3M13.5 8H6" /></>,
  key: <><circle cx="5" cy="11" r="3" /><path d="M7.2 8.8L13.5 2.5M11 5l2 2M12.5 3.5l1 1" /></>,
  user: <><circle cx="8" cy="5.5" r="3" /><path d="M2.5 14.5a5.5 5.5 0 0111 0" /></>,
  case: <><rect x="1.5" y="4.5" width="13" height="9" rx="1" /><path d="M5.5 4.5V3a1 1 0 011-1h3a1 1 0 011 1v1.5M1.5 8.5h13" /></>,
  shield: <><path d="M8 1.5l5.5 2v4.3c0 3.3-2.4 5.7-5.5 6.7-3.1-1-5.5-3.4-5.5-6.7V3.5z" /><path d="M5.5 8l1.8 1.8L10.8 6.3" /></>,
  tag: <><path d="M1.5 2.5v5l7 7 6-6-7-7h-5a1 1 0 00-1 1z" /><circle cx="5" cy="5" r="1" /></>,
  engine: <><rect x="2" y="3" width="12" height="4" rx="1" /><rect x="2" y="9" width="12" height="4" rx="1" /><path d="M5 5h.01M5 11h.01" /></>,
  refresh: <><path d="M13.5 8a5.5 5.5 0 11-1.6-3.9" /><path d="M13.5 2v3.5H10" /></>,
  sort: <path d="M5 3v10M2.5 10.5L5 13l2.5-2.5M11 13V3M8.5 5.5L11 3l2.5 2.5" />,
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

export type Tone = "ok" | "progress" | "warn" | "error" | "idle" | "dup";

export function Badge({ tone, icon, children, title }: { tone: Tone; icon: IconName; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      <Icon name={icon} />
      <span>{children}</span>
    </span>
  );
}

type ButtonProps = {
  children?: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "quiet" | "danger";
  size?: "md" | "sm";
  icon?: IconName;
  disabled?: boolean;
  title?: string;
  type?: "button" | "submit";
  busy?: boolean;
  form?: string;
  "aria-label"?: string;
};

export function Button({ children, onClick, variant = "secondary", size = "md", icon, disabled, title, type = "button", busy, form, ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      form={form}
      className={`btn btn--${variant} btn--${size}${children ? "" : " btn--icon"}`}
      onClick={onClick}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      title={title}
      aria-label={rest["aria-label"]}
    >
      {icon && <Icon name={icon} />}
      {children && <span>{children}</span>}
    </button>
  );
}

export function Link({ to, children, className }: { to: string; children: ReactNode; className?: string }) {
  return (
    <a href={to} onClick={onLinkClick} className={className}>
      {children}
    </a>
  );
}

export function CopyButton({ text, label = "Copy", what }: { text: string; label?: string; what: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt(`Copy the ${what}:`, text);
    }
  };
  return (
    <Button size="sm" icon="copy" onClick={copy} aria-label={`Copy the ${what}`}>
      <span className="copy-label" aria-live="polite">{copied ? "Copied" : label}</span>
    </Button>
  );
}

// ---------------------------------------------------------------------------
// Dialogs (Radix: focus trap, Escape, focus return).

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer: ReactNode;
  wide?: boolean;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className={`dialog${wide ? " dialog--wide" : ""}`}>
          <Dialog.Title className="dialog__title">{title}</Dialog.Title>
          {description ? <Dialog.Description className="dialog__lead">{description}</Dialog.Description> : <Dialog.Description className="sr-only">{title}</Dialog.Description>}
          {children && <div className="dialog__body">{children}</div>}
          <div className="dialog__actions">{footer}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function Drawer({ open, onOpenChange, title, children }: { open: boolean; onOpenChange: (o: boolean) => void; title: string; children: ReactNode }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay overlay--light" />
        <Dialog.Content className="drawer">
          <div className="drawer__head">
            <Dialog.Title className="drawer__title">{title}</Dialog.Title>
            <Dialog.Close asChild>
              <button className="btn btn--quiet btn--sm btn--icon" aria-label="Close">
                <Icon name="close" />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="sr-only">Details of {title}</Dialog.Description>
          <div className="drawer__body">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ---------------------------------------------------------------------------
// Menus (Radix: arrow keys, typeahead, Escape).

export type MenuItem = { label: string; icon?: IconName; onSelect: () => void; danger?: boolean; disabled?: boolean } | "separator";

export function ActionMenu({ label, items, trigger }: { label: string; items: MenuItem[]; trigger?: ReactNode }) {
  return (
    <Menu.Root>
      <Menu.Trigger asChild>
        {trigger ?? (
          <button className="btn btn--quiet btn--sm btn--icon" aria-label={label} title={label}>
            <Icon name="more" />
          </button>
        )}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content className="menu" align="end" sideOffset={4}>
          {items.map((it, i) =>
            it === "separator" ? (
              <Menu.Separator key={i} className="menu__sep" />
            ) : (
              <Menu.Item key={it.label} className={`menu__item${it.danger ? " menu__item--danger" : ""}`} onSelect={it.onSelect} disabled={it.disabled}>
                {it.icon && <Icon name={it.icon} />}
                {it.label}
              </Menu.Item>
            ),
          )}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

// ---------------------------------------------------------------------------
// States

export function EmptyState({ icon, title, children, action }: { icon: IconName; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} />
      <p className="empty__title">{title}</p>
      {children && <p className="empty__text">{children}</p>}
      {action && <div className="empty__action">{action}</div>}
    </div>
  );
}

export function ErrorPanel({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="problem" role="alert">
      <Icon name="error" />
      <div className="problem__body">
        <p className="problem__what">{message}</p>
        {onRetry && (
          <div className="problem__actions">
            <Button size="sm" onClick={onRetry}>Try again</Button>
          </div>
        )}
      </div>
    </div>
  );
}

export function Skeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton__row" />
      ))}
    </div>
  );
}

export function FormError({ message }: { message: string | null }) {
  return message ? <p className="field__error field__error--block" role="alert">{message}</p> : null;
}

export function Field({ id, label, hint, children }: { id: string; label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint && <p className="field__hint" id={`${id}-hint`}>{hint}</p>}
    </div>
  );
}
