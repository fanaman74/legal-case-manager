// Typed client for the web app's API (services/api/app/routes).

export type Role = "admin" | "editor" | "readonly";

export type User = {
  id: number;
  username: string;
  display_name: string;
  role: Role;
  must_change_password: boolean;
  managed_by_control_center: boolean;
};

export type Person = User & {
  active: boolean;
  locked: boolean;
  last_login_at: string | null;
  created_at: string;
  case_ids: string[];
};

export type Permissions = { edit: boolean; upload: boolean; manage: boolean };

export type Case = {
  id: string;
  name: string;
  reference: string;
  client: string;
  description: string;
  notes: string;
  status: "active" | "archived";
  created_at: string;
  created_by?: string;
  file_count?: number;
  member_count?: number;
  permissions?: Permissions;
};

export type FileType = "word" | "pdf" | "email" | "text" | "image";

export type CaseFile = {
  id: string;
  name: string;
  folder: string;
  type: FileType;
  ext: string;
  size: number;
  sha256: string;
  status: string;
  tags: string[];
  added_at: string;
  added_by: string | null;
};

export type Member = { id: number; display_name: string; username: string; role: Role; active: boolean };

export type AuditEntry = {
  id: number;
  at: string;
  username: string;
  action: string;
  outcome: "ok" | "denied" | "refused";
  case_id?: string | null;
  case_name?: string | null;
  target: string;
  detail: string;
  ip?: string;
};

export type Session = { user: User; csrf: string };

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public body?: unknown) {
    super(message);
  }
}

let csrf = "";
let onSignedOut: (() => void) | null = null;

/** Called when the server says the session has ended. */
export function setSignedOutHandler(fn: () => void) {
  onSignedOut = fn;
}

function messageFor(status: number): string {
  if (status === 0) return "The app isn't answering. Check that this computer is on the office network, then try again. The Admin can check the services in the Control Center.";
  if (status >= 500) return "Something went wrong on the server. Try again; if it keeps happening, the Admin can check the web app's log in the Control Center.";
  return "That didn't work. Try again.";
}

async function request<T>(method: string, path: string, body?: unknown, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  if (body !== undefined) headers["Content-Type"] = body instanceof Blob ? "application/octet-stream" : "application/json";
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : body instanceof Blob ? body : JSON.stringify(body),
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, messageFor(0));
  }
  const text = await res.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = undefined;
    }
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | undefined)?.detail;
    let message = messageFor(res.status);
    let code: string | undefined;
    if (typeof detail === "string") message = detail;
    else if (detail && typeof detail === "object" && "message" in detail) {
      message = String((detail as { message: string }).message);
      code = (detail as { code?: string }).code;
    } else if (Array.isArray(detail)) message = "Some of the details aren't valid. Check them and try again.";
    if (res.status === 401 && path !== "/api/session") onSignedOut?.();
    throw new ApiError(res.status, message, code, data);
  }
  if (data && typeof data === "object" && "csrf" in data) csrf = (data as Session).csrf;
  return data as T;
}

const q = (params: Record<string, string | number | boolean | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const out = s.toString();
  return out ? `?${out}` : "";
};

export type UploadResult =
  | { result: "stored"; file: CaseFile }
  | { result: "duplicate"; detail: string; existing: CaseFile }
  | { result: "unsupported"; detail: string };

export const api = {
  setup: () => request<{ admin_ready: boolean }>("GET", "/api/setup"),
  session: () => request<Session>("GET", "/api/session"),
  signIn: (username: string, password: string) => request<Session>("POST", "/api/session", { username, password }),
  signOut: () => request<void>("DELETE", "/api/session"),
  changePassword: (current_password: string, new_password: string) => request<Session>("POST", "/api/session/password", { current_password, new_password }),

  cases: (archived = false) => request<{ items: Case[] }>("GET", `/api/cases${q({ archived })}`),
  createCase: (body: { name: string; reference: string; client: string; description: string; member_ids: number[] }) => request<Case>("POST", "/api/cases", body),
  case: (id: string) => request<Case>("GET", `/api/cases/${id}`),
  editCase: (id: string, body: Partial<Pick<Case, "name" | "reference" | "client" | "description" | "notes">>) => request<Case>("PATCH", `/api/cases/${id}`, body),
  archiveCase: (id: string) => request<Case>("POST", `/api/cases/${id}/archive`),
  restoreCase: (id: string) => request<Case>("POST", `/api/cases/${id}/restore`),
  deleteCase: (id: string, confirm_name: string) => request<void>("POST", `/api/cases/${id}/delete`, { confirm_name }),
  members: (id: string) => request<{ items: Member[] }>("GET", `/api/cases/${id}/members`),
  setMembers: (id: string, user_ids: number[]) => request<{ items: Member[] }>("PUT", `/api/cases/${id}/members`, { user_ids }),
  activity: (id: string, before?: number) => request<{ items: AuditEntry[] }>("GET", `/api/cases/${id}/activity${q({ before, limit: 100 })}`),

  files: (id: string, params: { q?: string; folder?: string; type?: string; sort?: string; order?: string; limit?: number; offset?: number }) =>
    request<{ items: CaseFile[]; total: number }>("GET", `/api/cases/${id}/files${q(params)}`),
  file: (id: string, fileId: string) => request<CaseFile>("GET", `/api/cases/${id}/files/${fileId}`),
  folders: (id: string) => request<{ items: { path: string; files: number }[] }>("GET", `/api/cases/${id}/folders`),
  editFile: (id: string, fileId: string, tags: string[]) => request<CaseFile>("PATCH", `/api/cases/${id}/files/${fileId}`, { tags }),
  downloadUrl: (id: string, fileId: string) => `/api/cases/${id}/files/${fileId}/original`,
  /** Uploads one file. Duplicates and unsupported types come back as results, not errors. */
  upload: async (id: string, path: string, file: File, signal?: AbortSignal): Promise<UploadResult> => {
    try {
      return await request<UploadResult>("PUT", `/api/cases/${id}/files${q({ path })}`, file, { signal });
    } catch (e) {
      if (e instanceof ApiError && (e.status === 409 || e.status === 415) && e.body && typeof e.body === "object" && "result" in e.body) {
        return e.body as UploadResult;
      }
      throw e;
    }
  },

  people: () => request<{ items: Person[] }>("GET", "/api/users"),
  addPerson: (body: { username: string; display_name: string; role: Role; case_ids: string[] }) =>
    request<{ user: Person; temporary_password: string }>("POST", "/api/users", body),
  editPerson: (id: number, body: { display_name?: string; role?: Role; active?: boolean }) => request<Person>("PATCH", `/api/users/${id}`, body),
  resetPassword: (id: number) => request<{ user: Person; temporary_password: string }>("POST", `/api/users/${id}/reset-password`),
  unlock: (id: number) => request<Person>("POST", `/api/users/${id}/unlock`),
  setPersonCases: (id: number, case_ids: string[]) => request<Person>("PUT", `/api/users/${id}/cases`, { case_ids }),

  audit: (params: { username?: string; action?: string; case_id?: string; since?: string; until?: string; before?: number }) =>
    request<{ items: AuditEntry[]; tamper_check: { entries: number; intact: boolean; first_bad_entry: number | null } }>("GET", `/api/audit${q({ ...params, limit: 200 })}`),
};
