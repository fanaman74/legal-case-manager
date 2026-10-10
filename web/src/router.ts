// A small router: every page has a stable address that can be bookmarked
// or shared (a case, a case tab, a folder in a case).
import { useEffect, useState } from "react";

export type Route =
  | { page: "cases" }
  | { page: "case"; id: string; tab: CaseTab; folder: string }
  | { page: "people" }
  | { page: "audit" }
  | { page: "password" }
  | { page: "not-found" };

export type CaseTab = "files" | "details" | "people" | "activity";
const TABS: CaseTab[] = ["files", "details", "people", "activity"];

export function parse(pathname: string, search: string): Route {
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length === 0 || (parts.length === 1 && parts[0] === "cases")) return { page: "cases" };
  if (parts[0] === "cases" && parts.length <= 3 && /^[0-9a-f]{32}$/.test(parts[1])) {
    const tab = (parts[2] ?? "files") as CaseTab;
    if (!TABS.includes(tab)) return { page: "not-found" };
    return { page: "case", id: parts[1], tab, folder: new URLSearchParams(search).get("folder") ?? "" };
  }
  if (parts.length === 1 && parts[0] === "people") return { page: "people" };
  if (parts.length === 1 && parts[0] === "audit") return { page: "audit" };
  if (parts.length === 1 && parts[0] === "password") return { page: "password" };
  return { page: "not-found" };
}

export function href(route: Route): string {
  switch (route.page) {
    case "cases":
      return "/";
    case "case": {
      const base = `/cases/${route.id}${route.tab === "files" ? "" : `/${route.tab}`}`;
      return route.tab === "files" && route.folder ? `${base}?folder=${encodeURIComponent(route.folder)}` : base;
    }
    case "people":
      return "/people";
    case "audit":
      return "/audit";
    case "password":
      return "/password";
    default:
      return "/";
  }
}

const listeners = new Set<() => void>();

export function navigate(to: string, replace = false) {
  if (to === location.pathname + location.search) return;
  if (replace) history.replaceState(null, "", to);
  else history.pushState(null, "", to);
  listeners.forEach((l) => l());
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(location.pathname, location.search));
  useEffect(() => {
    const update = () => setRoute(parse(location.pathname, location.search));
    listeners.add(update);
    window.addEventListener("popstate", update);
    return () => {
      listeners.delete(update);
      window.removeEventListener("popstate", update);
    };
  }, []);
  return route;
}

/** Plain left clicks on app links navigate in place; anything else (new tab, etc.) is left to the browser. */
export function onLinkClick(e: React.MouseEvent<HTMLAnchorElement>) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  navigate(e.currentTarget.getAttribute("href") ?? "/");
}
