// A small router: every page has a stable address that can be bookmarked
// or shared (a case, a case tab, a folder in a case).
import { useEffect, useState } from "react";

export type Route =
  | { page: "cases" }
  | { page: "case"; id: string; tab: CaseTab; folder: string; q?: string; file?: string }
  | { page: "document"; id: string; fileId: string; page_no: number; q?: string }
  | { page: "jobs" }
  | { page: "settings" }
  | { page: "people" }
  | { page: "audit" }
  | { page: "password" }
  | { page: "not-found" };

export type CaseTab = "files" | "search" | "ask" | "review" | "email" | "details" | "people" | "activity";
export const TABS: CaseTab[] = ["files", "search", "ask", "review", "email", "details", "people", "activity"];

export function parse(pathname: string, search: string): Route {
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length === 0 || (parts.length === 1 && parts[0] === "cases")) return { page: "cases" };
  if (parts[0] === "cases" && parts.length === 4 && parts[2] === "files" && /^[0-9a-f]{32}$/.test(parts[1]) && /^[0-9a-f]{32}$/.test(parts[3])) {
    const params = new URLSearchParams(search);
    const n = Number(params.get("page") ?? "1");
    const route: Route = { page: "document", id: parts[1], fileId: parts[3], page_no: Number.isInteger(n) && n > 0 ? n : 1 };
    const q = params.get("q");
    return q ? { ...route, q } : route;
  }
  if (parts[0] === "cases" && parts.length <= 3 && /^[0-9a-f]{32}$/.test(parts[1])) {
    const tab = (parts[2] ?? "files") as CaseTab;
    if (!TABS.includes(tab)) return { page: "not-found" };
    const params = new URLSearchParams(search);
    if (tab === "search") return { page: "case", id: parts[1], tab, folder: "", q: params.get("q") ?? "" };
    const file = params.get("file");
    if (tab === "review" && file && /^[0-9a-f]{32}$/.test(file)) return { page: "case", id: parts[1], tab, folder: "", file };
    return { page: "case", id: parts[1], tab, folder: params.get("folder") ?? "" };
  }
  if (parts.length === 1 && parts[0] === "people") return { page: "people" };
  if (parts.length === 1 && parts[0] === "audit") return { page: "audit" };
  if (parts.length === 1 && parts[0] === "jobs") return { page: "jobs" };
  if (parts.length === 1 && parts[0] === "settings") return { page: "settings" };
  if (parts.length === 1 && parts[0] === "password") return { page: "password" };
  return { page: "not-found" };
}

export function href(route: Route): string {
  switch (route.page) {
    case "cases":
      return "/";
    case "case": {
      const base = `/cases/${route.id}${route.tab === "files" ? "" : `/${route.tab}`}`;
      if (route.tab === "search") return route.q ? `${base}?q=${encodeURIComponent(route.q)}` : base;
      if (route.tab === "review" && route.file) return `${base}?file=${route.file}`;
      return route.tab === "files" && route.folder ? `${base}?folder=${encodeURIComponent(route.folder)}` : base;
    }
    case "document": {
      const params = new URLSearchParams();
      if (route.page_no > 1) params.set("page", String(route.page_no));
      if (route.q) params.set("q", route.q);
      const query = params.toString();
      return `/cases/${route.id}/files/${route.fileId}${query ? `?${query}` : ""}`;
    }
    case "jobs":
      return "/jobs";
    case "settings":
      return "/settings";
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
