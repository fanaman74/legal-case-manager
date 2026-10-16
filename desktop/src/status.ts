// The tray icon's status: is the case manager answering, is only the
// launcher answering (services starting or stopped), or neither.

export type Health = "running" | "starting" | "stopped";

export function health(appUp: boolean, launcherUp: boolean): Health {
  if (appUp) return "running";
  return launcherUp ? "starting" : "stopped";
}

export const healthText: Record<Health, string> = {
  running: "Case File Manager is running",
  starting: "Case File Manager is starting or stopped. Open the Control Center to check.",
  stopped: "Case File Manager isn't running. Restart the computer, or run the installer again to repair it.",
};

type Counts = { queued: number; running: number; failed: number };
export type JobStatus = { jobs: Record<string, Counts> };

export type JobSnapshot = { active: number; failed: number };

export function snapshot(s: JobStatus): JobSnapshot {
  let active = 0;
  let failed = 0;
  for (const c of Object.values(s.jobs ?? {})) {
    active += (c.queued ?? 0) + (c.running ?? 0);
    failed += c.failed ?? 0;
  }
  return { active, failed };
}

/** finished returns the notification to show when background work (conversion,
 * OCR, indexing, exports) has just run out, or null. */
export function finished(prev: JobSnapshot | null, next: JobSnapshot): { title: string; body: string } | null {
  if (!prev || prev.active === 0 || next.active > 0) return null;
  const newlyFailed = Math.max(0, next.failed - prev.failed);
  return {
    title: "Background work finished",
    body: newlyFailed > 0
      ? `${newlyFailed} ${newlyFailed === 1 ? "file" : "files"} couldn't be processed. The case's file list shows which.`
      : "Every queued file has been processed.",
  };
}
