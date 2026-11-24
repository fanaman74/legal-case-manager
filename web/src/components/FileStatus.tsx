import type { CaseFile } from "../api";
import { plural } from "../format";
import { Badge } from "../ui";

/** A file's conversion state: colour, icon shape and words, never colour alone. */
export function FileStatusBadge({ file }: { file: CaseFile }) {
  switch (file.status) {
    case "queued":
      return <Badge tone="idle" icon="pending">Waiting</Badge>;
    case "converting":
      return <Badge tone="progress" icon="progress">Converting</Badge>;
    case "failed":
      return <Badge tone="error" icon="error" title={file.error}>Failed</Badge>;
    case "needs_ocr":
    case "converted": {
      const ocr = OcrBadge({ file });
      if (ocr) return ocr;
      if (file.status === "needs_ocr") return <Badge tone="warn" icon="warn" title="A scan with no text.">Scanned, no text</Badge>;
      if (file.warnings.length) return <Badge tone="warn" icon="warn" title={file.warnings.join(" ")}>Converted, {plural(file.warnings.length, "warning")}</Badge>;
      return <Badge tone="ok" icon="ok" title={file.notes.length ? file.notes.join(" ") : undefined}>Converted</Badge>;
    }
    default:
      return <Badge tone="idle" icon="pending">{String(file.status)}</Badge>;
  }
}

/** Scanned pages are read with OCR by themselves; this says how far that has got. */
function OcrBadge({ file }: { file: Pick<CaseFile, "ocr_state" | "ocr_error" | "scanned_pages"> }) {
  switch (file.ocr_state) {
    case "waiting":
      return <Badge tone="progress" icon="pending" title="The rest of the file can already be opened and searched.">Scans waiting for OCR</Badge>;
    case "running":
      return <Badge tone="progress" icon="progress" title="The rest of the file can already be opened and searched.">Reading scans (OCR)</Badge>;
    case "unavailable":
      return <Badge tone="warn" icon="warn" title={OCR_MISSING}>Scans: OCR not installed</Badge>;
    case "failed":
      return <Badge tone="warn" icon="warn" title={file.ocr_error}>OCR couldn't read scans</Badge>;
    default:
      return null;
  }
}

export const OCR_MISSING = "OCR isn't installed on this computer yet, so scanned pages can't be read. The Admin can install it from Components in the Control Center; scanned pages are then read by themselves.";

/** Shown beside the status when the text is almost the same as an earlier file. */
export function NearDuplicateBadge({ file }: { file: CaseFile }) {
  if (!file.near_duplicate) return null;
  const pct = Math.round(file.near_duplicate.similarity * 100);
  return <Badge tone="dup" icon="dup" title={`About ${pct}% the same text as ${file.near_duplicate.name}`}>Near-duplicate</Badge>;
}
