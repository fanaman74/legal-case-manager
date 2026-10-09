import { useEffect, useRef, useState, type FormEvent } from 'react';
import {
  ArrowDownToLine, ArrowRight, ArrowUp, BookOpen, Bot, CalendarDays,
  Check, ChevronDown, CircleCheck, Clock3, FileText, FolderClosed, FolderOpen,
  Layers3, LoaderCircle, Mail, MessageSquare, Paperclip, Plus, Search,
  Settings2, ShieldCheck, Sparkles, UsersRound, Wallet, X, Scale, Pencil,
  type LucideIcon,
} from 'lucide-react';
import type { Agent, Case, Citation, Document, Evidence, Message, Settings, Task } from './types';

type View = 'chat' | 'documents' | 'agents' | 'tasks' | 'models' | 'settings';
type Consent = { title: string; documents: Document[]; action: () => Promise<void> };
const navigation: { id: View; title: string; icon: LucideIcon }[] = [
  { id: 'chat', title: 'Case assistant', icon: MessageSquare },
  { id: 'documents', title: 'Documents', icon: FileText },
  { id: 'agents', title: 'Topic agents', icon: UsersRound },
  { id: 'tasks', title: 'Review tasks', icon: Layers3 },
  { id: 'models', title: 'Models', icon: Bot },
];
const agentIcons: Record<string, LucideIcon> = {
  calendar: CalendarDays, wallet: Wallet, mail: Mail, scale: Scale, search: Search,
};
const starters = [
  { icon: CalendarDays, color: 'blue', title: 'Build a timeline', text: 'Find dates, events and deadlines', prompt: 'Find key dates, meetings, events and deadlines in my documents.' },
  { icon: Wallet, color: 'amber', title: 'Follow the money', text: 'Review payments and financial evidence', prompt: 'Find payments, invoices, amounts, refunds and financial losses.' },
  { icon: Mail, color: 'mint', title: 'Review correspondence', text: 'Surface promises and disputed statements', prompt: 'Find emails, letters, promises, admissions and disputed statements.' },
  { icon: Scale, color: 'lavender', title: 'Check agreements', text: 'Find duties, terms and obligations', prompt: 'Find contract terms, agreements, obligations and potential breaches.' },
];

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = 'The request could not finish. Try again.';
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') message = body.detail;
    } catch { /* Keep the recovery message for a non-JSON response. */ }
    throw new Error(message);
  }
  return response.json();
}
const json = (body: unknown, method = 'POST') => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const formatDate = (date: string) => new Date(date).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const formatSize = (size: number) => size < 1024 * 1024 ? Math.max(1, Math.round(size / 1024)) + ' KB' : (size / 1024 / 1024).toFixed(1) + ' MB';

function Mark({ large = false }: { large?: boolean }) {
  return <div className={large ? 'mark mark-large' : 'mark'}><FolderOpen aria-hidden="true" /><span className="mark-spark"><Sparkles aria-hidden="true" /></span></div>;
}

