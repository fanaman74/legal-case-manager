import * as Menu from "@radix-ui/react-dropdown-menu";
import { useEffect, useState, type ReactNode } from "react";
import { api, type JobCounts } from "../api";
import { roleLabel } from "../format";
import { useApp } from "../hooks";
import { href, navigate, onLinkClick, type Route } from "../router";
import { Icon, type IconName } from "../ui";

/** The Control Center only opens on the host computer. */
function controlCenterUrl(): string | null {
  const h = location.hostname;
  return h === "localhost" || h === "127.0.0.1" || h === "[::1]" ? "https://localhost:8443/" : null;
}

export function Shell({ route, children }: { route: Route; children: ReactNode }) {
  const { user, cases, signOut } = useApp();
  const isAdmin = user.role === "admin";
  const current = route.page === "case" || route.page === "document" ? cases.find((c) => c.id === route.id) : undefined;
  const cc = isAdmin ? controlCenterUrl() : null;

  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <header className="topbar">
        <a className="topbar__brand" href="/" onClick={onLinkClick}>
          <span className="brand__mark" aria-hidden="true" />
          <span className="brand__name">Case File Manager</span>
        </a>
        <div className="topbar__middle">
          <Menu.Root>
            <Menu.Trigger className="switcher" aria-label="Switch case">
              <Icon name="case" />
              <span className="switcher__label">{current ? current.name : "Go to a case"}</span>
              <Icon name="chevronDown" />
            </Menu.Trigger>
            <Menu.Portal>
              <Menu.Content className="menu menu--wide" align="start" sideOffset={4}>
                {cases.length === 0 && <div className="menu__empty">No cases yet.</div>}
                {cases.slice(0, 30).map((c) => (
                  <Menu.Item key={c.id} className="menu__item" onSelect={() => navigate(href({ page: "case", id: c.id, tab: "files", folder: "" }))}>
                    <span className="menu__main">{c.name}</span>
                    {c.reference && <span className="menu__meta">{c.reference}</span>}
                  </Menu.Item>
                ))}
                <Menu.Separator className="menu__sep" />
                <Menu.Item className="menu__item" onSelect={() => navigate("/")}>All cases</Menu.Item>
              </Menu.Content>
            </Menu.Portal>
          </Menu.Root>
        </div>
        <WorkStatus />
        <Menu.Root>
          <Menu.Trigger className="usermenu" aria-label={`Account: ${user.display_name}`}>
            <Icon name="user" />
            <span className="usermenu__name">{user.display_name}</span>
            <span className="usermenu__role">{roleLabel[user.role]}</span>
            <Icon name="chevronDown" />
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Content className="menu" align="end" sideOffset={4}>
              <Menu.Item className="menu__item" onSelect={() => navigate("/password")}>
                <Icon name="key" /> Change password
              </Menu.Item>
              <Menu.Separator className="menu__sep" />
              <Menu.Item className="menu__item" onSelect={signOut}>
                <Icon name="signout" /> Sign out
              </Menu.Item>
            </Menu.Content>
          </Menu.Portal>
        </Menu.Root>
      </header>
      <nav className="rail" aria-label="Main">
        <div className="rail__inner">
          <ul>
            <RailLink to="/" icon="case" label="Cases" active={route.page === "cases" || route.page === "case" || route.page === "document"} />
          </ul>
          {isAdmin && (
            <>
              <p className="rail__group">Admin</p>
              <ul>
                <RailLink to="/people" icon="user" label="People" active={route.page === "people"} />
                <RailLink to="/jobs" icon="jobs" label="Jobs" active={route.page === "jobs"} />
                <RailLink to="/audit" icon="shield" label="Audit log" active={route.page === "audit"} />
                {cc && (
                  <li>
                    <a className="rail__link" href={cc} target="_blank" rel="noreferrer">
                      <span className="rail__label"><Icon name="engine" />Control Center</span>
                      <Icon name="external" label="opens in a new tab" />
                    </a>
                  </li>
                )}
              </ul>
            </>
          )}
        </div>
      </nav>
      <main id="main" className="content" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}

function RailLink({ to, icon, label, active }: { to: string; icon: IconName; label: string; active: boolean }) {
  return (
    <li>
      <a className={`rail__link${active ? " rail__link--active" : ""}`} href={to} onClick={onLinkClick} aria-current={active ? "page" : undefined}>
        <span className="rail__label"><Icon name={icon} />{label}</span>
      </a>
    </li>
  );
}

const VERBS: Record<string, string> = { convert: "Converting", index: "Indexing", pst: "Reading mailbox", export: "Exporting" };

/** "Converting 12 of 80 files": background work in the person's own cases.
 * Fixed width, so updates never move the top bar. */
function WorkStatus() {
  const [jobs, setJobs] = useState<Record<string, JobCounts>>({});
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      let busy = false;
      try {
        const s = await api.status();
        if (!stop) setJobs(s.jobs);
        busy = Object.values(s.jobs).some((k) => k.queued + k.running > 0);
      } catch {
        // the page shows its own errors
      }
      if (!stop) timer = setTimeout(() => void tick(), busy ? 3000 : 20000);
    };
    void tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, []);
  const active = Object.entries(jobs).find(([, k]) => k.queued + k.running > 0);
  const failed = Object.values(jobs).reduce((n, k) => n + k.failed, 0);
  let text = "";
  if (active) {
    const [kind, k] = active;
    const left = k.queued + k.running;
    text = kind === "pst" && k.total ? `${VERBS[kind]}: ${k.done.toLocaleString()} of ${k.total.toLocaleString()} emails` : `${VERBS[kind] ?? "Working"}: ${left.toLocaleString()} ${left === 1 ? "file" : "files"} to go`;
  }
  return (
    <div className="workstatus" role="status" aria-live="polite">
      {active && <><Icon name="progress" /><span className="workstatus__text">{text}</span></>}
      {!active && failed > 0 && <><Icon name="warn" /><span className="workstatus__text">{failed.toLocaleString()} {failed === 1 ? "file needs" : "files need"} attention</span></>}
    </div>
  );
}
