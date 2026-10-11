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
      return <Badge tone="warn" icon="warn" title="A scan with no text. Run OCR to read it.">Scanned: needs OCR</Badge>;
    case "converted":
      if (file.scanned_pages.length) return <Badge tone="warn" icon="warn" title={file.warnings.join(" ")}>Some pages need OCR</Badge>;
      if (file.warnings.length) return <Badge tone="warn" icon="warn" title={file.warnings.join(" ")}>Converted, {plural(file.warnings.length, "warning")}</Badge>;
      return <Badge tone="ok" icon="ok">Converted</Badge>;
    default:
      return <Badge tone="idle" icon="pending">{String(file.status)}</Badge>;
  }
}

/** Shown beside the status when the text is almost the same as an earlier file. */
export function NearDuplicateBadge({ file }: { file: CaseFile }) {
  if (!file.near_duplicate) return null;
  const pct = Math.round(file.near_duplicate.similarity * 100);
  return <Badge tone="dup" icon="dup" title={`About ${pct}% the same text as ${file.near_duplicate.name}`}>Near-duplicate</Badge>;
}
