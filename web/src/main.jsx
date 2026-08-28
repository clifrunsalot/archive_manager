import { createRoot } from 'react-dom/client'
import { useEffect, useMemo, useState } from 'react'
import {
  Activity, Archive, ArrowUpRight, Check, ChevronDown, CircleHelp, ClipboardList,
  FileText, Filter, FolderUp, HardDrive, Menu, MoreHorizontal, Search, Send,
  Settings, ShieldCheck, SlidersHorizontal, Sparkles, Upload, UserRound, X
} from 'lucide-react'
import './styles.css'
import { apiEnabled, executeMutation, executeReset, getActivity, getArtifacts, getEvents, getIntakeJob, getMutationJob, getReadiness, getSecurityStatus, getSession, previewDelete, previewExpiredPurge, previewIntake, previewReset, submitIntake, submitQuery } from './api'

const events = [
  { id: 'repair-2025-11-25', type: 'automotive_service', pages: 4, owner: 'cliftonhudson', expires: '2026-11-25', status: 'Active' },
  { id: 'tax-2024-q4', type: 'tax', pages: 2, owner: 'cliftonhudson', expires: '2027-04-15', status: 'Active' },
  { id: 'medical-2023-05', type: 'medical', pages: 1, owner: 'cliftonhudson', expires: '2025-05-08', status: 'Expired' },
  { id: 'insurance-home-2025', type: 'insurance', pages: 6, owner: 'alice', expires: '2027-01-14', status: 'Active' },
]

const activityEntries = [
  { timestamp: '2026-08-27T02:01:37Z', kind: 'query', status: 'completed', summary: 'completed query', user: 'cliftonhudson' },
  { timestamp: '2026-08-27T01:59:12Z', kind: 'ingestion', status: 'completed', summary: 'repair-2025-11-25.png ingested', user: 'cliftonhudson' },
  { timestamp: '2026-08-27T01:54:03Z', kind: 'mutation', status: 'failed', summary: 'delete 1 event', user: 'cliftonhudson' },
]

const navItems = [
  { id: 'search', label: 'Search & RAG', icon: Search },
  { id: 'intake', label: 'Intake & Ingest', icon: FolderUp },
  { id: 'catalog', label: 'Event Catalog', icon: ClipboardList },
  { id: 'activity', label: 'Activity & Logs', icon: Activity },
  { id: 'security', label: 'Security & Admin', icon: ShieldCheck },
]

