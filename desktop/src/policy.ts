// What the app window may load. Only the case manager and the Control
// Center on this computer open inside the app; any other web link opens in
// the normal browser, and nothing else is fetched at all.

export type Target = "app" | "control-center" | "local" | "external" | "blocked";

function sameOrigin(url: URL, base: string): boolean {
  return url.origin === new URL(base).origin;
}

/** classify says where a URL belongs. */
export function classify(raw: string, appUrl: string, controlCenterUrl: string): Target {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return "blocked";
  }
  if (sameOrigin(url, appUrl)) return "app";
  if (sameOrigin(url, controlCenterUrl)) return "control-center";
  // The app's own pages (offline screen) and what the web app builds in
  // the page (previews, downloads) never leave this computer.
  if (url.protocol === "file:" || url.protocol === "blob:" || url.protocol === "data:" || url.protocol === "devtools:") return "local";
  if (url.protocol === "https:" || url.protocol === "http:" || url.protocol === "mailto:") return "external";
  return "blocked";
}

/** mayRequest says whether the window may fetch a URL at all. Links to other
 * sites are opened in the browser instead, so the window itself never
 * reaches the internet. */
export function mayRequest(raw: string, appUrl: string, controlCenterUrl: string): boolean {
  const t = classify(raw, appUrl, controlCenterUrl);
  return t === "app" || t === "control-center" || t === "local";
}

const HANDOFF = /^https:\/\/localhost(:\d{1,5})?\/#setup=[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}$/;

/** handoffUrl checks the first-run file the installer leaves: the Control
 * Center address with the one-time setup code. Anything else is ignored. */
export function handoffUrl(text: string, controlCenterUrl: string): string | null {
  const u = text.trim();
  if (!HANDOFF.test(u)) return null;
  return classify(u, "https://invalid.invalid/", controlCenterUrl) === "control-center" ? u : null;
}