export default function App() {
  const [cases, setCases] = useState<Case[]>([]);
  const [caseId, setCaseId] = useState('');
  const [view, setView] = useState<View>('chat');
  const [settings, setSettings] = useState<Settings>();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [caseLoading, setCaseLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [caseForm, setCaseForm] = useState(false);
  const [caseName, setCaseName] = useState('');
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState('');
  const [dragging, setDragging] = useState(false);
  const [question, setQuestion] = useState('');
  const [sending, setSending] = useState(false);
  const [running, setRunning] = useState(false);
  const [useAI, setUseAI] = useState(false);
  const [selectedDocs, setSelectedDocs] = useState<string[]>([]);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [docQuery, setDocQuery] = useState('');
  const [agentForm, setAgentForm] = useState<Partial<Agent> | null>(null);
  const [savingAgent, setSavingAgent] = useState(false);
  const [expandedTask, setExpandedTask] = useState('');
  const [evidence, setEvidence] = useState<Evidence>();
  const [passageId, setPassageId] = useState('');
  const [consent, setConsent] = useState<Consent>();
  const [draftProvider, setDraftProvider] = useState('');
  const [draftModel, setDraftModel] = useState('');
  const [savingModel, setSavingModel] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const evidenceRef = useRef<HTMLDialogElement>(null);
  const consentRef = useRef<HTMLDialogElement>(null);
  const activeCase = useRef(caseId);
  const refreshTicket = useRef(0);
  const threadEnd = useRef<HTMLDivElement>(null);
  activeCase.current = caseId;
  const currentCase = cases.find(c => c.id === caseId);
  const runningTasks = tasks.filter(task => ['running', 'queued'].includes(task.status)).length;
  const base = '/api/cases/' + caseId;

  async function refresh(id: string, initial = false) {
    const ticket = ++refreshTicket.current;
    if (initial) setCaseLoading(true);
    try {
      const [docs, specialists, reviews, chat] = await Promise.all([
        api<Document[]>('/api/cases/' + id + '/documents'),
        api<Agent[]>('/api/cases/' + id + '/agents'),
        api<Task[]>('/api/cases/' + id + '/tasks'),
        api<Message[]>('/api/cases/' + id + '/messages'),
      ]);
      if (activeCase.current !== id || ticket !== refreshTicket.current) return;
      setDocuments(docs); setAgents(specialists); setTasks(reviews); setMessages(chat);
      setSelectedDocs(previous => previous.filter(value => docs.some(doc => doc.id === value)));
      setSelectedAgents(previous => previous.filter(value => specialists.some(agent => agent.id === value)));
      setCases(previous => previous.map(c => c.id === id ? { ...c, document_count: docs.length } : c));
    } catch (err) {
      if (activeCase.current === id && ticket === refreshTicket.current) setError((err as Error).message);
    } finally {
      if (activeCase.current === id && ticket === refreshTicket.current && initial) setCaseLoading(false);
    }
  }

  useEffect(() => {
    let mounted = true;
    Promise.all([api<Case[]>('/api/cases'), api<Settings>('/api/settings')])
      .then(([list, config]) => {
        if (!mounted) return;
        setCases(list); setSettings(config);
        const saved = localStorage.getItem('casefiles-active-case');
        setCaseId(list.find(c => c.id === saved)?.id || list[0]?.id || '');
      })
      .catch(err => { if (mounted) setError(err.message); })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    ++refreshTicket.current;
    setDocuments([]); setAgents([]); setTasks([]); setMessages([]);
    setSelectedDocs([]); setSelectedAgents([]); setExpandedTask(''); setAgentForm(null);
    setQuestion(''); setConsent(undefined); setEvidence(undefined); setError(''); setNotice('');
    if (caseId) {
      localStorage.setItem('casefiles-active-case', caseId);
      void refresh(caseId, true);
    }
  }, [caseId]);

  useEffect(() => {
    if (!caseId || !runningTasks) return;
    const interval = window.setInterval(() => { void refresh(caseId); }, 1500);
    return () => window.clearInterval(interval);
  }, [caseId, runningTasks]);

  useEffect(() => { threadEnd.current?.scrollIntoView({ behavior: 'instant', block: 'end' }); }, [messages.length, sending]);
  useEffect(() => {
    if (evidence) evidenceRef.current?.showModal();
    else evidenceRef.current?.close();
  }, [evidence]);
  useEffect(() => {
    if (consent) consentRef.current?.showModal();
    else consentRef.current?.close();
  }, [consent]);
  useEffect(() => {
    if (settings) { setDraftProvider(settings.provider.id); setDraftModel(settings.provider.model); }
  }, [settings?.provider.id, settings?.provider.model]);
  useEffect(() => {
    if (evidence && passageId) {
      document.getElementById('passage-' + passageId)?.scrollIntoView({ block: 'center' });
    }
  }, [evidence, passageId]);

  async function createCase(event: FormEvent) {
    event.preventDefault(); setCreating(true); setError('');
    try {
      const created = await api<Case>('/api/cases', json({ name: caseName }));
      setCases(previous => [...previous, created]); setCaseId(created.id);
      setCaseForm(false); setCaseName(''); setView('chat');
    } catch (err) { setError((err as Error).message); }
    finally { setCreating(false); }
  }

  function chooseUpload() {
    if (!caseId) { setCaseForm(true); return; }
    inputRef.current?.click();
  }

  async function uploadFiles(files: FileList | File[]) {
    if (!caseId || uploading) return;
    const id = caseId;
    setUploading(true); setError(''); setNotice('');
    const failures: string[] = [];
    let count = 0;
    for (const file of Array.from(files)) {
      setUploadProgress(file.name);
      const form = new FormData(); form.append('file', file);
      try { await api('/api/cases/' + id + '/documents', { method: 'POST', body: form }); ++count; }
      catch (err) { failures.push(file.name + ': ' + (err as Error).message); }
    }
    if (activeCase.current === id) {
      await refresh(id);
      setNotice(count ? count + ' document' + (count === 1 ? '' : 's') + ' uploaded and ready to review.' : '');
      setError(failures.join(' '));
    }
    setUploading(false); setUploadProgress('');
    if (inputRef.current) inputRef.current.value = '';
  }

  async function approveOrRun(title: string, docs: Document[], action: (approved: boolean) => Promise<void>) {
    if (useAI && settings?.provider.external) {
      setConsent({ title, documents: [...docs], action: () => action(true) });
    } else await action(false);
  }

  async function sendQuestion(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || sending || !caseId) return;
    const id = caseId, text = question.trim(), mode = useAI;
    const docs = [...documents];
    const provider = settings?.provider;
    setError('');
    await approveOrRun('Answer your case question', docs, async approved => {
      setConsent(undefined); setSending(true);
      try {
        await api('/api/cases/' + id + '/chat', json({ question: text, document_ids: docs.map(doc => doc.id), use_ai: mode, approved_external: approved, provider_id: provider?.id, model: provider?.model }));
        if (activeCase.current === id) { setQuestion(''); await refresh(id); }
      } catch (err) { if (activeCase.current === id) setError((err as Error).message); }
      finally { setSending(false); }
    });
  }

  async function runAgents(ids: string[]) {
    if (!ids.length || running || !caseId) return;
    const id = caseId, mode = useAI;
    const docs = selectedDocs.length ? documents.filter(doc => selectedDocs.includes(doc.id)) : [...documents];
    const provider = settings?.provider;
    setError('');
    await approveOrRun('Run ' + ids.length + ' topic agent' + (ids.length === 1 ? '' : 's'), docs, async approved => {
      setConsent(undefined); setRunning(true);
      try {
        await api('/api/cases/' + id + '/reviews', json({ agent_ids: ids, document_ids: docs.map(doc => doc.id), use_ai: mode, approved_external: approved, provider_id: provider?.id, model: provider?.model }));
        if (activeCase.current === id) { setView('tasks'); await refresh(id); }
      } catch (err) { if (activeCase.current === id) setError((err as Error).message); }
      finally { setRunning(false); }
    });
  }

  async function saveAgent(event: FormEvent) {
    event.preventDefault();
    if (!agentForm) return;
    setSavingAgent(true); setError('');
    const id = caseId, form = { ...agentForm };
    try {
      await api('/api/cases/' + id + '/agents' + (form.id ? '/' + form.id : ''), json(form, form.id ? 'PUT' : 'POST'));
      if (activeCase.current === id) { setAgentForm(null); await refresh(id); setNotice('Topic agent saved.'); }
    } catch (err) { if (activeCase.current === id) setError((err as Error).message); }
    finally { setSavingAgent(false); }
  }

  async function openEvidence(docId: string, passage = '') {
    const id = caseId;
    try {
      const doc = await api<Evidence>('/api/cases/' + id + '/documents/' + docId);
      if (activeCase.current === id) { setPassageId(passage); setEvidence(doc); }
    } catch (err) { setError((err as Error).message); }
  }

  async function exportCase() {
    try {
      const data = await api(base + '/export');
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const link = document.createElement('a'); link.href = url;
      link.download = 'case-review-' + caseId + '.json'; link.click();
      URL.revokeObjectURL(url); setNotice('Case review exported. Original documents stay in your workspace.');
    } catch (err) { setError((err as Error).message); }
  }

  async function saveModel(event: FormEvent) {
    event.preventDefault(); setSavingModel(true); setError('');
    try {
      const config = await api<Settings>('/api/models/selection', json({ provider_id: draftProvider, model: draftModel.trim() }, 'PUT'));
      setSettings(config); setUseAI(previous => previous && config.provider.configured);
      setNotice(config.provider.name + ' selected with ' + config.provider.model + '. ' + (config.provider.configured ? 'Enable AI analysis when you want to use it.' : 'Add its API key in secure server settings to enable analysis.'));
    } catch (err) { setError((err as Error).message); }
    finally { setSavingModel(false); }
  }

  function CitationButton({ cite }: { cite: Citation }) {
    return <button className="citation" onClick={() => void openEvidence(cite.document_id, cite.passage_id)}>
      <FileText size={15} aria-hidden="true" /><span>{cite.document_name}</span><span className="citation-page">{cite.document_name.toLowerCase().endsWith('.pdf') ? 'p.' : 'section'} {cite.page}</span><ArrowRight size={14} aria-hidden="true" />
    </button>;
  }

  function ModeControl() {
    return <label className={'mode-control' + (!settings?.provider.configured ? ' muted' : '')}>
      <input type="checkbox" checked={useAI} disabled={!settings?.provider.configured} onChange={event => setUseAI(event.target.checked)} />
      <Sparkles size={14} aria-hidden="true" /> AI analysis
      {!settings?.provider.configured && <span className="mode-note">not configured</span>}
    </label>;
  }

  const busy = sending || running || uploading || creating || savingAgent || savingModel;
  const visibleDocs = documents.filter(doc => doc.name.toLowerCase().includes(docQuery.toLowerCase()));
  const modelProvider = settings?.providers.find(provider => provider.id === draftProvider);

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="#chat" onClick={() => setView('chat')}><Mark /><span>Case File <strong>Manager</strong></span><span className="version">WORKSPACE</span></a>
      <div className="topbar-right"><span className="local-status"><ShieldCheck size={15} aria-hidden="true" /> Local storage</span><button className="icon-button" aria-label="Open settings" onClick={() => setView('settings')}><Settings2 size={18} /></button><span className="avatar" aria-label="Local workspace">CF</span></div>
    </header>

    <aside className="sidebar">
      <div className="case-switcher">
        <label htmlFor="case-select">Your workspace</label>
        <div className="select-wrap"><FolderClosed size={16} aria-hidden="true" /><select id="case-select" value={caseId} disabled={busy || loading} onChange={event => setCaseId(event.target.value)}>
          {!cases.length && <option value="">Create your first case</option>}
          {cases.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select><ChevronDown size={14} aria-hidden="true" /></div>
        <button className="new-case" onClick={() => setCaseForm(value => !value)} disabled={busy}><Plus size={14} aria-hidden="true" /> New case</button>
      </div>
      <nav aria-label="Workspace">{navigation.map(item => <button key={item.id} className={'nav-item' + (view === item.id ? ' active' : '')} aria-current={view === item.id ? 'page' : undefined} onClick={() => setView(item.id)}>
        <item.icon size={18} aria-hidden="true" /><span>{item.title}</span>
        {item.id === 'documents' && documents.length > 0 && <span className="nav-count" aria-hidden="true">{documents.length}</span>}
        {item.id === 'tasks' && runningTasks > 0 && <span className="nav-count" aria-hidden="true">{runningTasks}</span>}
      </button>)}</nav>
      <div className="sidebar-note"><BookOpen size={18} aria-hidden="true" /><strong>Every finding has a source.</strong><p>Keep your documents together. Let each agent look through a different lens.</p></div>
      <div className="sidebar-bottom"><button className={'nav-item' + (view === 'settings' ? ' active' : '')} onClick={() => setView('settings')}><Settings2 size={18} aria-hidden="true" /> Settings</button><div className="privacy-caption"><span className="status-dot" /> Your case. Your control.</div></div>
    </aside>

    <main className={'workspace view-' + view}>
      <div className="workspace-bar"><div className="breadcrumb"><FolderOpen size={15} aria-hidden="true" /><span>{currentCase?.name || 'Your workspace'}</span><span>/</span><strong>{view === 'settings' ? 'Settings' : navigation.find(item => item.id === view)?.title}</strong></div>{caseId && <button className="text-button" onClick={() => void exportCase()}><ArrowDownToLine size={14} aria-hidden="true" /> Export review</button>}</div>
      <div className="status-messages" aria-live="polite">
        {error && <div className="alert error" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}><X size={16} /></button></div>}
        {notice && <div className="alert success"><CircleCheck size={17} aria-hidden="true" /><span>{notice}</span><button aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={16} /></button></div>}
      </div>
      {(caseForm || (!caseId && !loading)) && <form className="case-form" onSubmit={createCase}>
        <div><h2>{cases.length ? 'Start another case' : 'Give your first case a home'}</h2><p>A separate workspace for its documents, agents and findings.</p></div>
        <label className="sr-only" htmlFor="case-name">Case name</label><input id="case-name" value={caseName} onChange={event => setCaseName(event.target.value)} placeholder="Case name" maxLength={120} required autoFocus />
        <button className="button primary" disabled={creating || !caseName.trim()}>{creating ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />} Create case</button>
        {caseId && <button type="button" className="icon-button" aria-label="Cancel new case" onClick={() => setCaseForm(false)}><X size={18} /></button>}
      </form>}

      {loading || caseLoading ? <div className="loading-workspace" aria-label="Loading workspace"><div className="skeleton short" /><div className="skeleton" /><div className="skeleton" /><p>Opening your workspace…</p></div> : <>
        {view === 'chat' && <div className="chat-workspace">
          {messages.length === 0 ? <div className="welcome">
            <Mark large /><h1>Your case, seen from every angle.</h1><p>One place for your documents. A focused agent for every question.</p>
            <div className="welcome-actions"><button className="button primary" onClick={chooseUpload} disabled={uploading}><Plus size={16} aria-hidden="true" /> Upload documents</button><button className="button subtle" onClick={() => setView('agents')}><UsersRound size={16} aria-hidden="true" /> Meet your agents <ArrowRight size={14} aria-hidden="true" /></button></div>
            <div className="quick-starts">{starters.map(starter => <button key={starter.title} className="quick-start" onClick={() => { setQuestion(starter.prompt); composerRef.current?.focus(); }}>
              <span className={'starter-icon ' + starter.color}><starter.icon size={20} aria-hidden="true" /></span><span><strong>{starter.title}</strong><small>{starter.text}</small></span><ArrowRight className="starter-arrow" size={15} aria-hidden="true" />
            </button>)}</div>
            <div className="welcome-footnote"><ShieldCheck size={14} aria-hidden="true" /> Documents stay local. You choose when AI can see excerpts.</div>
          </div> : <div className="chat-thread" aria-label="Case conversation">{messages.map(message => <article key={message.id} className={'message ' + message.role}>
            <div className="message-avatar">{message.role === 'assistant' ? <FolderOpen size={18} /> : <span>You</span>}</div><div className="message-body"><div className="message-label">{message.role === 'assistant' ? 'Case assistant' : 'You'}{message.role === 'assistant' && <span className="small-badge">{message.mode === 'ai' ? 'AI analysis' : 'Evidence search'}</span>}</div><p>{message.text}</p>
              {message.citations.length > 0 && <div className="chat-citations">{message.citations.map(cite => <div key={cite.passage_id}><CitationButton cite={cite} /><blockquote>{cite.quote.length > 300 ? cite.quote.slice(0, 300) + '…' : cite.quote}</blockquote></div>)}</div>}
            </div>
          </article>)}{sending && <div className="working-message" role="status"><LoaderCircle className="spin" size={16} /> {useAI ? 'Reviewing approved evidence…' : 'Searching your documents…'}</div>}<div ref={threadEnd} /></div>}
          <div className="composer-container">
            <form className="composer" onSubmit={sendQuestion}>
              <label className="sr-only" htmlFor="question">Ask a question about your case</label>
              <textarea ref={composerRef} id="question" value={question} onChange={event => setQuestion(event.target.value)} placeholder={documents.length ? 'Ask about your case, or give the agents a topic to explore…' : 'Upload your documents, then ask a question about your case…'} rows={3} maxLength={2000} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} />
              <div className="composer-tools"><div className="composer-left"><button type="button" className="icon-button attachment" onClick={chooseUpload} aria-label="Attach documents" disabled={uploading}><Paperclip size={18} /></button><span className="document-context"><FileText size={14} aria-hidden="true" /> {documents.length} document{documents.length === 1 ? '' : 's'}</span><span className="tool-divider" /><ModeControl /></div><button className="send-button" aria-label="Send question" disabled={!question.trim() || !documents.length || sending || !caseId}>{sending ? <LoaderCircle className="spin" size={18} /> : <ArrowUp size={20} />}</button></div>
            </form><p className="composer-note">Source-backed review. Check the evidence before relying on a finding.{!settings?.provider.configured && <button onClick={() => setView('models')}>Choose an AI model</button>}</p>
          </div>
        </div>}

        {view === 'documents' && <section className="page-content">
          <div className="page-heading"><div><h1>Case documents</h1><p>Your shared evidence library. Every agent works from these sources.</p></div><button className="button primary" onClick={chooseUpload} disabled={uploading}><Plus size={16} /> Upload documents</button></div>
          <div className={'upload-zone' + (dragging ? ' dragging' : '')} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (!caseId) setCaseForm(true); else void uploadFiles(event.dataTransfer.files); }}>
            <span className="upload-icon">{uploading ? <LoaderCircle className="spin" size={25} /> : <ArrowUp size={25} />}</span><div><strong>{uploading ? 'Reading ' + uploadProgress : 'Drop your documents here'}</strong><p>PDF, Word, text, Markdown, email, CSV and Excel · up to 25 MB each</p></div><button className="button" onClick={chooseUpload} disabled={uploading}>{uploading ? 'Uploading…' : 'Browse files'}</button>
          </div>
          <div className="table-toolbar"><label className="search-field"><Search size={16} /><input aria-label="Find a document" placeholder="Find a document…" value={docQuery} onChange={event => setDocQuery(event.target.value)} /></label><span>{selectedDocs.length ? selectedDocs.length + ' selected for review' : documents.length + ' documents'}</span></div>
          {documents.length ? <div className="document-table" role="table" aria-label="Case documents"><div className="document-row table-head" role="row"><input type="checkbox" aria-label="Select all visible documents" checked={visibleDocs.length > 0 && visibleDocs.every(doc => selectedDocs.includes(doc.id))} onChange={event => setSelectedDocs(event.target.checked ? [...new Set([...selectedDocs, ...visibleDocs.map(doc => doc.id)])] : selectedDocs.filter(id => !visibleDocs.some(doc => doc.id === id)))} /><span>Document</span><span>Size</span><span>Added</span><span /></div>
            {visibleDocs.map(doc => <div className="document-row" key={doc.id} role="row"><input type="checkbox" aria-label={'Select ' + doc.name} checked={selectedDocs.includes(doc.id)} onChange={event => setSelectedDocs(event.target.checked ? [...selectedDocs, doc.id] : selectedDocs.filter(id => id !== doc.id))} /><button className="document-title" onClick={() => void openEvidence(doc.id)}><span className="file-icon"><FileText size={20} /></span><span><strong>{doc.name}</strong><small><span className="status-dot" /> Ready to review · {doc.pages} {doc.name.toLowerCase().endsWith('.pdf') ? 'pages' : 'sections'}</small></span></button><span className="size-cell">{formatSize(doc.size)}</span><span className="date-cell">{formatDate(doc.created_at)}</span><button className="icon-button" aria-label={'Open ' + doc.name} onClick={() => void openEvidence(doc.id)}><ArrowRight size={17} /></button></div>)}
            {!visibleDocs.length && <p className="no-results">No documents match this name. Try another search.</p>}
          </div> : <div className="empty-state"><FileText size={32} /><h2>Start with the evidence</h2><p>Upload your case documents above. Their text becomes searchable, and every finding links back to its source.</p></div>}
          {documents.length > 0 && <div className="review-footer"><span>{selectedDocs.length ? 'Your selection is ready for focused review.' : 'All documents will be included unless you select a subset.'}</span><button className="button" onClick={() => setView('agents')}><UsersRound size={16} /> Choose topic agents <ArrowRight size={15} /></button></div>}
          <p className="help-note">Scanned PDFs need OCR before uploading. Original files are preserved; Word locations refer to extracted sections rather than printed page numbers.</p>
        </section>}

        {view === 'agents' && <section className="page-content">
          <div className="page-heading"><div><h1>A different lens for every topic.</h1><p>Give each agent a focus. Run them together on the same case evidence.</p></div><button className="button" onClick={() => caseId ? setAgentForm({ name: '', instruction: '', keywords: '', icon: 'search' }) : setCaseForm(true)}><Plus size={16} /> Create agent</button></div>
          <div className="review-controls"><div><strong>{selectedDocs.length || documents.length} documents in scope</strong><button className="text-button" onClick={() => setView('documents')}>Change selection</button></div><ModeControl /><button className="button primary" disabled={!documents.length || running || !agents.length} onClick={() => void runAgents(selectedAgents.length ? selectedAgents : agents.map(agent => agent.id))}><Sparkles size={16} /> {running ? 'Starting…' : selectedAgents.length ? 'Run selected agents' : 'Run all agents'}</button></div>
          {agentForm && <form className="agent-form" onSubmit={saveAgent}>
            <div className="form-heading"><h2>{agentForm.id ? 'Edit topic agent' : 'Create a topic agent'}</h2><button type="button" className="icon-button" aria-label="Cancel agent form" onClick={() => setAgentForm(null)}><X size={18} /></button></div>
            <div className="form-grid"><label>Name<input required maxLength={80} value={agentForm.name || ''} onChange={event => setAgentForm({ ...agentForm, name: event.target.value })} placeholder="e.g. Property repairs" /></label><label>Search terms<input required maxLength={500} value={agentForm.keywords || ''} onChange={event => setAgentForm({ ...agentForm, keywords: event.target.value })} placeholder="repair, leak, notice, inspection" /></label></div>
            <label>Review instruction<textarea required rows={3} maxLength={2000} value={agentForm.instruction || ''} onChange={event => setAgentForm({ ...agentForm, instruction: event.target.value })} placeholder="Describe the topic and what this agent should look for." /></label><p>Search terms retrieve candidate passages. The instruction guides AI analysis when enabled.</p><button className="button primary" disabled={savingAgent}>{savingAgent ? 'Saving…' : 'Save agent'}</button>
          </form>}
          <div className="agent-list">{agents.map((agent, index) => {
            const Icon = agentIcons[agent.icon] || Search;
            return <article className="agent-row" key={agent.id}>
              <input type="checkbox" aria-label={'Select agent ' + agent.name} checked={selectedAgents.includes(agent.id)} onChange={event => setSelectedAgents(event.target.checked ? [...selectedAgents, agent.id] : selectedAgents.filter(id => id !== agent.id))} /><span className={'agent-icon ' + ['blue', 'amber', 'mint', 'lavender'][index % 4]}><Icon size={23} /></span>
              <div className="agent-description"><h2>{agent.name}</h2><p>{agent.instruction}</p><div className="keyword-list">{agent.keywords.split(',').slice(0, 6).map((word, i) => <span key={i}>{word.trim()}</span>)}</div></div>
              <div className="agent-actions"><button className="icon-button" aria-label={'Edit agent ' + agent.name} onClick={() => setAgentForm({ ...agent })}><Pencil size={16} /></button><button className="button" disabled={!documents.length || running} onClick={() => void runAgents([agent.id])}>Run agent <ArrowRight size={15} /></button></div>
            </article>;
          })}</div>
          {!agents.length && <div className="empty-state"><UsersRound size={32} /><h2>First, create a case</h2><p>Four focused agents come with every new case. Add your own for the topics that matter to you.</p></div>}
          <div className="explanation-note"><BookOpen size={20} /><p><strong>Focused review, traceable results.</strong> Agents run independently. Evidence search finds matching passages; AI analysis interprets those excerpts when you enable it. Neither mode establishes that unmatched evidence is absent.</p></div>
        </section>}

        {view === 'tasks' && <section className="page-content">
          <div className="page-heading"><div><h1>Review tasks</h1><p>Follow each agent's progress, then inspect its findings against the evidence.</p></div><button className="button" onClick={() => setView('agents')}><Plus size={16} /> Start a review</button></div>
          {tasks.length ? <div className="task-list">{tasks.map(task => <article className="task-item" key={task.id}>
            <button className="task-summary" aria-expanded={expandedTask === task.id} onClick={() => setExpandedTask(expandedTask === task.id ? '' : task.id)}>
              <span className={'task-symbol ' + task.status}>{task.status === 'completed' ? <Check size={19} /> : task.status === 'failed' ? <X size={19} /> : <LoaderCircle size={19} className="spin" />}</span>
              <span className="task-title"><strong>{task.name}</strong><small>{formatDate(task.created_at)} · {task.mode === 'ai' ? 'AI analysis' : 'Evidence search'}</small></span>
              <span className={'status-badge ' + task.status}>{task.status}</span><span className="finding-count">{task.status === 'completed' ? task.findings.length + ' findings' : task.status === 'failed' ? 'Review failed' : 'In progress'}</span><ChevronDown size={17} className={expandedTask === task.id ? 'rotated' : ''} />
            </button>
            {expandedTask === task.id && <div className="task-details">{task.status === 'failed' ? <div className="task-error"><p>{task.error}</p><button className="button" onClick={() => void runAgents([task.agent_id])} disabled={running}>Run agent again</button></div> : task.status !== 'completed' ? <p className="help-note">This agent is reviewing the selected evidence. Results appear when it finishes.</p> : task.findings.length ? task.findings.map((finding, i) => <div className="finding" key={i}><p>{finding.analysis}</p><blockquote>{finding.quote}</blockquote><CitationButton cite={finding} /></div>) : <div className="no-findings"><Search size={22} /><p>{task.mode === 'ai' ? 'This analysis returned no cited findings. Refine the review instruction or search terms and try again.' : 'No matching passages were found. Refine the agent’s search terms and try again.'} This is not a conclusion that the topic is absent from your case.</p><button className="text-button" onClick={() => setView('agents')}>Adjust agent topics <ArrowRight size={14} /></button></div>}</div>}
          </article>)}</div> : <div className="empty-state"><Layers3 size={32} /><h2>Your review history starts here</h2><p>Upload documents and run a topic agent. Its progress and source-backed findings will appear here.</p><button className="button primary" onClick={() => setView('agents')}>Choose an agent <ArrowRight size={15} /></button></div>}
        </section>}

        {view === 'models' && <section className="page-content models-page">
          <div className="page-heading"><div><h1>Choose who reviews your evidence.</h1><p>Claude, ChatGPT, DeepSeek or OpenRouter. One clear choice for your next analysis.</p></div></div>
          <div className="active-model"><Bot size={21} /><div><strong>Current selection: {settings?.provider.name}</strong><span>{settings?.provider.model} · {settings?.provider.configured ? 'Ready for analysis' : 'API key needed'}</span></div><span className="small-badge">Applies to new reviews</span></div>
          <form onSubmit={saveModel}>
            <fieldset className="provider-options"><legend>AI provider</legend>{settings?.providers.map(provider => <label key={provider.id} className={'provider-choice' + (draftProvider === provider.id ? ' selected' : '')}>
              <input type="radio" name="provider" value={provider.id} checked={draftProvider === provider.id} onChange={() => { setDraftProvider(provider.id); setDraftModel(provider.model); }} />
              <span className={'provider-monogram provider-' + provider.id}>{provider.id === 'anthropic' ? 'C' : provider.id === 'openai' ? 'G' : provider.id === 'deepseek' ? 'D' : provider.id === 'openrouter' ? 'O' : 'L'}</span>
              <span className="provider-copy"><strong>{provider.name}</strong><small>{provider.id === 'anthropic' ? 'Claude models, directly from Anthropic' : provider.id === 'openai' ? 'GPT models through the OpenAI API' : provider.id === 'deepseek' ? 'Chat and reasoning from DeepSeek' : provider.id === 'openrouter' ? 'A choice of models through one provider' : 'Your configured local or compatible endpoint'}</small></span>
              <span className={'provider-status' + (provider.configured ? ' ready' : '')}>{provider.configured ? <><CircleCheck size={13} /> Key configured</> : 'Key needed'}</span>
            </label>)}</fieldset>
            <div className="model-selection"><label htmlFor="model-id">Model identifier</label><div className="model-field"><input id="model-id" list="model-suggestions" value={draftModel} onChange={event => setDraftModel(event.target.value)} maxLength={200} required placeholder="Choose or enter a model identifier" /><datalist id="model-suggestions">{modelProvider?.models?.map(model => <option key={model} value={model} />)}</datalist><button className="button primary" disabled={savingModel || !draftProvider || !draftModel.trim()}>{savingModel ? 'Saving…' : 'Use this model'}<Check size={16} /></button></div><p>Choose a suggested model or enter the exact identifier from your provider. Availability depends on your account.</p></div>
          </form>
          <div className="model-credentials"><ShieldCheck size={22} /><div><h2>{modelProvider?.configured ? 'Your API key is configured' : 'Connect ' + (modelProvider?.name || 'your provider')}</h2><p>{modelProvider?.configured ? 'The key stays on the server and is never sent to this interface.' : 'Add your key through secure server environment settings, then restart the workspace. Keys are never stored in your browser or case exports.'}</p><div className="credential-name"><span>Server setting</span><code>{modelProvider?.key_env}</code></div><p className="help-note">You can configure several providers and switch between them here. A ChatGPT or Claude subscription is separate from API access and billing.</p></div></div>
          <div className="explanation-note"><BookOpen size={20} /><p><strong>You approve each external request.</strong> Changing a model does not send documents. Before analysis starts, the approval shows the provider, model and documents in scope. Running jobs keep the model they started with.</p></div>
        </section>}

        {view === 'settings' && <section className="page-content settings-page">
          <div className="page-heading"><div><h1>Your workspace, your control.</h1><p>Local evidence storage. Optional AI. Clear consent before anything is sent.</p></div></div>
          <section className="settings-section"><div className="settings-icon"><ShieldCheck size={22} /></div><div><h2>Document storage</h2><p>Original documents, extracted passages, agents and review history are stored on this machine.</p><dl><dt>Storage</dt><dd>Local files + SQLite</dd><dt>Access</dt><dd>Single-user workspace on this machine</dd><dt>Location</dt><dd className="path-value">{settings?.data_location}</dd></dl><p className="help-note">Keep this release on loopback. Multi-user authentication and the Windows launcher from the original plan are not implemented yet.</p></div></section>
          <section className="settings-section"><div className="settings-icon"><Sparkles size={22} /></div><div><h2>AI analysis <span className={'small-badge ' + (settings?.provider.configured ? 'ready' : '')}>{settings?.provider.configured ? 'Configured' : 'Not configured'}</span></h2><p>Evidence search works without AI. Choose Claude, ChatGPT, DeepSeek or OpenRouter to interpret relevant passages and produce cited findings.</p>
            <dl><dt>Provider</dt><dd>{settings?.provider.name}</dd><dt>Model</dt><dd>{settings?.provider.model}</dd><dt>Approval</dt><dd>{settings?.provider.external ? 'Required for every external request' : 'Local model; no external sending'}</dd></dl><button className="button" onClick={() => setView('models')}><Bot size={15} /> Open Model management <ArrowRight size={14} /></button>
            <p className="help-note">External approval covers your question or agent instructions, file names, and up to {settings?.provider.max_excerpts || 18} relevant excerpts per request. Original files are not uploaded to the provider. Provider retention rules still apply.</p>
          </div></section>
          <section className="settings-section"><div className="settings-icon"><FolderOpen size={22} /></div><div><h2>Files and limits</h2><p>PDF, DOCX, TXT, Markdown, CSV, EML and XLSX. Up to 25 MB per file. Scanned documents need OCR before import; legacy Word and Outlook PST files need conversion.</p><p>Review exports include findings and cited text. Back up the data directory to preserve original documents too.</p></div></section>
          <section className="settings-section"><div className="settings-icon"><Bot size={22} /></div><div><h2>Inspired by Octop</h2><p>The shared workspace and specialist-agent pattern adapt TencentCloud/Octop to legal evidence review. This application uses its own implementation and identity.</p><a className="inline-link" href="https://github.com/TencentCloud/Octop" target="_blank" rel="noreferrer">Explore the reference <ArrowRight size={14} /></a></div></section>
        </section>}
      </>}
      <input ref={inputRef} className="sr-only" type="file" aria-label="Choose case documents" multiple accept=".pdf,.docx,.txt,.md,.csv,.eml,.xlsx" onChange={event => { if (event.target.files) void uploadFiles(event.target.files); }} />
      {uploading && view !== 'documents' && <div className="upload-toast" role="status"><LoaderCircle size={16} className="spin" /><span>Reading {uploadProgress}</span></div>}
    </main>

    <dialog ref={evidenceRef} className="evidence-dialog" aria-labelledby="source-dialog-title" onCancel={() => setEvidence(undefined)} onClick={event => { if (event.target === event.currentTarget) setEvidence(undefined); }}>
      {evidence && <><div className="dialog-header"><div><span className="dialog-label">Source document</span><h2 id="source-dialog-title">{evidence.name}</h2></div><button className="icon-button" aria-label="Close source document" onClick={() => setEvidence(undefined)}><X size={20} /></button></div><div className="evidence-toolbar"><span>Extracted text · {evidence.pages} {evidence.name.toLowerCase().endsWith('.pdf') ? 'pages' : 'sections'}</span><a className="button" href={base + '/documents/' + evidence.id + '/download'} download><ArrowDownToLine size={15} /> Original file</a></div><div className="evidence-text">{evidence.passages.map(passage => <section key={passage.id} id={'passage-' + passage.id} className={passage.id === passageId ? 'highlighted-passage' : ''}><span>{evidence.name.toLowerCase().endsWith('.pdf') ? 'Page' : 'Section'} {passage.page}{passage.id === passageId && ' · Cited passage'}</span><p>{passage.text}</p></section>)}</div></>}
    </dialog>

    <dialog ref={consentRef} className="consent-dialog" aria-labelledby="consent-dialog-title" onCancel={() => setConsent(undefined)}>
      {consent && <><div className="dialog-header"><div className="consent-title"><ShieldCheck size={24} /><h2 id="consent-dialog-title">Approve external AI analysis</h2></div><button className="icon-button" aria-label="Cancel external AI request" onClick={() => setConsent(undefined)}><X size={20} /></button></div><p><strong>{consent.title}</strong> will send your question or agent instructions, file names and selected evidence excerpts to <strong>{settings?.provider.host}</strong>, using <strong>{settings?.provider.model}</strong>.</p><p>Up to {settings?.provider.max_excerpts} passages per request. Original files stay on this machine. Your provider's data-retention rules apply.</p><div className="consent-documents"><strong>Documents in scope</strong>{consent.documents.map(doc => <span key={doc.id}><FileText size={15} /> {doc.name}</span>)}</div><p className="help-note">This approval applies to this action only.</p><div className="dialog-actions"><button className="button" onClick={() => setConsent(undefined)}>Keep it local</button><button className="button primary" onClick={() => void consent.action()}>Approve and continue <ArrowRight size={15} /></button></div></>}
    </dialog>
  </div>;
}