function App() {
  const [activeTab, setActiveTab] = useState('search')
  const [question, setQuestion] = useState('')
  const [asked, setAsked] = useState(false)
  const [liveAnswer, setLiveAnswer] = useState(null)
  const [queryLoading, setQueryLoading] = useState(false)
  const [session, setSession] = useState(null)
  const [readiness, setReadiness] = useState(null)
  const [securityStatus, setSecurityStatus] = useState(null)
  const [activityEntriesLive, setActivityEntriesLive] = useState(null)
  const [intakeDraft, setIntakeDraft] = useState([])
  const [apiEvents, setApiEvents] = useState(null)
  const [apiArtifacts, setApiArtifacts] = useState(null)
  const [apiError, setApiError] = useState(null)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    document.querySelectorAll([
      '.help-button',
      '.mobile-menu',
      '.sidebar-bottom .nav-item',
      '.query-options input',
      '.query-options select',
      '.query-options .text-button',
      '.answer-column .icon-button',
      '.answer-footer .outline-button',
      '.evidence-item .icon-button',
      '.catalog-toolbar .outline-button',
      '.operation .icon-text-button',
    ].join(',')).forEach((control) => { control.disabled = true })
  }, [])

  useEffect(() => {
    if (!apiEnabled) return
    Promise.allSettled([getSession(), getEvents(), getArtifacts(), getReadiness(), getSecurityStatus(), getActivity()])
      .then(([sessionResult, eventsResult, artifactsResult, readinessResult, securityResult, activityResult]) => {
        if (sessionResult.status === 'fulfilled') setSession(sessionResult.value)
        if (readinessResult.status === 'fulfilled') setReadiness(readinessResult.value)
        if (securityResult.status === 'fulfilled') setSecurityStatus(securityResult.value)
        if (activityResult.status === 'fulfilled') setActivityEntriesLive(activityResult.value)
        if (artifactsResult.status === 'fulfilled') setApiArtifacts(artifactsResult.value)
        if (eventsResult.status === 'fulfilled') setApiEvents(eventsResult.value.map((event) => ({
          id: event.event_id,
          type: event.event_type,
          pages: event.pages.length,
          owner: event.owner || 'Unassigned',
          expires: event.expires_at || 'Policy managed',
          status: event.status || 'Active',
        })))
        const failedResult = [sessionResult, eventsResult, artifactsResult, readinessResult, securityResult, activityResult].find((result) => result.status === 'rejected')
        if (failedResult) { setApiError(failedResult.reason.message); setReadiness((current) => current || { status: 'degraded' }) }
      })
  }, [])

  useEffect(() => {
    if (!apiEnabled || activeTab !== 'activity') return undefined
    const refreshActivity = async () => {
      try { setActivityEntriesLive(await getActivity()) } catch (error) { setApiError(error.message) }
    }
    const timer = window.setInterval(refreshActivity, 2000)
    return () => window.clearInterval(timer)
  }, [activeTab])

  useEffect(() => {
    if (!apiEnabled || activeTab !== 'catalog') return undefined
    const refreshCatalog = async () => {
      try {
        const [eventData, artifactData] = await Promise.all([getEvents(), getArtifacts()])
        setApiArtifacts(artifactData)
        setApiEvents(eventData.map((event) => ({
          id: event.event_id,
          type: event.event_type,
          pages: event.pages.length,
          owner: event.owner || 'Unassigned',
          expires: event.expires_at || 'Policy managed',
          status: event.status || 'Active',
        })))
      } catch (error) { setApiError(error.message) }
    }
    refreshCatalog()
  }, [activeTab])

  const askQuestion = async () => {
    if (!question.trim()) return
    setAsked(false)
    setLiveAnswer(null)
    if (!apiEnabled) return
    setQueryLoading(true)
    setApiError(null)
    try {
      const response = await submitQuery(question)
      setLiveAnswer(response.answer)
      setAsked(true)
    } catch (error) {
      setApiError(error.message)
    } finally {
      setQueryLoading(false)
    }
  }

  const active = navItems.find((item) => item.id === activeTab)
  const ActiveIcon = active.icon
  const catalogCount = apiEvents ? apiEvents.length : events.length

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark"><Archive size={19} /></div>
          <div><strong>Archive Manager</strong><span>Private document intelligence</span></div>
        </div>
        <div className="top-status">
          <span className="status-pill secure"><span className="status-dot" /> Sensitive mode</span>
          <span className="status-pill"><Activity size={14} /> {apiEnabled ? (readiness?.status === 'ready' ? 'Services ready' : session ? 'Services degraded' : 'Connecting') : 'Review mode'}</span>
          <button className="avatar-button" onClick={() => setMenuOpen(!menuOpen)} aria-label="Open user menu"><UserRound size={17} /><span>{session?.user || 'cliftonhudson'}</span><ChevronDown size={14} /></button>
          {menuOpen && <div className="user-menu"><strong>cliftonhudson</strong><span>Authenticated via OIDC</span><button onClick={() => setMenuOpen(false)}>Sign out</button></div>}
        </div>
        <button className="mobile-menu" aria-label="Open navigation"><Menu size={20} /></button>
      </header>

      <div className="workspace">
        <aside className="sidebar">
          <div className="sidebar-label">Workspace</div>
          <nav>{navItems.map((item) => {
            const Icon = item.icon
            return <button key={item.id} className={activeTab === item.id ? 'nav-item active' : 'nav-item'} onClick={() => setActiveTab(item.id)}><Icon size={17} /><span>{item.label}</span>{item.id === 'catalog' && <span className="nav-count" aria-label={`${catalogCount} catalog events`}>{catalogCount}</span>}</button>
          })}</nav>
          <div className="sidebar-bottom">
            <div className="storage-meter"><div className="meter-title"><HardDrive size={15} /> Local archive <span>42%</span></div><div className="meter"><span /></div><small>4.2 GB of 10 GB used</small></div>
            <button className="nav-item"><Settings size={17} /><span>Preferences</span></button>
            <div className="version">ARCHIVE MANAGER <b>v0.1</b></div>
          </div>
        </aside>

        <main className="main-content">
          <div className="page-heading">
            <div><div className="eyebrow"><ActiveIcon size={14} /> {active.label}</div><h1>{activeTab === 'search' ? 'Ask your archive' : active.label}</h1><p>{getSubtitle(activeTab)}</p></div>
            <button className="help-button" title="Help"><CircleHelp size={18} /></button>
          </div>
          {apiError && <div className="api-alert" role="alert"><CircleHelp size={16} /> {apiError}</div>}
          <div hidden={activeTab !== 'search'}><SearchView question={question} setQuestion={setQuestion} asked={asked} setAsked={setAsked} liveAnswer={liveAnswer} onAsk={askQuestion} loading={queryLoading} /></div>
          <div hidden={activeTab !== 'intake'}><IntakeView onDraftChange={setIntakeDraft} /></div>
          <div hidden={activeTab !== 'catalog'}><CatalogView eventRows={apiEvents || events} artifacts={apiArtifacts || []} /></div>
          <div hidden={activeTab !== 'activity'}><ActivityView entries={activityEntriesLive || activityEntries} intakeDraft={intakeDraft} /></div>
          <div hidden={activeTab !== 'security'}><SecurityView securityStatus={securityStatus} /></div>
        </main>
      </div>
    </div>
  )
}

