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

export type FileStatus = "queued" | "converting" | "converted" | "needs_ocr" | "failed";

export type CaseFile = {
  id: string;
  name: string;
  folder: string;
  type: FileType;
  ext: string;
  size: number;
  sha256: string;
  status: FileStatus;
  tags: string[];
  added_at: string;
  added_by: string | null;
  parent_id: string | null;
  page_count: number | null;
  ocr: boolean;
  warnings: string[];
  error: string;
  scanned_pages: number[];
  doc_date: string | null;
  title: string | null;
  converted_at: string | null;
  previews: number;
  search: { state: "" | "indexed" | "keyword_only" | "failed"; passages: number };
  email: { from: string; to: string[]; cc: string[]; date: string | null; subject: string } | null;
  attachments: { name: string; doc_id: string; duplicate?: boolean }[];
};

export type JobCounts = { queued: number; running: number; failed: number; done: number; total: number };

export type Progress = {
  files: Partial<Record<FileStatus, number>>;
  total: number;
  warnings: number;
  scanned: number;
  jobs: Record<string, JobCounts>;
  index: Partial<Record<"waiting" | "indexed" | "keyword_only" | "failed", number>>;
};

export type SearchMode = "hybrid" | "keyword" | "semantic";
export type SearchFilters = { type?: string; folder?: string; date_from?: string; date_to?: string; tag?: string };
export type SearchHit = { passage: number; page: number | null; heading: string; snippet: string; found_by: ("words" | "meaning")[] };
export type SearchResult = {
  file: { id: string; name: string; folder: string; type: CaseFile["type"]; title: string | null; doc_date: string | null; page_count: number | null };
  hits: SearchHit[];
  more: number;
};
export type SearchResponse = {
  results: SearchResult[];
  documents: number;
  terms: string[];
  meaning: "on" | "off" | "unavailable";
  mode: SearchMode;
  index: { indexed: number; keyword_only: number; waiting: number; failed: number };
};

export type Mailbox = {
  id: string;
  name: string;
  size: number;
  sha256: string;
  status: "queued" | "indexing" | "ready" | "failed";
  error: string;
  emails: number;
  done: number;
  total: number;
  added_at: string;
  added_by: string | null;
  added_to_case: number;
  folders?: { path: string; messages: number }[];
};
export type MailFilters = { q?: string; sender?: string; to?: string; folder?: string; date_from?: string; date_to?: string; attachments?: boolean; sort?: "relevance" | "newest" | "oldest" };
export type MailRow = { id: number; folder: string; kind: string; subject: string; sender: string; to: string; date: string | null; attachments: number; snippet: string; in_case: string | null };
export type Mail = {
  id: number;
  folder: string;
  kind: string;
  subject: string;
  sender: string;
  to: string[];
  cc: string[];
  date: string | null;
  message_id: string;
  body: string;
  attachments: { name: string; size: number; embedded: boolean }[];
  in_case: string | null;
};

