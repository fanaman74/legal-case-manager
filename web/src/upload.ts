// Collects files from a drop or a picker, with their path inside the dropped
// folder, and uploads them one at a time per slot so each file succeeds or
// fails on its own.
import { api, ApiError, type CaseFile } from "./api";
import { ACCEPTED, extensionOf } from "./format";

export type PickedFile = { file: File; path: string };

export type ItemState =
  | { state: "waiting" }
  | { state: "uploading" }
  | { state: "stored"; file: CaseFile }
  | { state: "duplicate"; existing: CaseFile }
  | { state: "unsupported"; message: string }
  | { state: "failed"; message: string };

export type UploadItem = PickedFile & { key: number } & ItemState;

/** Files the operating system adds to folders; never part of a case. */
const SYSTEM_FILES = new Set(["thumbs.db", "desktop.ini", ".ds_store"]);

export function isSystemFile(name: string): boolean {
  return SYSTEM_FILES.has(name.toLowerCase()) || name.startsWith("~$");
}

export function fromInput(list: FileList): PickedFile[] {
  return Array.from(list)
    .filter((f) => !isSystemFile(f.name))
    .map((file) => ({ file, path: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name }));
}

/** Reads a drop, walking into dropped folders. */
export async function fromDrop(dt: DataTransfer): Promise<PickedFile[]> {
  const entries = Array.from(dt.items)
    .map((i) => (i.kind === "file" ? i.webkitGetAsEntry?.() : null))
    .filter((e): e is FileSystemEntry => !!e);
  if (entries.length === 0) return fromInput(dt.files);
  const out: PickedFile[] = [];
  const walk = async (entry: FileSystemEntry, prefix: string): Promise<void> => {
    if (entry.isFile) {
      const file = await new Promise<File>((res, rej) => (entry as FileSystemFileEntry).file(res, rej));
      if (!isSystemFile(file.name)) out.push({ file, path: prefix + file.name });
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      // readEntries returns results in batches; keep reading until empty.
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((res, rej) => reader.readEntries(res, rej));
        if (batch.length === 0) break;
        for (const child of batch) await walk(child, `${prefix}${entry.name}/`);
      }
    }
  };
  for (const e of entries) await walk(e, "");
  return out;
}

export function unsupportedMessage(name: string): string | null {
  const ext = extensionOf(name);
  if (ACCEPTED.includes(ext)) return null;
  return `${ext || "Files without an extension"} isn't supported. Supported: ${ACCEPTED.join(", ")}.`;
}

export async function uploadOne(caseId: string, item: PickedFile, signal: AbortSignal): Promise<ItemState> {
  try {
    const r = await api.upload(caseId, item.path, item.file, signal);
    if (r.result === "stored") return { state: "stored", file: r.file };
    if (r.result === "duplicate") return { state: "duplicate", existing: r.existing };
    return { state: "unsupported", message: r.detail };
  } catch (e) {
    if ((e as Error).name === "AbortError") return { state: "failed", message: "Upload cancelled." };
    return { state: "failed", message: e instanceof ApiError ? e.message : "The upload stopped. Check the network connection and try again." };
  }
}
