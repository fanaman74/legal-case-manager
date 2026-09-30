// Types mirror the launcher's JSON (launcher/internal/server).

export type ServiceState = "running" | "starting" | "stopped" | "error";
export type CheckLevel = "pass" | "warn" | "fail" | "pending";
export type StepState = "todo" | "in_progress" | "done" | "failed" | "unavailable";
export type ActionName =
  | "service.start"
  | "service.stop"
  | "service.restart"
  | "stack.start_all"
  | "stack.stop_all"
  | "diagnostics.bundle"
  | "cert.renew"
  | "launcher.set_lan_binding"
  | "component.install"
  | "components.install_missing";

export type ComponentState = "installed" | "missing" | "outdated" | "installing" | "failed";

export interface Component {
  id: string;
  name: string;
  purpose: string;
  state: ComponentState;
  version?: string;
  detail: string;
  canInstall: boolean;
}

export interface Problem {
  what: string;
  why: string;
  next: string;
  fixAction?: ActionName;
  fixLabel?: string;
}

export interface Service {
  id: string;
  name: string;
  purpose: string;
  state: ServiceState;
  detail: string;
  startedAt?: string;
  version: string;
  usage?: { cpuPercent: number; memoryBytes: number };
  lastCheck?: string;
  restartCount: number;
  autoRestartAt?: string;
  problem?: Problem;
}

export interface Check {
  id: string;
  label: string;
  level: CheckLevel;
  detail: string;
  next?: string;
}

export interface Operation {
  id: string;
  action: ActionName;
  service?: string;
  component?: string;
  actor: string;
  state: "running" | "succeeded" | "failed";
  message: string;
  startedAt: string;
  finishedAt?: string;
}

export interface WizardStep {
  n: number;
  id: string;
  title: string;
  state: StepState;
  detail: string;
}

export interface Snapshot {
  generatedAt: string;
  launcherVersion: string;
  runtime: { ok: boolean; name: string; problem?: Problem };
  services: Service[];
  components: Component[];
  checks: Check[];
  lan: { controlCenterOnLan: boolean; appUrls: string[]; controlCenterUrls: string[] };
  operations: Operation[];
  wizard: WizardStep[];
  hasAdmin: boolean;
}

export type SessionState = "needs_setup" | "signed_out" | "setup" | "admin";

export interface Session {
  state: SessionState;
  user?: string;
  csrf?: string;
}

export interface LogLine {
  time: string;
  stream: string;
  level: "error" | "warning" | "info" | "debug";
  text: string;
}

export interface AuditEntry {
  time: string;
  actor: string;
  ip: string;
  action: string;
  target?: string;
  outcome: string;
  detail?: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

let csrf = "";

export function setCsrf(token: string | undefined) {
  csrf = token ?? "";
}

const offline = "The Control Center can't reach the launcher. Check that the launcher service is running, then reload this page.";

async function request(method: string, path: string, body?: unknown): Promise<Response> {
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(offline, 0);
  }
  if (!res.ok) {
    let msg = "Something went wrong. Reload the page and try again.";
    try {
      const data = (await res.json()) as { error?: string };
      if (data.error) msg = data.error;
    } catch {
      /* not JSON */
    }
    throw new ApiError(msg, res.status);
  }
  return res;
}

async function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await request(method, path, body);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  session: () => json<Session>("GET", "/api/session"),
  redeemSetupCode: (code: string) => json<Session>("POST", "/api/session/setup-code", { code }),
  createAdmin: (username: string, password: string) => json<Session>("POST", "/api/session/admin", { username, password }),
  login: (username: string, password: string) => json<Session>("POST", "/api/session/login", { username, password }),
  logout: () => json<void>("POST", "/api/session/logout", {}),
  status: () => json<Snapshot>("GET", "/api/status"),
  action: (action: ActionName, extra: { service?: string; component?: string; enabled?: boolean } = {}) =>
    json<Operation | { message: string }>("POST", "/api/actions", { action, ...extra }),
  logs: (service: string, tail = 1000) =>
    json<{ service: string; lines: LogLine[] }>("GET", `/api/logs?service=${encodeURIComponent(service)}&tail=${tail}`),
  audit: () => json<{ entries: AuditEntry[] }>("GET", "/api/audit"),
  async diagnostics(): Promise<void> {
    const res = await request("POST", "/api/actions", { action: "diagnostics.bundle" });
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") ?? "";
    const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? "diagnostics.zip";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