function getSubtitle(tab) {
  return { search: 'Search across authorized documents with grounded answers and provenance.', intake: 'Bring documents into the archive and group related pages into events.', catalog: 'Review event scope, retention, and lifecycle actions.', security: 'Review protection state and operational controls.' }[tab]
}

function SearchView({ question, setQuestion, asked, setAsked, liveAnswer, onAsk, loading }) {
  return <>
    <section className="query-panel panel">
      <div className="panel-kicker"><Sparkles size={15} /> Grounded query</div>
      <label htmlFor="question">What would you like to find?</label>
      <div className="query-input-wrap"><textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} rows="2" /><button className="clear-button" onClick={() => setQuestion('')} disabled={!question || loading} title="Clear question" aria-label="Clear question"><X size={16} /></button><button className="send-button" onClick={onAsk} disabled={loading} title="Ask question">{loading ? <Activity className="spin" size={17} /> : <Send size={17} />}</button></div>
      <div className="query-options"><label>Output <select defaultValue=""><option value="" disabled>Choose format</option><option value="summary">Summary</option><option value="table">Markdown table</option><option value="diagram">Mermaid diagram</option></select></label><label>Top-K <input type="number" min="1" max="50" placeholder="Optional" /></label><label className="wide-option">Filename filter <input placeholder="Optional filename" /></label><label className="check-label"><input type="checkbox" /> Strict authorization</label><button className="text-button"><SlidersHorizontal size={15} /> More filters</button></div>
    </section>
    {!asked && <div className="empty-query-state"><Search size={19} /><span>Ask a question to search authorized archive content.</span></div>}
    {asked && liveAnswer && <section className="answer-layout">
      <div className="answer-column panel"><div className="result-header"><div><span className="success-label"><Check size={14} /> Answer ready</span><h2>{liveAnswer ? 'Archive response' : '2025 service records'}</h2></div><button className="icon-button" title="More actions"><MoreHorizontal size={18} /></button></div>{liveAnswer ? <FormattedAnswer answer={liveAnswer} /> : <p className="answer-copy">Two automotive service events were recorded in 2025. The documented total is <strong>$1,138.29</strong>, including brake pad replacement and inspection work on November 25.</p>}{!liveAnswer && <div className="fact-grid"><Fact label="Events" value="2" /><Fact label="Total charges" value="$1,138.29" /><Fact label="Latest service" value="Nov 25, 2025" /></div>}<div className="answer-footer"><span><ShieldCheck size={14} /> Scope checked for {apiEnabled ? 'authenticated user' : 'cliftonhudson'}</span><button className="outline-button"><ArrowUpRight size={15} /> Export report</button></div></div>
      {!liveAnswer && <div className="evidence-column"><div className="section-title"><div><span className="eyebrow">Evidence</span><h2>Retrieved sources</h2></div><span className="source-count">2 sources</span></div><Evidence filename="repair-2025-11-25.png" page="Page 1" score="0.892" text="RO# 10452 TOTAL CHARGES $1138.29 BRAKE PADS REPLACED..." /><Evidence filename="repair-2025-09-12.png" page="Page 1" score="0.847" text="SERVICE COMPLETED: OIL CHANGE AND FILTER REPLACEMENT..." /></div>}
    </section>}
  </>
}
function FormattedAnswer({ answer }) {
  const lines = answer.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
  const tableLines = lines.filter((line) => line.startsWith('|'))
  if (tableLines.length >= 2 && /^\|?\s*:?-{3,}/.test(tableLines[1].replace(/^\|\s*/, ''))) {
    const headers = tableLines[0].split('|').slice(1, -1).map((cell) => cell.trim())
    const rows = tableLines.slice(2).map((line) => line.split('|').slice(1, -1).map((cell) => cell.trim()))
    return <div className="formatted-answer table-answer"><div className="table-scroll"><table><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={`${row.join('-')}-${rowIndex}`}>{row.map((cell, cellIndex) => <td key={`${cell}-${cellIndex}`}>{cell}</td>)}</tr>)}</tbody></table></div></div>
  }
  const processedIndex = lines.findIndex((line) => /^Processed files:\s*/i.test(line))
  if (processedIndex !== -1) {
    const fileText = lines.slice(processedIndex).join(' ').replace(/^Processed files:\s*/i, '')
    const files = fileText.split(/\s+-\s+/).map((file) => file.replace(/^[-*]\s*/, '').trim()).filter(Boolean)
    return <div className="formatted-answer"><strong>Processed files</strong><ul>{files.map((file) => <li key={file}><FileText size={14} /> <span>{file}</span></li>)}</ul></div>
  }
  return <div className="formatted-answer">{lines.map((line, index) => <p key={`${line}-${index}`}>{line}</p>)}</div>
}
function Fact({ label, value }) { return <div><span>{label}</span><strong>{value}</strong></div> }
function Evidence({ filename, page, score, text }) { return <article className="evidence-item"><div className="file-icon"><FileText size={17} /></div><div className="evidence-body"><div className="evidence-meta"><strong>{filename}</strong><span>{page} · match {score}</span></div><p>“{text}”</p></div><button className="icon-button" title="Open source"><ArrowUpRight size={16} /></button></article> }