export type Job = {
  id: number;
  kind: string;
  label: string;
  state: "queued" | "running" | "done" | "failed";
  case_id: string | null;
  case_name: string | null;
  file_id: string | null;
  file: string | null;
  error: string;
  attempts: number;
  done: number;
  total: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
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

// --- AI services (Ask, Review, Settings) ----------------------------------------

export type AiProviderChoice = { id: string; name: string; destination: string; model: string; external: boolean };

export type AiProvider = AiProviderChoice & {
  label: string;
  about: string;
  enabled: boolean;
  base_url: string;
  url_editable: boolean;
  default_model: string;
  needs_key: boolean;
  has_key: boolean;
  key_hint: string;
  ready: boolean;
  tested_at: string | null;
  test_ok: boolean | null;
};

export type AiSettings = {
  providers: AiProvider[];
  defaults: { ask: string; review: string };
  local: {
    running: boolean;
    installed: string[];
    available: { model: string; about: string; installed: boolean; download: { state: string; done: number; total: number; error: string } | null }[];
  };
  embedding: { model: string; files: number };
};

export type AiKind = "ask" | "summary" | "chronology" | "relevance" | "extract" | "tags";
export type AiScope = { type: "case" } | { type: "folder"; folder: string } | { type: "file"; file_id: string };

export type AiSource = { n: number; passage: number; file_id: string; name: string; path: string; page: number | null; heading: string; text: string };

export type AiCall = {
  id: string;
  kind: AiKind;
  label: string;
  state: "prepared" | "sending" | "done" | "failed" | "cancelled";
  question: string;
  scope: AiScope;
  provider: AiProviderChoice;
  served_model: string | null;
  sources: AiSource[];
  documents: number;
  note: string;
  answer: string;
  cited: number[];
  cut_short: boolean;
  suggestions: { file_id: string; name: string; document_type: string; tags: string[] }[] | null;
  accepted: Record<string, string[]>;
  error: string;
  username: string;
  created_at: string;
  finished_at: string | null;
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

  files: (id: string, params: { q?: string; folder?: string; type?: string; status?: string; sort?: string; order?: string; limit?: number; offset?: number }) =>
    request<{ items: CaseFile[]; total: number }>("GET", `/api/cases/${id}/files${q(params)}`),
  file: (id: string, fileId: string) => request<CaseFile>("GET", `/api/cases/${id}/files/${fileId}`),
  search: (id: string, q: string, mode: SearchMode, filters: SearchFilters, signal?: AbortSignal) => {
    const params = new URLSearchParams({ q, mode });
    for (const [k, v] of Object.entries(filters)) if (v) params.set(k, v);
    return request<SearchResponse>("GET", `/api/cases/${id}/search?${params}`, undefined, { signal });
  },
  reindex: (id: string) => request<{ queued: number }>("POST", `/api/cases/${id}/reindex`),
  folders: (id: string) => request<{ items: { path: string; files: number }[] }>("GET", `/api/cases/${id}/folders`),
  editFile: (id: string, fileId: string, tags: string[]) => request<CaseFile>("PATCH", `/api/cases/${id}/files/${fileId}`, { tags }),
  downloadUrl: (id: string, fileId: string) => `/api/cases/${id}/files/${fileId}/original`,
  pageUrl: (id: string, fileId: string, page: number) => `/api/cases/${id}/files/${fileId}/pages/${page}`,
  markdown: (id: string, fileId: string) => request<{ front_matter: string; body: string; markdown_sha256: string }>("GET", `/api/cases/${id}/files/${fileId}/markdown`),
  reconvert: (id: string, fileId: string, ocr = false) => request<CaseFile>("POST", `/api/cases/${id}/files/${fileId}/convert`, { ocr }),
  bulkConvert: (id: string, what: "ocr_scanned" | "retry_failed") => request<{ queued: number }>("POST", `/api/cases/${id}/convert`, { what }),
  progress: (id: string) => request<Progress>("GET", `/api/cases/${id}/progress`),
  status: () => request<{ jobs: Record<string, JobCounts>; files: Partial<Record<FileStatus, number>> }>("GET", "/api/status"),
  jobs: (state?: string) => request<{ items: Job[]; counts: Record<string, JobCounts> }>("GET", `/api/jobs${q({ state })}`),
  retryJob: (jobId: number) => request<{ ok: boolean }>("POST", `/api/jobs/${jobId}/retry`),
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

  mailboxes: (id: string) => request<{ items: Mailbox[] }>("GET", `/api/cases/${id}/mailboxes`),
  mailbox: (id: string, mailboxId: string) => request<Mailbox>("GET", `/api/cases/${id}/mailboxes/${mailboxId}`),
  searchMailbox: (id: string, mailboxId: string, f: MailFilters, offset: number, signal?: AbortSignal) => {
    const params = new URLSearchParams({ offset: String(offset), limit: "200" });
    for (const [k, v] of Object.entries(f)) if (v) params.set(k, String(v));
    return request<{ items: MailRow[]; total: number; terms: string[] }>("GET", `/api/cases/${id}/mailboxes/${mailboxId}/search?${params}`, undefined, { signal });
  },
  mail: (id: string, mailboxId: string, messageId: number) => request<Mail>("GET", `/api/cases/${id}/mailboxes/${mailboxId}/messages/${messageId}`),
  addMail: (id: string, mailboxId: string, messages: number[], attachments: boolean) =>
    request<{ queued: number; already_in_case: number }>("POST", `/api/cases/${id}/mailboxes/${mailboxId}/add`, { messages, attachments }),
  deleteMailbox: (id: string, mailboxId: string) => request<void>("POST", `/api/cases/${id}/mailboxes/${mailboxId}/delete`),
  /** Uploads a mailbox with progress (they can be many gigabytes). */
  uploadMailbox: (id: string, file: File, onProgress: (sent: number) => void, signal?: AbortSignal) =>
    new Promise<Mailbox>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", `/api/cases/${id}/mailboxes${q({ name: file.name })}`);
      xhr.setRequestHeader("Accept", "application/json");
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
      xhr.upload.onprogress = (e) => onProgress(e.loaded);
      xhr.onload = () => {
        let body: { mailbox?: Mailbox; detail?: string } = {};
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          // not JSON
        }
        if (xhr.status === 201 && body.mailbox) resolve(body.mailbox);
        else reject(new ApiError(xhr.status, body.detail ?? messageFor(xhr.status)));
      };
      xhr.onerror = () => reject(new ApiError(0, messageFor(0)));
      xhr.onabort = () => reject(new DOMException("Cancelled", "AbortError"));
      signal?.addEventListener("abort", () => xhr.abort());
      xhr.send(file);
    }),

  aiChoices: () => request<{ providers: AiProviderChoice[]; defaults: { ask: string; review: string } }>("GET", "/api/ai/providers"),
  aiPrepare: (id: string, body: { kind: AiKind; question?: string; scope?: AiScope; provider?: string }) => request<AiCall>("POST", `/api/cases/${id}/ai`, body),
  aiSend: (id: string, callId: string, confirmed: boolean) => request<AiCall>("POST", `/api/cases/${id}/ai/${callId}/send`, { confirmed }),
  aiCancel: (id: string, callId: string) => request<{ ok: boolean }>("POST", `/api/cases/${id}/ai/${callId}/cancel`),
  aiHistory: (id: string, kind: "ask" | "review") => request<{ items: AiCall[] }>("GET", `/api/cases/${id}/ai${q({ kind })}`),
  aiAccept: (id: string, callId: string, file_id: string, tag: string) => request<AiCall>("POST", `/api/cases/${id}/ai/${callId}/accept`, { file_id, tag }),
  aiSettings: () => request<AiSettings>("GET", "/api/settings/ai"),
  editAiProvider: (providerId: string, body: { enabled?: boolean; base_url?: string; model?: string; api_key?: string; remove_key?: boolean }) =>
    request<AiProvider>("PUT", `/api/settings/ai/providers/${providerId}`, body),
  testAiProvider: (providerId: string) => request<{ ok: boolean; message: string; models: string[]; provider: AiProvider }>("POST", `/api/settings/ai/providers/${providerId}/test`),
  setAiDefaults: (body: { ask: string; review: string }) => request<{ ask: string; review: string }>("PUT", "/api/settings/ai/defaults", body),
  downloadLocalModel: (model: string) => request<{ queued: boolean }>("POST", "/api/settings/ai/local-models", { model }),
  reindexAll: () => request<{ queued: number }>("POST", "/api/settings/ai/reindex"),

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
