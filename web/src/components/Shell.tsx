import * as Menu from "@radix-ui/react-dropdown-menu";
import type { ReactNode } from "react";
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
  const current = route.page === "case" ? cases.find((c) => c.id === route.id) : undefined;
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
            <RailLink to="/" icon="case" label="Cases" active={route.page === "cases" || route.page === "case"} />
          </ul>
          {isAdmin && (
            <>
              <p className="rail__group">Admin</p>
              <ul>
                <RailLink to="/people" icon="user" label="People" active={route.page === "people"} />
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