function IntakeView({ onDraftChange }) {
  const [files, setFiles] = useState(apiEnabled ? [] : ['repair-2026-08-26-p1.jpg', 'repair-2026-08-26-p2.jpg'])
  const [fileObjects, setFileObjects] = useState([])
  const [preview, setPreview] = useState(null)
  const [previewError, setPreviewError] = useState(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [submitLoading, setSubmitLoading] = useState(false)
  const [job, setJob] = useState(null)
  useEffect(() => {
    if (!apiEnabled || !job?.job_id || ['completed', 'completed_with_errors', 'failed'].includes(job.status)) return undefined
    const poll = async () => {
      try { const updatedJob = await getIntakeJob(job.job_id); setJob(updatedJob); if (['failed', 'completed_with_errors'].includes(updatedJob.status)) { const failures = (updatedJob.failed_files || []).map((failure) => `${failure.filename}: ${failure.error}`).join('; '); if (failures) setPreviewError(`${updatedJob.error || 'Ingestion completed with errors'}: ${failures}`) } } catch (error) { setPreviewError(error.message) }
    }
    const timer = window.setInterval(poll, 2000)
    return () => window.clearInterval(timer)
  }, [job?.job_id, job?.status])
  const [eventId, setEventId] = useState('repair-2026-08-26')
  const [eventType, setEventType] = useState('Automotive service')
  const [subjectRef, setSubjectRef] = useState('VIN-2T1BURHE5EC081401')
  const selectFiles = (selected) => { setFileObjects([...fileObjects, ...selected]); const nextFiles = [...files, ...selected.map((file) => file.name)]; setFiles(nextFiles); onDraftChange(nextFiles) }
  const reviewManifest = async () => {
    if (!apiEnabled) { setPreview({ status: 'validated', pages: files.map((file, index) => ({ source_filename: file, page_number: index + 1 })) }); return }
    setPreviewLoading(true); setPreviewError(null)
    try { setPreview(await previewIntake({ eventId, eventType: eventType.toLowerCase().replaceAll(' ', '_'), subjectRef, files: fileObjects })) } catch (error) { setPreviewError(error.message) } finally { setPreviewLoading(false) }
  }
  const startIngestion = async () => {
    if (!apiEnabled) { setJob({ status: 'mock queued' }); return }
    setSubmitLoading(true); setPreviewError(null)
    try { setJob(await submitIntake({ eventId, eventType: eventType.toLowerCase().replaceAll(' ', '_'), subjectRef, allowedUsers: 'cliftonhudson, alice', files: fileObjects })); onDraftChange([]) } catch (error) { setPreviewError(error.message) } finally { setSubmitLoading(false) }
  }
  const resetMetadata = () => {
    setEventId('')
    setEventType('Automotive service')
    setSubjectRef('')
    setPreview(null)
    setPreviewError(null)
    setJob(null)
  }
  const removeFile = (index) => { const nextFiles = files.filter((_, fileIndex) => fileIndex !== index); setFiles(nextFiles); setFileObjects(fileObjects.filter((_, fileIndex) => fileIndex !== index)); onDraftChange(nextFiles) }
  return <div className="intake-grid"><section className="panel intake-main"><div className="panel-kicker"><Upload size={15} /> Event intake wizard</div><h2>Build a new archive event</h2><p className="muted">Upload pages, verify their order, then add the metadata that controls access and retention.</p><label className="dropzone"><input type="file" multiple onChange={(event) => selectFiles(Array.from(event.target.files))} /><Upload size={25} /><strong>Drop pages here or browse</strong><span>PDF, PNG, JPG up to 50 MB each</span></label><div className="upload-list">{files.map((file, index) => <div className="upload-row" key={`${file}-${index}`}><span className="drag-handle">≡</span><FileText size={17} /><span>{file}</span><small>Page {index + 1}</small><button className="icon-button" onClick={() => removeFile(index)} title="Remove page"><X size={15} /></button></div>)}</div></section><section className="panel metadata-panel"><div className="panel-kicker"><ClipboardList size={15} /> Event metadata</div><label className="field"><span>Event ID</span><input value={eventId} onChange={(event) => setEventId(event.target.value)} /></label><label className="field"><span>Event type</span><select value={eventType} onChange={(event) => setEventType(event.target.value)}><option>Automotive service</option><option>Tax</option><option>Medical</option><option>Insurance</option><option>General document</option></select></label><label className="field"><span>Subject reference</span><input value={subjectRef} onChange={(event) => setSubjectRef(event.target.value)} /></label><Field label="Allowed users" value="cliftonhudson, alice" /><Field label="Retention" value="365 days" /><button className="primary-button" onClick={reviewManifest} disabled={previewLoading}>{previewLoading ? 'Validating...' : 'Review manifest'} <ArrowUpRight size={16} /></button>{preview && <><span className="form-note"><Check size={14} /> {preview.pages.length} pages validated, {preview.total_bytes ? `${preview.total_bytes} bytes` : 'mock review'}</span><button className="outline-button intake-submit" onClick={startIngestion} disabled={submitLoading}>{submitLoading ? 'Queuing...' : 'Start ingestion'} <ArrowUpRight size={16} /></button></>}{job && <span className="form-note"><Activity size={14} /> Job {job.job_id ? `${job.job_id.slice(0, 8)}: ` : ''}{job.status}</span>}{previewError && <span className="form-error">{previewError}</span>}<span className="form-note"><ShieldCheck size={14} /> Access rules apply at query time</span></section></div>
}
function Field({ label, value, select }) { return <label className="field"><span>{label}</span><div>{select ? <select defaultValue={value}><option>{value}</option><option>Tax</option><option>Medical</option><option>General document</option></select> : <input defaultValue={value} />}{select && <ChevronDown size={15} />}</div></label> }

function CatalogView({ eventRows, artifacts }) {
  const [filter, setFilter] = useState('')
  const [lifecycleMessage, setLifecycleMessage] = useState(null)
  const [pendingMutation, setPendingMutation] = useState(null)
  const [mutationJob, setMutationJob] = useState(null)
  useEffect(() => {
    if (!apiEnabled || !mutationJob?.job_id || ['completed', 'failed'].includes(mutationJob.status)) return undefined
    const poll = async () => {
      try { const updated = await getMutationJob(mutationJob.job_id); setMutationJob(updated); setLifecycleMessage(`${updated.action} ${updated.status} for ${updated.event_ids.length} event${updated.event_ids.length === 1 ? '' : 's'}.`) } catch (error) { setLifecycleMessage(error.message) }
    }
    const timer = window.setInterval(poll, 2000)
    return () => window.clearInterval(timer)
  }, [mutationJob?.job_id, mutationJob?.status])
  const visibleEvents = useMemo(() => eventRows.filter((event) => `${event.id} ${event.type} ${event.owner}`.toLowerCase().includes(filter.toLowerCase())), [eventRows, filter])
  const showExpiredPreview = async () => {
    try {
      const purge = apiEnabled ? await previewExpiredPurge() : { events: visibleEvents.filter((event) => event.status === 'Expired').map((event) => ({ event_id: event.id, expires_at: event.expires })), confirmation_token: null }
      const expired = purge.events
      if (expired.length) setPendingMutation({ token: purge.confirmation_token, phrase: 'PURGE EXPIRED EVENTS', summary: `${expired.length} expired event${expired.length === 1 ? '' : 's'} selected for purge.` })
      setLifecycleMessage(expired.length ? `${expired.length} expired event${expired.length === 1 ? '' : 's'} selected for dry-run purge.` : 'No expired events found.')
    } catch (error) { setLifecycleMessage(error.message) }
  }
  const showDeletePreview = async (eventId) => {
    try {
      const result = apiEnabled ? await previewDelete(eventId) : { filenames: [`${eventId}.pdf`], document_ids: [] }
      setPendingMutation(apiEnabled ? { token: result.confirmation_token, phrase: `DELETE ${eventId}`, summary: `${eventId}: ${result.filenames.length} file${result.filenames.length === 1 ? '' : 's'} and ${result.document_ids.length} indexed document${result.document_ids.length === 1 ? '' : 's'} would be removed.` } : null)
      setLifecycleMessage(`${eventId}: ${result.filenames.length} file${result.filenames.length === 1 ? '' : 's'} and ${result.document_ids.length} indexed document${result.document_ids.length === 1 ? '' : 's'} would be removed.`)
    } catch (error) { setLifecycleMessage(error.message) }
  }
  const confirmMutation = async () => {
    if (!pendingMutation) return
    if (!apiEnabled) { setLifecycleMessage(`${pendingMutation.summary} No changes made in review mode.`); setPendingMutation(null); return }
    try { const result = await executeMutation(pendingMutation.token, pendingMutation.phrase); setMutationJob(result); setLifecycleMessage(`${result.action} queued for ${result.event_ids.length} event${result.event_ids.length === 1 ? '' : 's'}.`); setPendingMutation(null) } catch (error) { setLifecycleMessage(error.message) }
  }
  return <><section className="catalog-toolbar"><div className="search-field"><Search size={17} /><input placeholder="Search event ID, type, or owner" value={filter} onChange={(event) => setFilter(event.target.value)} /></div><button className="outline-button"><Filter size={15} /> Filters</button><button className="primary-button" onClick={showExpiredPreview}>Preview expired purge</button></section>{lifecycleMessage && <div className="api-alert lifecycle-alert"><ShieldCheck size={16} /><span>{lifecycleMessage}{pendingMutation && <><br /><strong>Type “{pendingMutation.phrase}” to confirm.</strong></>}</span>{pendingMutation && <button className="danger-button" onClick={confirmMutation}>Confirm action</button>}<button className="icon-button" onClick={() => { setLifecycleMessage(null); setPendingMutation(null) }} title="Dismiss preview"><X size={15} /></button></div>}<section className="panel table-panel"><div className="table-heading"><div><span className="eyebrow">Authorized records</span><h2>Event catalog</h2></div><span className="source-count">{visibleEvents.length} events</span></div><div className="event-table"><div className="table-row table-head"><span>Event</span><span>Type</span><span>Pages</span><span>Owner</span><span>Expires</span><span>Status</span><span /></div>{visibleEvents.map((event) => <div className="table-row" key={event.id}><strong>{event.id}</strong><span className="type-label">{event.type}</span><span>{event.pages}</span><span>{event.owner}</span><span>{event.expires}</span><span className={event.status === 'Expired' ? 'status-text expired' : 'status-text'}><span className="status-dot" />{event.status}</span><button className="icon-button" title={`Preview deletion of ${event.id}`} onClick={() => showDeletePreview(event.id)}><ArrowUpRight size={16} /></button></div>)}</div></section><section className="panel artifacts-panel"><div className="table-heading"><div><span className="eyebrow">Processed documents</span><h2>Artifacts in archive</h2></div><span className="source-count">{artifacts.length} artifacts</span></div>{artifacts.length ? <div className="artifact-list">{artifacts.map((artifact) => <div className="artifact-row" key={artifact.doc_id}><FileText size={16} /><strong>{artifact.filename}</strong><span>{artifact.event_id || 'Standalone artifact'}</span></div>)}</div> : <p className="muted">No processed artifacts in the archive.</p>}</section></>
}

function ActivityView({ entries, intakeDraft }) {
  const [kind, setKind] = useState('all')
  const [searchTerm, setSearchTerm] = useState('')
  const draftEntries = intakeDraft.length ? [{ timestamp: new Date().toISOString(), kind: 'ingestion', status: 'pending', summary: `${intakeDraft.length} file${intakeDraft.length === 1 ? '' : 's'} selected; awaiting manifest review`, user: 'current browser' }] : []
  const visible = [...draftEntries, ...entries].filter((entry) => {
    const matchesKind = kind === 'all' || entry.kind === kind
    const searchable = `${entry.summary} ${entry.kind} ${entry.status} ${entry.user || ''} ${entry.timestamp}`.toLowerCase()
    return matchesKind && searchable.includes(searchTerm.toLowerCase())
  })
  return <section className="panel activity-panel"><div className="activity-toolbar"><div><span className="eyebrow">Operational history</span><h2>Activity &amp; logs</h2><p className="muted">Sanitized events from queries, ingestion, and lifecycle operations.</p></div><select value={kind} onChange={(event) => setKind(event.target.value)} aria-label="Filter activity"><option value="all">All activity</option><option value="query">Queries</option><option value="ingestion">Ingestion</option><option value="mutation">Lifecycle</option></select></div><div className="activity-search"><Search size={16} /><input value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} placeholder="Search activity" aria-label="Search activity" />{searchTerm && <button className="icon-button" onClick={() => setSearchTerm('')} title="Clear activity search" aria-label="Clear activity search"><X size={15} /></button>}</div><div className="activity-list">{visible.map((entry, index) => <div className="activity-row" key={`${entry.timestamp}-${index}`}><div className={`activity-icon ${entry.status}`}><Activity size={16} /></div><div className="activity-copy"><strong>{entry.summary}</strong><span>{entry.kind} · {entry.user || 'system'}</span></div><time>{formatActivityTime(entry.timestamp)}</time><span className={`activity-status ${entry.status}`}>{entry.status}</span></div>)}</div>{!visible.length && <p className="muted">No activity matches this filter.</p>}</section>
}

