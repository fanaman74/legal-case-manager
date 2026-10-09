import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type LogLine, type Snapshot } from "../api";
import { Button, Section } from "../ui";

type Level = "all" | "warning" | "error";
const ROW = 22;

export function Logs({ snap }: { snap: Snapshot }) {
  const [service, setService] = useState(snap.services[0]?.id ?? "api");
  const [level, setLevel] = useState<Level>("all");
  const [query, setQuery] = useState("");
  const [follow, setFollow] = useState(true);
  const [lines, setLines] = useState<LogLine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [diagBusy, setDiagBusy] = useState(false);
  const [diagError, setDiagError] = useState<string | null>(null);
  const parent = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.logs(service, 2000);
      setLines(res.lines);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [service]);

  useEffect(() => {
    setLines(null);
    void load();
  }, [load]);

  useEffect(() => {
    if (!follow) return;
    const t = setInterval(() => void load(), 3000);
    return () => clearInterval(t);
  }, [follow, load]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (lines ?? []).filter((l) => {
      if (level === "error" && l.level !== "error") return false;
      if (level === "warning" && l.level !== "error" && l.level !== "warning") return false;
      return !q || l.text.toLowerCase().includes(q);
    });
  }, [lines, level, query]);

  const v = useVirtualizer({ count: shown.length, getScrollElement: () => parent.current, estimateSize: () => ROW, overscan: 20 });

  useEffect(() => {
    if (follow && shown.length) v.scrollToIndex(shown.length - 1, { align: "end" });
  }, [follow, shown.length, v]);

  const diagnostics = async () => {
    setDiagBusy(true);
    setDiagError(null);
    try {
      await api.diagnostics();
    } catch (e) {
      setDiagError((e as Error).message);
    } finally {
      setDiagBusy(false);
    }
  };

  const svcName = snap.services.find((s) => s.id === service)?.name ?? service;

  return (
    <Section
      id="logs"
      title="Logs"
      meta="Secrets and case file names are removed before logs are shown"
      actions={<Button icon="download" onClick={() => void diagnostics()} busy={diagBusy}>Download diagnostics</Button>}
    >
      {diagError && <p className="field__error field__error--block" role="alert">{diagError}</p>}
      <div className="log-toolbar">
        <div className="field field--inline">
          <label htmlFor="log-service">Service</label>
          <select id="log-service" className="input input--sm" value={service} onChange={(e) => setService(e.target.value)}>
            {snap.services.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        </div>
        <div className="field field--inline">
          <label htmlFor="log-level">Show</label>
          <select id="log-level" className="input input--sm" value={level} onChange={(e) => setLevel(e.target.value as Level)}>
            <option value="all">Everything</option>
            <option value="warning">Warnings and errors</option>
            <option value="error">Errors only</option>
          </select>
        </div>
        <div className="field field--inline field--grow">
          <label htmlFor="log-query">Filter</label>
          <input id="log-query" className="input input--sm" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Text to find" />
        </div>
        <label className="check-inline">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          <span>Follow new lines</span>
        </label>
      </div>

      <div className="log" ref={parent} role="log" aria-label={`${svcName} log`} tabIndex={0}>
        {error ? (
          <p className="log__empty">{error}</p>
        ) : lines === null ? (
          <p className="log__empty">Loading the log…</p>
        ) : shown.length === 0 ? (
          <p className="log__empty">
            {lines.length === 0 ? `${svcName} hasn't written anything yet. Lines appear here once it starts.` : "No lines match this filter."}
          </p>
        ) : (
          <div style={{ height: v.getTotalSize(), position: "relative" }}>
            {v.getVirtualItems().map((it) => {
              const l = shown[it.index];
              return (
                <div key={it.key} className={`log__line log__line--${l.level}`} style={{ transform: `translateY(${it.start}px)`, height: ROW }}>
                  <span className="log__time">{l.time.replace("T", " ").slice(0, 19)}</span>
                  <span className="log__level">{l.level === "info" ? "" : l.level}</span>
                  <span className="log__text">{l.text}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
      <p className="hint">{lines ? `${shown.length} of ${lines.length} lines` : ""}</p>
    </Section>
  );
}
