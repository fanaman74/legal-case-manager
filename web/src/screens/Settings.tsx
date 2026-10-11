import { useEffect, useState, type FormEvent } from "react";
import { api, type AiProvider, type AiSettings } from "../api";
import { ProviderBadge } from "../components/Ai";
import { formatDateTime, plural } from "../format";
import { useApp, useLoad, useTitle } from "../hooks";
import { Badge, Button, ErrorPanel, Field, FormError, Icon, Modal, Skeleton } from "../ui";

/** Admin settings: the AI services Ask and Review may use, local models,
 * and the search model. */
export function Settings() {
  useTitle("Settings");
  const { toast } = useApp();
  const settings = useLoad(() => api.aiSettings(), []);
  const [reindexing, setReindexing] = useState(false);

  // While a local model downloads, refresh its progress.
  const downloading = settings.data?.local.available.some((m) => m.download && m.download.state !== "failed");
  useEffect(() => {
    if (!downloading) return;
    const t = setInterval(() => void settings.reload(), 3000);
    return () => clearInterval(t);
  }, [downloading, settings.reload]);

  if (settings.error && !settings.data) return <div className="page"><ErrorPanel message={settings.error} onRetry={() => void settings.reload()} /></div>;
  const s = settings.data;
  const replace = (p: AiProvider) => settings.setData((d) => d && { ...d, providers: d.providers.map((x) => (x.id === p.id ? p : x)) });

  return (
    <div className="page settings">
      <header className="page__head">
        <div>
          <h1 className="page__title">Settings</h1>
          <p className="page__meta">AI services for Ask and Review. Search always runs on this computer.</p>
        </div>
      </header>
      {!s ? (
        <Skeleton rows={6} />
      ) : (
        <>
          <section className="settings__section" aria-labelledby="providers-h">
            <div className="settings__intro">
              <h2 id="providers-h" className="h3">AI services</h2>
              <p className="muted">
                Services on this computer keep everything here. Online services receive the passages a question or review includes; whoever asks sees exactly what will be sent and confirms it every time. Every request is in the audit log.
              </p>
            </div>
            <ul className="providers">
              {s.providers.map((p) => (
                <li key={p.id}><ProviderCard p={p} local={s.local} onChange={replace} onReload={() => void settings.reload()} /></li>
              ))}
            </ul>
          </section>

          <Defaults s={s} onSaved={(d) => { settings.setData({ ...s, defaults: d }); toast("Defaults saved."); }} />

          <section className="settings__section" aria-labelledby="local-h">
            <div className="settings__intro">
              <h2 id="local-h" className="h3">Models on this computer</h2>
              <p className="muted">
                Download a chat model to answer questions without anything leaving this computer. Answers are slower than online services and less polished; a computer with more memory helps.
              </p>
            </div>
            <LocalModels s={s} onStarted={() => void settings.reload()} />
          </section>

          <section className="settings__section" aria-labelledby="embed-h">
            <div className="settings__intro">
              <h2 id="embed-h" className="h3">Search model</h2>
              <p className="muted">Finds passages by meaning. It always runs on this computer; case text is never sent anywhere to be searched.</p>
            </div>
            <div className="card settings__card">
              <dl className="kv">
                <dt>Model</dt><dd className="mono">{s.embedding.model}</dd>
                <dt>Indexed</dt><dd>{plural(s.embedding.files, "converted file")}</dd>
              </dl>
              <div>
                <Button icon="refresh" onClick={() => setReindexing(true)}>Re-index all cases…</Button>
              </div>
            </div>
          </section>
        </>
      )}
      <ReindexAll open={reindexing} onClose={() => setReindexing(false)} onDone={(n) => toast(`${plural(n, "file")} queued for indexing.`)} />
    </div>
  );
}

function status(p: AiProvider): { tone: "ok" | "warn" | "error"; text: string } {
  if (p.needs_key && !p.has_key) return { tone: "warn", text: "Needs an API key" };
  if (!p.model) return { tone: "warn", text: "Choose a model" };
  if (p.test_ok === false) return { tone: "error", text: "Last test failed" };
  return { tone: "ok", text: p.test_ok ? "Ready, tested" : "Ready" };
}

