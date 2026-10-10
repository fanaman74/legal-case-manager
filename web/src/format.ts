const dateFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const dateTimeFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dateFmt.format(d);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dateTimeFmt.format(d);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

export const roleLabel = { admin: "Admin", editor: "Editor", readonly: "Read-only" } as const;

export const typeLabel = { word: "Word", pdf: "PDF", email: "Email", text: "Text", image: "Image" } as const;

/** Middle-truncated hash for tables: first and last 8 characters. */
export function shortHash(h: string): string {
  return h.length > 20 ? `${h.slice(0, 8)}…${h.slice(-8)}` : h;
}

export const ACCEPTED = [".docx", ".pdf", ".msg", ".eml", ".txt", ".png", ".jpg", ".jpeg", ".tif", ".tiff"];

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

/** Plain-language description of an audit action. */
export function describeAction(action: string): string {
  const map: Record<string, string> = {
    "admin.sync": "Admin account updated",
    "auth.sign_in": "Signed in",
    "auth.sign_out": "Signed out",
    "auth.password_changed": "Changed password",
    "user.create": "Added person",
    "user.edit": "Changed person",
    "user.reset_password": "Reset password",
    "user.unlock": "Unlocked account",
    "case.create": "Created case",
    "case.view": "Opened case",
    "case.edit": "Edited case",
    "case.archive": "Archived case",
    "case.restore": "Restored case",
    "case.delete": "Deleted case",
    "case.members": "Changed case access",
    "file.upload": "Uploaded file",
    "file.duplicate": "Duplicate not added",
    "file.rejected": "Unsupported file refused",
    "file.tags": "Changed tags",
    "file.download": "Downloaded original",
  };
  return map[action] ?? action;
}