function formatActivityTime(timestamp) {
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
}

function SecurityView({ securityStatus }) {
  const [confirmed, setConfirmed] = useState(false)
  const [resetPreview, setResetPreview] = useState(null)
  const [resetMessage, setResetMessage] = useState(null)
  const [resetLoading, setResetLoading] = useState(false)
  const status = securityStatus || { security_mode: 'sensitive', authorization_mode: 'strict', encryption_key_loaded: true, qdrant_key_configured: true, trace_content_redacted: true, user: 'cliftonhudson' }
  const requestResetPreview = async () => {
    setResetLoading(true); setResetMessage(null)
    try {
      const result = apiEnabled ? await previewReset() : { actions: ['Clear Qdrant collection', 'Clear local archive files', 'Delete manifests, facts, cache, and logs'], confirmation_token: null }
      setResetPreview(result)
      setConfirmed(true)
    } catch (error) { setResetMessage(error.message) } finally { setResetLoading(false) }
  }
  const confirmReset = async () => {
    if (!resetPreview) return
    if (!apiEnabled) { setResetMessage('Review mode: no changes made.'); setResetPreview(null); setConfirmed(false); return }
    setResetLoading(true)
    try { await executeReset(resetPreview.confirmation_token); setResetMessage('Archive reset complete.'); setResetPreview(null); setConfirmed(false) } catch (error) { setResetMessage(error.message) } finally { setResetLoading(false) }
  }
  return <div className="security-grid"><section className="panel security-overview"><div className="panel-kicker"><ShieldCheck size={15} /> Protection state</div><div className="security-hero"><div className="shield-large"><ShieldCheck size={25} /></div><div><h2>{status.security_mode === 'sensitive' ? 'Sensitive mode active' : 'Compatibility mode active'}</h2><p>Fail-closed controls are protecting this archive.</p></div><span className="live-badge">{securityStatus ? 'Live' : 'Review'}</span></div><div className="security-list"><SecurityRow label="Manifest encryption" value={status.encryption_key_loaded ? 'Active · Fernet key loaded' : 'Not configured'} /><SecurityRow label="Authorization" value={status.authorization_mode === 'strict' ? 'Strict' : 'Compatibility'} /><SecurityRow label="Trace content" value={status.trace_content_redacted ? 'Redacted' : 'Enabled'} /><SecurityRow label="Audit identity" value={status.user} /></div></section><section className="panel operations"><div className="panel-kicker"><Settings size={15} /> Operations</div><Operation icon={HardDrive} title="Filesystem permissions" description="Owner-only access on sensitive storage." action="Apply hardening" /><Operation icon={Activity} title="Service health" description="Qdrant, Ollama, and OCR are local-only." action="View status" /><Operation icon={Archive} title="Generated artifacts" description="Remove logs and reports after confirmation." action="Review cleanup" /></section><section className="danger-zone panel"><div><span className="eyebrow">Danger zone</span><h2>System reset</h2><p>Preview and clear selected archive storage areas. This action cannot be undone.</p>{resetMessage && <div className="notice"><ShieldCheck size={15} /> {resetMessage}</div>}{resetPreview && <div className="reset-actions">{resetPreview.actions.map((action) => <span key={action}>{action}</span>)}<strong>Type “RESET ARCHIVE” to confirm.</strong></div>}</div>{resetPreview ? <button className="danger-button" onClick={confirmReset} disabled={resetLoading}>{resetLoading ? 'Resetting...' : 'Confirm reset'}</button> : <button className="danger-button" onClick={requestResetPreview} disabled={resetLoading}>{resetLoading ? 'Preparing...' : 'Preview reset'}</button>}</section></div>
}
function SecurityRow({ label, value }) { return <div className="security-row"><span>{label}</span><strong><Check size={14} /> {value}</strong></div> }
function Operation({ icon: Icon, title, description, action }) { return <div className="operation"><div className="operation-icon"><Icon size={18} /></div><div><strong>{title}</strong><p>{description}</p></div><button className="icon-text-button">{action}<ArrowUpRight size={14} /></button></div> }

export default App

const rootElement = document.getElementById('root')
const reactRoot = globalThis.__archiveManagerRoot || createRoot(rootElement)
globalThis.__archiveManagerRoot = reactRoot
reactRoot.render(<App />)