function ProviderCard({ p, local, onChange, onReload }: { p: AiProvider; local: AiSettings["local"]; onChange: (p: AiProvider) => void; onReload: () => void }) {
  const { toast } = useApp();
  const [key, setKey] = useState("");
  const [model, setModel] = useState(p.model);
  const [url, setUrl] = useState(p.base_url);
  const [models, setModels] = useState<string[]>(p.id === "ollama" ? local.installed : []);
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "test" | "toggle" | null>(null);
  useEffect(() => setModel(p.model), [p.model]);
  useEffect(() => setUrl(p.base_url), [p.base_url]);
  useEffect(() => {
    if (p.id === "ollama") setModels(local.installed);
  }, [p.id, local.installed]);

  const dirty = key.trim() !== "" || model.trim() !== p.model || (p.url_editable && url.trim() !== p.base_url);
  const st = status(p);

  const save = async (e?: FormEvent) => {
    e?.preventDefault();
    setBusy("save");
    setError(null);
    setTest(null);
    try {
      const next = await api.editAiProvider(p.id, {
        ...(key.trim() ? { api_key: key.trim() } : {}),
        model: model.trim(),
        ...(p.url_editable ? { base_url: url.trim() } : {}),
      });
      setKey("");
      onChange(next);
      toast(`${p.label} saved.`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const runTest = async () => {
    setBusy("test");
    setError(null);
    try {
      const r = await api.testAiProvider(p.id);
      setTest(r);
      if (r.models.length) setModels(r.models);
      onChange(r.provider);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const toggle = async () => {
    setBusy("toggle");
    setError(null);
    try {
      onChange(await api.editAiProvider(p.id, { enabled: !p.enabled }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const removeKey = async () => {
    setBusy("save");
    try {
      onChange(await api.editAiProvider(p.id, { remove_key: true }));
      toast(`${p.label} API key removed.`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <form className={`card provider${p.enabled ? "" : " provider--off"}`} onSubmit={save} aria-labelledby={`p-${p.id}`}>
      <div className="provider__head">
        <h3 id={`p-${p.id}`} className="provider__name">{p.label}</h3>
        <ProviderBadge p={p} />
        {p.enabled && <Badge tone={st.tone} icon={st.tone === "ok" ? "ok" : st.tone === "error" ? "error" : "warn"}>{st.text}</Badge>}
        <label className="switch">
          <input type="checkbox" role="switch" checked={p.enabled} onChange={() => void toggle()} disabled={busy !== null} />
          <span>{p.enabled ? "On" : "Off"}</span>
        </label>
      </div>
      <p className="provider__about muted">{p.about}</p>
      {p.enabled && (
        <>
          <FormError message={error} />
          <div className="provider__fields">
            {p.needs_key && (
              <Field id={`k-${p.id}`} label="API key" hint={p.has_key ? <>Saved{p.key_hint ? <>, ends in <span className="mono">…{p.key_hint}</span></> : ""}. Type a new key to replace it.</> : "Stored encrypted on this computer. It is never shown again."}>
                <div className="provider__key">
                  <input id={`k-${p.id}`} className="input" type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)}
                    placeholder={p.has_key ? "Saved" : "Paste the key"} maxLength={500} />
                  {p.has_key && <Button size="sm" variant="quiet" onClick={() => void removeKey()}>Remove</Button>}
                </div>
              </Field>
            )}
            <Field id={`u-${p.id}`} label="Address" hint={p.url_editable ? "LM Studio's local server address. Another computer's address counts as off this machine." : undefined}>
              {p.url_editable ? (
                <input id={`u-${p.id}`} className="input mono" value={url} onChange={(e) => setUrl(e.target.value)} maxLength={500} />
              ) : (
                <input id={`u-${p.id}`} className="input mono" value={p.base_url} readOnly />
              )}
            </Field>
            <Field id={`m-${p.id}`} label="Model" hint={p.id === "ollama" && !local.installed.length ? "No chat model downloaded yet. Download one below." : p.default_model && model !== p.default_model ? `Suggested: ${p.default_model}` : undefined}>
              {p.id === "ollama" ? (
                <select id={`m-${p.id}`} className="input input--select" value={model} onChange={(e) => setModel(e.target.value)}>
                  <option value="">Choose a model</option>
                  {models.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              ) : (
                <>
                  <input id={`m-${p.id}`} className="input mono" value={model} onChange={(e) => setModel(e.target.value)} list={`ml-${p.id}`} maxLength={200} placeholder={p.default_model || "Model name"} />
                  <datalist id={`ml-${p.id}`}>{models.map((m) => <option key={m} value={m} />)}</datalist>
                </>
              )}
            </Field>
          </div>
          <div className="provider__actions">
            <Button type="submit" variant="primary" busy={busy === "save"} disabled={!dirty}>Save</Button>
            <Button icon="refresh" busy={busy === "test"} disabled={dirty || (p.needs_key && !p.has_key)} onClick={() => void runTest()} title={dirty ? "Save first" : undefined}>Test connection</Button>
            {test ? (
              <p className={`provider__test tone--${test.ok ? "ok" : "error"}`} role="status"><Icon name={test.ok ? "ok" : "error"} />{test.message}</p>
            ) : p.tested_at ? (
              <p className="hint">Last tested {formatDateTime(p.tested_at)}</p>
            ) : null}
            {p.id === "ollama" && !local.running && <p className="hint tone--warn"><Icon name="warn" /> The Local AI models service isn't running. Start it from the Control Center. <button type="button" className="linkish" onClick={onReload}>Check again</button></p>}
          </div>
        </>
      )}
    </form>
  );
}

function Defaults({ s, onSaved }: { s: AiSettings; onSaved: (d: AiSettings["defaults"]) => void }) {
  const [form, setForm] = useState(s.defaults);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setForm(s.defaults), [s.defaults]);
  const ready = s.providers.filter((p) => p.ready);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.setAiDefaults(form));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const pick = (use: "ask" | "review") => (
    <select id={`d-${use}`} className="input input--select" value={form[use]} onChange={(e) => setForm({ ...form, [use]: e.target.value })}>
      <option value="">The first one that's ready</option>
      {s.providers.map((p) => <option key={p.id} value={p.id} disabled={!p.ready}>{p.label}{p.ready ? ` (${p.model})` : " (not set up)"}</option>)}
    </select>
  );
  return (
    <section className="settings__section" aria-labelledby="defaults-h">
      <div className="settings__intro">
        <h2 id="defaults-h" className="h3">Defaults</h2>
        <p className="muted">Which service Ask and Review start with. People can choose another ready service for a single request.</p>
      </div>
      <form className="card settings__card" onSubmit={submit}>
        <FormError message={error} />
        {ready.length === 0 && <p className="hint">Set up a service above first.</p>}
        <div className="form-row">
          <Field id="d-ask" label="Ask uses">{pick("ask")}</Field>
          <Field id="d-review" label="Review uses">{pick("review")}</Field>
        </div>
        <div><Button type="submit" variant="primary" busy={busy} disabled={form.ask === s.defaults.ask && form.review === s.defaults.review}>Save defaults</Button></div>
      </form>
    </section>
  );
}

function LocalModels({ s, onStarted }: { s: AiSettings; onStarted: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const start = async (model: string) => {
    setError(null);
    try {
      await api.downloadLocalModel(model);
      onStarted();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <div className="card settings__card">
      <FormError message={error} />
      {!s.local.running && <p className="hint tone--warn"><Icon name="warn" /> The Local AI models service isn't running, so models can't be downloaded or used. Start it from the Control Center.</p>}
      <ul className="localmodels">
        {s.local.available.map((m) => {
          const d = m.download;
          const pct = d && d.total ? Math.round((d.done / d.total) * 100) : 0;
          return (
            <li key={m.model} className="localmodel">
              <div className="localmodel__text">
                <span className="mono">{m.model}</span>
                <span className="muted">{m.about}</span>
                {d?.state === "failed" && <span className="tone--error">{d.error}</span>}
              </div>
              {m.installed ? (
                <Badge tone="ok" icon="ok">Downloaded</Badge>
              ) : d && d.state !== "failed" ? (
                <div className="localmodel__progress" role="status">
                  <progress max={100} value={d.state === "running" ? pct : undefined} aria-label={`Downloading ${m.model}`} />
                  <span className="hint">{d.state === "queued" ? "Waiting to start" : `${pct}%`}</span>
                </div>
              ) : (
                <Button size="sm" icon="download" disabled={!s.local.running} onClick={() => void start(m.model)}>{d?.state === "failed" ? "Try again" : "Download"}</Button>
              )}
            </li>
          );
        })}
      </ul>
      <p className="hint">After downloading, choose the model under Ollama above and save.</p>
    </div>
  );
}

function ReindexAll({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: (n: number) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.reindexAll();
      onDone(r.queued);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="Re-index every case?"
      description="Every converted file in every case is indexed again. Search still works meanwhile, but results are partial until it finishes, which can take hours for large cases."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} onClick={() => void go()}>Re-index all cases</Button></>}
    >
      <FormError message={error} />
    </Modal>
  );
}
