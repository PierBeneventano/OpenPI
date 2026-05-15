import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const vscode = acquireVsCodeApi();

const emptyState = {
  root: '',
  view: 'home',
  loading: true,
  loaded: false,
  campaigns: [],
  settings: {},
  diagnostics: {},
  campaignDetails: null,
  campaignWorkspace: null,
  campaignGraph: null,
  campaignArtifacts: [],
  campaignEvents: [],
  campaignExecution: null,
  campaignDecisions: [],
  campaignFeedback: [],
  campaignDeliverables: [],
  campaignPlannedOutputs: [],
  campaignDiagnosticArtifacts: [],
  campaignRunSummary: { runs: [], latestRun: null, feedback: [] },
  selectedGraphNode: null,
  artifactPreview: null,
  activeRun: null,
  runLog: [],
  steering: {},
  openClaude: { status: 'idle', model: 'openai/gpt-5-mini', models: [], transcript: [], actions: [], contextLinks: [] },
  openClaudeContextLinks: []
};

function App() {
  const [state, setState] = useState(emptyState);
  const [newCampaignOpen, setNewCampaignOpen] = useState(false);
  const [deleteCandidate, setDeleteCandidate] = useState(null);
  const [workspaceTab, setWorkspaceTab] = useState('graph');

  useEffect(() => {
    const listener = (event) => {
      if (event.data && event.data.type === 'state') {
        setState({ ...emptyState, ...event.data.state });
      }
    };
    window.addEventListener('message', listener);
    vscode.postMessage({ type: 'ready' });
    return () => window.removeEventListener('message', listener);
  }, []);

  useEffect(() => {
    if (!deleteCandidate || !state.loaded) return;
    const exists = (state.campaigns || []).some((campaign) => {
      const refs = [campaign.path, campaign.name, campaign.campaign_id, campaign.id, campaign.title].filter(Boolean).map(String);
      return refs.includes(String(deleteCandidate.ref));
    });
    if (state.view === 'home' && !exists && !state.actionError) {
      setDeleteCandidate(null);
    }
  }, [state.generatedAt, state.view, state.loaded, state.actionError, state.campaigns, deleteCandidate]);

  if (state.view === 'campaign') {
    return (
      <>
        <CampaignWorkspace
          state={state}
          tab={workspaceTab}
          setTab={setWorkspaceTab}
          requestDelete={setDeleteCandidate}
        />
        {deleteCandidate ? <DeleteCampaignModal target={deleteCandidate} state={state} onClose={() => setDeleteCandidate(null)} /> : null}
      </>
    );
  }

  return (
    <>
      <Home
        state={state}
        newCampaignOpen={newCampaignOpen}
        setNewCampaignOpen={setNewCampaignOpen}
        requestDelete={setDeleteCandidate}
      />
      {deleteCandidate ? <DeleteCampaignModal target={deleteCandidate} state={state} onClose={() => setDeleteCandidate(null)} /> : null}
    </>
  );
}

function Home({ state, newCampaignOpen, setNewCampaignOpen, requestDelete }) {
  const campaigns = state.campaigns || [];
  const firstLoad = state.loading && !state.loaded;
  const totalArtifacts = campaigns.reduce((sum, campaign) => sum + Number(campaign.artifactCount || 0), 0);
  const activeCampaigns = campaigns.filter((campaign) => ['running', 'planned', 'draft'].includes(String(campaign.status || ''))).length;

  return (
    <div className="app-shell">
      <header className="home-header">
        <div>
          <p className="eyebrow">MSc Campaigns</p>
          <h1>Campaign Workspace</h1>
          <p className="subtle">{state.root}</p>
        </div>
        <div className="header-actions">
          <button onClick={() => vscode.postMessage({ type: 'refresh' })}>Refresh</button>
          <button onClick={() => vscode.postMessage({ type: 'openSettings' })}>Diagnostics</button>
          <button className="primary" onClick={() => setNewCampaignOpen(true)}>New Campaign</button>
        </div>
      </header>

      {state.loading ? <LoadingNotice /> : null}
      {state.actionError ? <div className="notice error">{state.actionError}</div> : null}
      <ErrorSummary errors={state.errors || []} />

      {firstLoad ? (
        <DashboardLoadingState />
      ) : (
        <>
          <section className="metrics-grid">
            <Metric label="Campaigns" value={campaigns.length} detail="local specs" />
            <Metric label="Active" value={activeCampaigns} detail="draft, planned, or executing" />
            <Metric label="Deliverables" value={totalArtifacts} detail="produced and planned" />
            <Metric label="OpenRouter" value={state.settings?.openRouterConfigured ? 'Ready' : 'Missing'} detail="settings check" />
          </section>

          <section className="campaign-grid">
            {campaigns.length ? campaigns.map((campaign) => {
              const campaignRef = campaign.path || campaign.name;
              return (
              <article
                key={campaignRef}
                className="campaign-card"
              >
                <div className="card-topline">
                  <span className={`status-dot status-${statusClass(campaign.status)}`} />
                  <span>{campaign.status || 'unknown'}</span>
                </div>
                <h2>{campaign.title || campaign.name || 'Untitled campaign'}</h2>
                <p>{campaign.path || campaign.workspaceRoot || 'No path'}</p>
                <div className="card-facts">
                  <span>{formatBudget(campaign.budget)}</span>
                  <span>{campaign.artifactCount || 0} outputs</span>
                  <span>{campaign.requiredMissing || 0} planned</span>
                </div>
                <div className="card-actions">
                  <button className="primary" onClick={() => vscode.postMessage({ type: 'selectCampaign', campaign: campaignRef })}>Open</button>
                  <button className="danger" onClick={() => requestDelete(deleteTargetForCampaign(campaign))}>Delete</button>
                </div>
              </article>
            );}) : (
              <div className="empty-panel">
                <h2>No campaigns yet</h2>
                <p>Create a draft campaign to start shaping the local workflow without launching anything.</p>
                <button className="primary" onClick={() => setNewCampaignOpen(true)}>New Campaign</button>
              </div>
            )}
          </section>
        </>
      )}

      {state.settingsOpen ? <DiagnosticsModal state={state} /> : null}
      {newCampaignOpen ? <NewCampaignModal onClose={() => setNewCampaignOpen(false)} /> : null}
    </div>
  );
}

function deleteTargetForCampaign(campaign) {
  const ref = campaign.path || campaign.name || campaign.campaign_id || campaign.id || campaign.title;
  return {
    ref,
    label: campaign.title || campaign.name || campaign.campaign_id || campaign.id || ref || 'this campaign'
  };
}

function DeleteCampaignModal({ target, state, onClose }) {
  const [confirmation, setConfirmation] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const canDelete = confirmation === 'DELETE' && target?.ref && !deleting;
  useEffect(() => {
    if (state.actionError) {
      setDeleting(false);
    }
  }, [state.actionError]);
  useEffect(() => {
    if (!deleting) return undefined;
    const timer = window.setTimeout(() => {
      setTimedOut(true);
      setDeleting(false);
    }, 15000);
    return () => window.clearTimeout(timer);
  }, [deleting]);
  function submit(event) {
    event.preventDefault();
    if (!canDelete) return;
    setTimedOut(false);
    setDeleting(true);
    vscode.postMessage({ type: 'deleteCampaign', campaign: target.ref, confirm: 'DELETE' });
  }
  return (
    <div className="modal-backdrop">
      <form className="modal delete-modal" onSubmit={submit}>
        <div className="modal-head">
          <div>
            <h2>Delete Campaign</h2>
            <p className="subtle">{target.label}</p>
          </div>
          <button type="button" onClick={onClose} disabled={deleting}>Cancel</button>
        </div>
        {deleting ? <LoadingNotice text="Deleting campaign files and refreshing the dashboard..." /> : null}
        {state.actionError ? <div className="notice error">{state.actionError}</div> : null}
        {timedOut ? (
          <div className="notice error">
            No delete response came back from the extension backend. Reload the VS Code window, reopen the dashboard, and try again.
          </div>
        ) : null}
        <div className="notice error">
          This removes the campaign record, campaign bundle, results workspace, graph snapshot, and local chat history.
        </div>
        <label>Type DELETE to confirm
          <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} disabled={deleting} autoFocus />
        </label>
        <button className="danger" type="submit" disabled={!canDelete}>{deleting ? 'Deleting...' : 'Delete Campaign'}</button>
      </form>
    </div>
  );
}

function CampaignWorkspace({ state, tab, setTab, requestDelete }) {
  const [chatOpen, setChatOpen] = useState(false);
  const details = state.campaignDetails || {};
  const title = details.name || details.campaign_id || basename(state.selectedCampaign) || 'Campaign';
  const execution = state.campaignExecution || {};
  const currentStage = graphNodesFromState(state).find((node) => node.id === execution.current_stage_id);
  const openClaude = state.openClaude || emptyState.openClaude;
  const inspectorTab = tab === 'graph' ? 'decisions' : tab;

  return (
    <div className="app-shell workspace-shell graph-workspace">
      <header className="workspace-header graph-header">
        <div className="workspace-title">
          <button onClick={() => vscode.postMessage({ type: 'backToCampaigns' })}>Back</button>
          <div>
            <p className="eyebrow">Research Graph</p>
            <h1>{title}</h1>
            <p className="subtle">{details.metadata?.objective || details.path || state.selectedCampaign}</p>
          </div>
        </div>
        <div className="header-actions">
          <span className={`pill status-${statusClass(statusOf(details))}`}>{statusOf(details)}</span>
          <span className="pill">{currentStage?.label || 'No active stage'}</span>
          <button className="primary" onClick={() => setChatOpen(true)}>AI Helper</button>
          <button onClick={() => vscode.postMessage({ type: 'refreshCampaign' })}>Refresh</button>
          <button className="danger" onClick={() => requestDelete(deleteTargetForCampaign({ path: state.selectedCampaign, title }))}>Delete</button>
        </div>
      </header>

      {state.loading ? <LoadingNotice /> : null}
      {state.actionError ? <div className="notice error">{state.actionError}</div> : null}
      <ErrorSummary errors={state.errors || []} />

      <main className="graph-primary-layout">
        <GraphTab state={state} />
        <ContextRail state={state} currentStage={currentStage} />
      </main>

      <section className="inspector-stack">
        <div className="inspector-tabs">
          {['decisions', 'deliverables', 'feedback', 'diagnostics'].map((name) => (
            <button key={name} className={inspectorTab === name ? 'active' : ''} onClick={() => setTab(name)}>
              {capitalize(name)}
            </button>
          ))}
        </div>
        <div className="inspector-body">
          {inspectorTab === 'decisions' ? <DecisionsTab state={state} /> : null}
          {inspectorTab === 'deliverables' ? <DeliverablesTab state={state} /> : null}
          {inspectorTab === 'feedback' ? <FeedbackTab state={state} /> : null}
          {inspectorTab === 'diagnostics' ? <DiagnosticsTab state={state} /> : null}
        </div>
      </section>
      <FloatingOpenClaude state={state} open={chatOpen} setOpen={setChatOpen} />
    </div>
  );
}

function FloatingOpenClaude({ state, open, setOpen }) {
  const openClaude = state.openClaude || emptyState.openClaude;
  if (!open) {
    return (
      <button className="ai-helper-button" onClick={() => setOpen(true)} aria-label="Open AI helper">
        <span>AI</span>
        <small>{openClaude.status || 'idle'}</small>
      </button>
    );
  }
  return (
    <section className="floating-openclaude" aria-label="AI helper">
      <div className="floating-openclaude-head">
        <div>
          <p className="eyebrow">AI Helper</p>
          <h2>OpenClaude</h2>
        </div>
        <div className="floating-openclaude-actions">
          <span className={`pill status-${statusClass(openClaude.status || 'idle')}`}>{openClaude.status || 'idle'}</span>
          <button onClick={() => vscode.postMessage({ type: 'openClaudeStart', model: openClaude.model })}>Refresh</button>
          <button onClick={() => vscode.postMessage({ type: 'openClaudeClearHistory' })}>Clear History</button>
          <button className="danger" onClick={() => vscode.postMessage({ type: 'openClaudeStop' })}>Stop</button>
          <button onClick={() => setOpen(false)}>Hide</button>
        </div>
      </div>
      <ModelSelector openClaude={openClaude} />
      <OpenClaudeChat state={state} />
    </section>
  );
}

function ModelSelector({ openClaude }) {
  const [custom, setCustom] = useState(openClaude.model || 'openai/gpt-5-mini');
  useEffect(() => {
    setCustom(openClaude.model || 'openai/gpt-5-mini');
  }, [openClaude.model]);
  const models = openClaude.models || [];
  function restart(model) {
    vscode.postMessage({ type: 'openClaudeRestartModel', model });
  }
  return (
    <div className="model-control">
      <select value={custom} onChange={(event) => {
        setCustom(event.target.value);
        restart(event.target.value);
      }}>
        <option value={openClaude.model || custom}>{openClaude.model || custom}</option>
        {models.map((item) => <option key={item.id} value={item.model}>{item.id}: {item.model}</option>)}
      </select>
      <input value={custom} onChange={(event) => setCustom(event.target.value)} onBlur={() => restart(custom)} aria-label="Custom OpenClaude model" />
    </div>
  );
}

function OpenClaudeChat({ state }) {
  const [text, setText] = useState('');
  const chatEndRef = useRef(null);
  const openClaude = state.openClaude || emptyState.openClaude;
  const transcript = openClaude.transcript || [];
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ block: 'end' });
  }, [transcript.length, transcript[transcript.length - 1]?.text, openClaude.status]);
  const suggestions = [
    'Continue the campaign and tell me what you changed.',
    'Review the latest deliverables and flag weaknesses.',
    'Rerun the current stage using my feedback.',
    'Compare the linked artifacts and decide what evidence is missing.'
  ];
  function send(value = text) {
    const message = value.trim();
    if (!message) return;
    vscode.postMessage({ type: 'openClaudeSend', text: message, model: openClaude.model });
    setText('');
  }
  return (
    <section className="chat-panel">
      <div className="chat-status-row">
        <div>
          <p className="eyebrow">Research Chat</p>
          <h2>Ask OpenClaude to drive the campaign</h2>
          <p className="subtle">{transcript.length ? `${transcript.length} saved chat message${transcript.length === 1 ? '' : 's'}` : 'Campaign chat history will be saved locally.'}</p>
        </div>
        <span className={`pill status-${statusClass(openClaude.status || 'idle')}`}>{openClaude.status || 'idle'}</span>
      </div>
      {openClaude.error ? <div className="notice error">{openClaude.error}</div> : null}
      <ContextChips links={openClaude.contextLinks || state.openClaudeContextLinks || []} />
      <div className="suggestion-row">
        {suggestions.map((suggestion) => (
          <button key={suggestion} onClick={() => send(suggestion)}>{suggestion}</button>
        ))}
      </div>
      <div className="chat-transcript">
        {transcript.length ? transcript.map((item) => (
          <article key={item.id || `${item.role}-${item.timestamp}`} className={`chat-message role-${item.role}${item.streaming ? ' streaming' : ''}`}>
            <div className="chat-meta">
              <strong>{item.role === 'assistant' ? 'OpenClaude' : item.role === 'user' ? 'You' : 'System'}</strong>
              <span>{item.streaming ? 'responding...' : formatTime(item.timestamp)}</span>
            </div>
            <p>{item.text}</p>
          </article>
        )) : (
          <div className="empty-panel">
            <h2>OpenClaude is ready to become the interface</h2>
            <p>Ask about the campaign, request revisions, link artifacts for context, continue execution, rerun a stage, or reroute the graph in natural language.</p>
          </div>
        )}
        {openClaude.status === 'responding' && !transcript.some((item) => item.streaming) ? (
          <article className="chat-message role-assistant streaming">
            <div className="chat-meta">
              <strong>OpenClaude</strong>
              <span>thinking...</span>
            </div>
            <p>Preparing a response...</p>
          </article>
        ) : null}
        <div ref={chatEndRef} />
      </div>
      <form className="chat-composer" onSubmit={(event) => {
        event.preventDefault();
        send();
      }}>
        <textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="Tell OpenClaude what you want to inspect, change, rerun, or improve..." />
        <button className="primary" type="submit" disabled={!text.trim() || openClaude.status === 'responding'}>
          Send to OpenClaude
        </button>
      </form>
    </section>
  );
}

function ContextChips({ links }) {
  const active = (links || []).filter((link) => link.status === 'active');
  if (!active.length) {
    return <p className="subtle">No linked artifacts yet. Use Link to Chat on a deliverable to give OpenClaude durable context.</p>;
  }
  return (
    <div className="context-chip-row">
      {active.slice(0, 8).map((link) => (
        <span key={link.id} className="context-chip">
          {link.target?.artifact_path || link.target?.artifact_id || link.target?.node_id || link.target?.scope || 'campaign'}
          <button onClick={() => vscode.postMessage({ type: 'updateContextLink', linkId: link.id, status: 'resolved' })}>Resolve</button>
        </span>
      ))}
    </div>
  );
}

function ContextRail({ state, currentStage }) {
  const openClaude = state.openClaude || emptyState.openClaude;
  const links = openClaude.contextLinks?.length ? openClaude.contextLinks : state.openClaudeContextLinks || [];
  const decisions = state.campaignDecisions || [];
  const deliverables = state.campaignDeliverables || [];
  const actions = openClaude.actions || [];
  return (
    <aside className="context-rail">
      <section>
        <p className="eyebrow">Campaign State</p>
        <h2>{state.campaignExecution?.status || 'not started'}</h2>
        <dl className="definition-list compact">
          <dt>Stage</dt><dd>{currentStage?.label || '-'}</dd>
          <dt>Decisions</dt><dd>{decisions.length}</dd>
          <dt>Deliverables</dt><dd>{deliverables.length}</dd>
          <dt>Model</dt><dd>{openClaude.model || '-'}</dd>
        </dl>
      </section>
      <section>
        <h3>Linked Context</h3>
        <ContextChips links={links} />
      </section>
      <section>
        <h3>Latest Deliverables</h3>
        <ArtifactRows artifacts={deliverables.slice(0, 5)} compact />
      </section>
      <section>
        <h3>Autonomous Action Log</h3>
        {actions.length ? (
          <div className="action-log">
            {actions.slice(-12).map((action, index) => <code key={`${action.timestamp}-${index}`}>{action.status}: {action.text}</code>)}
          </div>
        ) : (
          <p className="subtle">OpenClaude actions will appear here as it uses SDK commands and tools.</p>
        )}
      </section>
    </aside>
  );
}

function CampaignExecutionPanel({ state, onStart }) {
  const run = state.activeRun;
  const logs = state.runLog || [];
  const summary = state.campaignRunSummary || {};
  const latestRun = summary.latestRun || null;
  const execution = state.campaignExecution || {};
  const pendingCount = Number(execution.pending_decision_count || (state.campaignDecisions || []).length || 0);
  if (!run && !logs.length) {
    return (
      <section className="run-strip idle">
        <div>
          <strong>{latestRun ? `Campaign execution: ${latestRun.status || execution.status || 'unknown'}` : 'Campaign not started'}</strong>
          <span>
            {latestRun
              ? `${latestRun.exited_at ? `Last completed ${formatTime(latestRun.exited_at)}` : latestRun.started_at ? `Started ${formatTime(latestRun.started_at)}` : 'Execution history is available in diagnostics.'}${pendingCount ? ` | ${pendingCount} decision${pendingCount === 1 ? '' : 's'} pending` : ''}. No local process is attached to this dashboard.`
              : 'The graph is ready for this campaign goal.'}
          </span>
        </div>
        <button className="primary" onClick={onStart}>{latestRun ? 'Continue Campaign' : 'Start Campaign'}</button>
      </section>
    );
  }
  return (
    <section className="run-strip">
      <div className="run-summary">
        <span className={`status-dot status-${statusClass(run?.status || 'idle')}`} />
        <div>
          <strong>{run?.status || 'idle'}</strong>
          <span>{run?.dryRun ? 'dry execution' : 'real local execution'}{run?.pid ? ` | pid ${run.pid}` : ''}</span>
        </div>
      </div>
      <div className="run-actions">
        {run && ['running', 'stopping'].includes(run.status) ? (
          <button className="danger" onClick={() => vscode.postMessage({ type: 'stopCampaign' })}>Stop</button>
        ) : (
          <button onClick={onStart}>Continue Campaign</button>
        )}
      </div>
      {logs.length ? <span className="pill">{logs.length} diagnostic log lines</span> : null}
    </section>
  );
}

function StartCampaignModal({ state, onClose }) {
  const details = state.campaignDetails || {};
  const [run, setRun] = useState({
    task: details.objective || details.description || '',
    dryRun: true,
    tier: 'live-smoke',
    outputFormat: 'markdown',
    budget: 1,
    counsel: false,
    math: false,
    treeSearch: false,
    allowSpend: false,
    confirmation: ''
  });
  const [formError, setFormError] = useState('');

  function update(key, value) {
    setRun((current) => ({ ...current, [key]: value }));
  }

  function submit(event) {
    event.preventDefault();
    const budget = Number.parseInt(String(run.budget), 10);
    if (!run.task.trim()) {
      setFormError('The campaign goal is required before starting execution.');
      return;
    }
    if (!Number.isInteger(budget) || budget < 1 || budget > 10000) {
      setFormError('Budget must be an integer between 1 and 10000.');
      return;
    }
    if (!run.dryRun && (!run.allowSpend || run.confirmation !== 'RUN LOCAL')) {
      setFormError('Real local execution requires allow spend plus confirmation text RUN LOCAL.');
      return;
    }
    vscode.postMessage({ type: 'startCampaign', ...run });
    onClose();
  }

  const realRun = !run.dryRun;
  const blocked = realRun && (!run.allowSpend || run.confirmation !== 'RUN LOCAL');

  return (
    <div className="modal-backdrop">
      <form className="modal run-modal" onSubmit={submit}>
        <div className="modal-head">
          <div>
            <h2>{campaignStarted(state) ? 'Continue Campaign' : 'Start Campaign'}</h2>
            <p className="subtle">Campaign execution uses the campaign goal and attaches events and deliverables to this workspace.</p>
          </div>
          <button type="button" onClick={onClose}>Cancel</button>
        </div>
        {formError ? <div className="notice error">{formError}</div> : null}
        <label>Campaign goal<textarea value={run.task} onChange={(event) => update('task', event.target.value)} required /></label>
        <div className="form-grid">
          <label>Execution profile<select value={run.tier} onChange={(event) => update('tier', event.target.value)}>
            <option value="live-smoke">Live smoke</option>
            <option value="budget">Budget</option>
            <option value="light">Light</option>
            <option value="medium">Medium</option>
            <option value="pro">Pro</option>
            <option value="max">Max</option>
            <option value="ultra">Ultra</option>
          </select></label>
          <label>Budget cap<input type="number" min="1" value={run.budget} onChange={(event) => update('budget', event.target.value)} /></label>
          <label>Output<select value={run.outputFormat} onChange={(event) => update('outputFormat', event.target.value)}>
            <option value="markdown">Markdown</option>
            <option value="latex">LaTeX</option>
          </select></label>
          <label>Mode<select value={run.dryRun ? 'dry' : 'real'} onChange={(event) => update('dryRun', event.target.value === 'dry')}>
            <option value="dry">Dry execution</option>
            <option value="real">Real local execution</option>
          </select></label>
        </div>
        <div className="toggle-row">
          <label><input type="checkbox" checked={run.counsel} onChange={(event) => update('counsel', event.target.checked)} /> Persona counsel</label>
          <label><input type="checkbox" checked={run.math} onChange={(event) => update('math', event.target.checked)} /> Math track</label>
          <label><input type="checkbox" checked={run.treeSearch} onChange={(event) => update('treeSearch', event.target.checked)} /> Tree search</label>
        </div>
        {realRun ? (
          <section className="spend-gate">
            <label><input type="checkbox" checked={run.allowSpend} onChange={(event) => update('allowSpend', event.target.checked)} /> Allow OpenRouter spend for this campaign execution</label>
            <label>Confirmation<input value={run.confirmation} onChange={(event) => update('confirmation', event.target.value)} placeholder="RUN LOCAL" /></label>
          </section>
        ) : null}
        <button className="primary" type="submit" disabled={blocked}>
          {run.dryRun ? 'Start Campaign' : 'Start Real Campaign'}
        </button>
      </form>
    </div>
  );
}

function GraphTab({ state }) {
  const graphNodes = graphNodesFromState(state);
  const graphEdges = graphEdgesFromState(state, graphNodes);
  const selectedNodeId = state.selectedGraphNode || graphNodes[0]?.id || '';
  const selectedNode = graphNodes.find((node) => node.id === selectedNodeId);
  const stageArtifacts = (state.campaignArtifacts || []).filter((artifact) => {
    return !selectedNode || !artifact.stage_id || artifact.stage_id === selectedNode.id;
  });

  return (
    <main className="split-layout">
      <section className="graph-board">
        {graphNodes.length ? (
          <InteractiveGraph nodes={graphNodes} edges={graphEdges} selectedNodeId={selectedNodeId} />
        ) : (
          <Empty title="No graph yet" detail="Draft campaigns show their graph after stages are planned." />
        )}
      </section>

      <aside className="detail-panel">
        <h2>{selectedNode?.label || selectedNode?.id || 'Stage details'}</h2>
        <dl className="definition-list">
          <dt>Kind</dt><dd>{selectedNode?.kind || '-'}</dd>
          <dt>Status</dt><dd>{selectedNode?.status || 'unknown'}</dd>
          <dt>Purpose</dt><dd>{selectedNode?.purpose || '-'}</dd>
          <dt>Workspace</dt><dd>{selectedNode?.workspace || '-'}</dd>
          <dt>Budget</dt><dd>{formatBudget(selectedNode?.budget)}</dd>
          <dt>Tools</dt><dd>{(selectedNode?.tools || []).join(', ') || '-'}</dd>
          <dt>Validators</dt><dd>{(selectedNode?.validators || []).join(', ') || '-'}</dd>
          <dt>Pause</dt><dd>{(selectedNode?.pausePolicy || []).join(', ') || '-'}</dd>
          <dt>Routes</dt><dd>{formatRoutes(selectedNode?.routes)}</dd>
          <dt>Failure</dt><dd>{selectedNode?.fail_reason || '-'}</dd>
        </dl>
        <h3>Stage Outputs</h3>
        <ArtifactRows artifacts={stageArtifacts} />
        <h3>Logs</h3>
        <LogList logs={selectedNode?.logs || []} />
      </aside>
    </main>
  );
}

function InteractiveGraph({ nodes, edges, selectedNodeId }) {
  const viewportRef = useRef(null);
  const [view, setView] = useState({ x: 36, y: 36, scale: 0.78 });
  const [drag, setDrag] = useState(null);
  const layout = useMemo(() => layoutGraph(nodes, edges), [nodes, edges]);

  const zoomAt = useCallback((delta, point = null) => {
    const viewport = viewportRef.current;
    setView((current) => {
      const scale = clamp(current.scale + delta, 0.35, 1.6);
      if (!point || !viewport || scale === current.scale) {
        return { ...current, scale };
      }
      const rect = viewport.getBoundingClientRect();
      const px = point.clientX - rect.left;
      const py = point.clientY - rect.top;
      const worldX = (px - current.x) / current.scale;
      const worldY = (py - current.y) / current.scale;
      return {
        x: px - worldX * scale,
        y: py - worldY * scale,
        scale
      };
    });
  }, []);

  function zoomBy(delta) {
    zoomAt(delta);
  }

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;
    const wheelListener = (event) => {
      event.preventDefault();
      event.stopPropagation();
      zoomAt(event.deltaY > 0 ? -0.08 : 0.08, event);
    };
    viewport.addEventListener('wheel', wheelListener, { passive: false });
    return () => viewport.removeEventListener('wheel', wheelListener);
  }, [zoomAt]);

  function fitGraph() {
    const viewport = viewportRef.current;
    if (!viewport) {
      setView({ x: 36, y: 36, scale: 0.78 });
      return;
    }
    const rect = viewport.getBoundingClientRect();
    const scale = clamp(Math.min((rect.width - 72) / layout.width, (rect.height - 72) / layout.height), 0.35, 1.25);
    setView({ x: 36, y: 36, scale });
  }

  function handleWheel(event) {
    event.preventDefault();
    event.stopPropagation();
    zoomAt(event.deltaY > 0 ? -0.08 : 0.08, event);
  }

  function handlePointerDown(event) {
    if (event.button !== 0) return;
    if (event.target.closest && event.target.closest('.graph-svg-node')) return;
    setDrag({ pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, x: view.x, y: view.y });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handlePointerMove(event) {
    if (!drag || drag.pointerId !== event.pointerId) return;
    setView((current) => ({
      ...current,
      x: drag.x + event.clientX - drag.startX,
      y: drag.y + event.clientY - drag.startY
    }));
  }

  function handlePointerUp(event) {
    if (drag && drag.pointerId === event.pointerId) {
      setDrag(null);
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  return (
    <div className="graph-shell">
      <div className="graph-toolbar">
        <div>
          <strong>Campaign Graph</strong>
          <span>{nodes.length} nodes, {edges.length} links</span>
        </div>
        <div className="graph-tools">
          <button type="button" onClick={() => zoomBy(-0.12)} aria-label="Zoom out">-</button>
          <button type="button" onClick={fitGraph}>Fit</button>
          <button type="button" onClick={() => zoomBy(0.12)} aria-label="Zoom in">+</button>
        </div>
      </div>
      <svg
        ref={viewportRef}
        className={`graph-canvas ${drag ? 'dragging' : ''}`}
        onWheel={handleWheel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
        <defs>
          <marker id="arrow-head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
        </defs>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
          <g className="graph-edges">
            {layout.edges.map((edge) => (
              <path
                key={edge.edgeId}
                className={`graph-edge edge-${statusClass(edge.kind)} edge-route-${edge.routeDirection || 'forward'}`}
                d={edge.path}
                markerEnd="url(#arrow-head)"
              >
                <title>{`${edge.source} -> ${edge.target}`}</title>
              </path>
            ))}
          </g>
          <g className="graph-nodes">
            {layout.nodes.map((node) => (
              <GraphSvgNode key={node.id} node={node} selected={selectedNodeId === node.id} />
            ))}
          </g>
        </g>
      </svg>
    </div>
  );
}

function GraphSvgNode({ node, selected }) {
  const titleLines = wrapLabel(node.label || node.id, 24, 2);
  return (
    <g
      className={`graph-svg-node status-${statusClass(node.status)} ${selected ? 'selected' : ''}`}
      transform={`translate(${node.x} ${node.y})`}
      role="button"
      tabIndex="0"
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation();
        vscode.postMessage({ type: 'selectGraphNode', nodeId: node.id });
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          vscode.postMessage({ type: 'selectGraphNode', nodeId: node.id });
        }
      }}
    >
      <rect width={node.width} height={node.height} rx="6" />
      <circle cx="16" cy="18" r="5" />
      <text x="28" y="21" className="node-kind">{node.kind || 'node'}</text>
      <circle className="node-port node-port-in" cx={node.width / 2} cy="0" r="4" />
      <circle className="node-port node-port-out" cx={node.width / 2} cy={node.height} r="4" />
      <circle className="node-port node-port-side" cx="0" cy={node.height / 2} r="4" />
      {titleLines.map((line, index) => (
        <text key={`${line}-${index}`} x="14" y={46 + index * 17} className="node-title">{line}</text>
      ))}
      <text x="14" y={node.height - 15} className="node-meta">{node.status || 'unknown'} | {node.artifactCount || 0} outputs</text>
    </g>
  );
}

function DecisionsTab({ state }) {
  const decisions = state.campaignDecisions || [];
  if (!decisions.length) {
    return (
      <main className="panel">
        <p className="eyebrow">Human decisions</p>
        <h2>No Decisions Pending</h2>
        <p className="subtle">When the graph reaches a review point, the decision, evidence, and safe actions will appear here.</p>
      </main>
    );
  }
  return (
    <main className="panel">
      <p className="eyebrow">Human decisions</p>
      <h2>Pending Decisions</h2>
      <div className="feedback-list">
        {decisions.map((decision) => (
          <div className="feedback-item" key={decision.id}>
            <div className="card-topline">
              <span className="pill">{decision.status || 'pending'}</span>
              <span className="pill">{decision.target_label || decision.target_type || 'campaign'}</span>
              <span>{formatTime(decision.created_at)}</span>
            </div>
            <h3>{decision.title || 'Human review needed'}</h3>
            <p>{decision.summary || decision.reason || decision.target_type || 'Human review is required.'}</p>
            {decision.reason && decision.reason !== decision.summary ? <p className="subtle">Technical detail: {decision.reason}</p> : null}
            {decision.evidence?.length ? <p className="subtle">{decision.evidence.join(', ')}</p> : null}
            <div className="card-facts">
              {(decision.safe_next_actions || []).map((action) => <span key={action}>{action}</span>)}
            </div>
            <DecisionActionButtons decision={decision} />
          </div>
        ))}
      </div>
    </main>
  );
}

function DecisionActionButtons({ decision }) {
  const actions = decision.safe_next_actions || [];
  if (!actions.length) return null;
  return (
    <div className="decision-actions">
      {actions.map((action) => (
        <button key={action} onClick={() => vscode.postMessage({ type: 'openClaudeSend', text: decisionPrompt(decision, action) })}>
          Ask OpenClaude to {action.replace(/-/g, ' ')}
        </button>
      ))}
    </div>
  );
}

function decisionPrompt(decision, action) {
  const base = `Decision ${decision.id || ''} needs ${action}. Target: ${decision.target_label || decision.target_id || decision.target_type || 'campaign'}.`;
  const reason = decision.reason ? ` Technical detail: ${decision.reason}` : '';
  if (decision.target_type === 'failure_recovery') {
    return `${base}${reason} Diagnose the failed campaign execution, explain the likely root cause in researcher-facing terms, then use the SDK to ${action} or propose the safest recovery path. Do not edit files directly.`;
  }
  return `${base}${reason} Inspect the campaign workspace and use the SDK to carry out or propose this action.`;
}

function FeedbackTab({ state }) {
  const active = state.activeRun && ['running', 'stopping'].includes(state.activeRun.status);
  const steering = state.steering || {};
  const feedback = state.campaignFeedback?.length ? state.campaignFeedback : state.campaignRunSummary?.feedback || [];
  return (
    <main className="split-layout">
      <section className="panel">
        <p className="eyebrow">Researcher feedback</p>
        <h2>Human Feedback</h2>
        <HumanFeedbackForm state={state} />
        <h3>Recent Feedback</h3>
        {feedback.length ? (
          <div className="feedback-list">
            {feedback.slice(0, 6).map((item) => (
              <div className="feedback-item" key={item.id || `${item.created_at}-${item.text}`}>
                <div className="card-topline">
                  <span className="pill">{item.type || 'feedback'}</span>
                  {item.node_id ? <span className="pill">{item.node_id}</span> : null}
                  <span>{formatTime(item.created_at)}</span>
                </div>
                <p>{item.text}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="subtle">No human feedback has been recorded for this campaign yet.</p>
        )}
      </section>

      <section className="panel">
        <p className="eyebrow">Live process control</p>
        <h2>Low-Level Local Steering</h2>
        {active ? (
          <LowLevelSteering steering={steering} />
        ) : (
          <p className="subtle">Start campaign execution from this dashboard before live steering controls appear here. Campaign feedback can still be recorded without an active process.</p>
        )}
        <h3>Assistant readiness</h3>
        <dl className="definition-list">
          <dt>OpenClaude</dt><dd>{state.diagnostics?.openclaude?.launch_ready ? 'ready' : 'not ready'}</dd>
          <dt>OpenRouter</dt><dd>{state.settings?.openRouterConfigured ? 'configured' : 'missing'}</dd>
          <dt>Model</dt><dd>{state.diagnostics?.openclaude?.model || '-'}</dd>
        </dl>
      </section>
    </main>
  );
}

function HumanFeedbackForm({ state }) {
  const stages = graphNodesFromState(state);
  const defaultNode = state.selectedGraphNode || stages[0]?.id || '';
  const latestRun = state.campaignRunSummary?.latestRun || null;
  const [draft, setDraft] = useState({
    text: '',
    nodeId: defaultNode,
    feedbackType: 'feedback',
    attachRun: Boolean(latestRun?.run_id)
  });

  useEffect(() => {
    setDraft((current) => current.nodeId ? current : { ...current, nodeId: defaultNode });
  }, [defaultNode]);

  function update(key, value) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function submit(event) {
    event.preventDefault();
    const text = draft.text.trim();
    if (!text) return;
    vscode.postMessage({
      type: 'submitFeedback',
      text,
      nodeId: draft.nodeId,
      feedbackType: draft.feedbackType,
      runId: draft.attachRun && latestRun?.run_id ? latestRun.run_id : ''
    });
    setDraft((current) => ({ ...current, text: '' }));
  }

  return (
    <form className="feedback-form" onSubmit={submit}>
      <label>Feedback<textarea value={draft.text} onChange={(event) => update('text', event.target.value)} placeholder="Tell the campaign what should change, what needs review, or what future execution should respect." /></label>
      <div className="form-grid">
        <label>Target stage<select value={draft.nodeId} onChange={(event) => update('nodeId', event.target.value)}>
          <option value="">Whole campaign</option>
          {stages.map((stage) => <option key={stage.id} value={stage.id}>{stage.label || stage.id}</option>)}
        </select></label>
        <label>Kind<select value={draft.feedbackType} onChange={(event) => update('feedbackType', event.target.value)}>
          <option value="feedback">Feedback</option>
          <option value="revision">Revision request</option>
          <option value="question">Question</option>
          <option value="approval_note">Approval note</option>
        </select></label>
      </div>
      {latestRun?.run_id ? (
        <label className="check-line"><input type="checkbox" checked={draft.attachRun} onChange={(event) => update('attachRun', event.target.checked)} /> Attach to latest execution</label>
      ) : null}
      <button className="primary" type="submit" disabled={!draft.text.trim()}>Record Feedback</button>
    </form>
  );
}

function LowLevelSteering({ steering }) {
  const [text, setText] = useState('');
  return (
    <div className="stack">
      <div className="fact-row">
        <span>Steering</span>
        <strong>{steering.available ? 'available' : 'unavailable'}</strong>
      </div>
      {steering.lastError ? <div className="notice">Steering not available for this execution: {steering.lastError}</div> : null}
      <button onClick={() => vscode.postMessage({ type: 'interruptRun' })}>Interrupt / Pause</button>
      <textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="Instruction for the active campaign execution" />
      <button className="primary" onClick={() => vscode.postMessage({ type: 'sendInstruction', text, instructionType: 'm' })}>
        Send Instruction
      </button>
    </div>
  );
}

function DeliverablesTab({ state }) {
  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [stageFilter, setStageFilter] = useState('all');
  const [existenceFilter, setExistenceFilter] = useState('existing');
  const [audienceFilter, setAudienceFilter] = useState('deliverables');
  const artifacts = state.campaignArtifacts || [];
  const stages = unique(artifacts.map((artifact) => artifact.stage_id).filter(Boolean));
  const types = unique(artifacts.map((artifact) => artifact.type).filter(Boolean));
  const filtered = artifacts.filter((artifact) => {
    const haystack = `${artifact.label || ''} ${artifact.path || ''} ${artifact.stage_id || ''}`.toLowerCase();
    const matchesQuery = !query || haystack.includes(query.toLowerCase());
    const matchesType = typeFilter === 'all' || artifact.type === typeFilter;
    const matchesStage = stageFilter === 'all' || artifact.stage_id === stageFilter;
    const matchesExistence = existenceFilter === 'all' ||
      (existenceFilter === 'existing' && artifact.exists) ||
      (existenceFilter === 'missing' && !artifact.exists) ||
      (existenceFilter === 'required' && artifact.required) ||
      (existenceFilter === 'optional' && !artifact.required);
    const audience = artifact.audience || artifact.metadata?.audience || '';
    const matchesAudience = audienceFilter === 'all' ||
      (audienceFilter === 'deliverables' && ['deliverable', 'evidence'].includes(audience)) ||
      artifact.audience === audienceFilter ||
      artifact.metadata?.audience === audienceFilter;
    return matchesQuery && matchesType && matchesStage && matchesExistence && matchesAudience;
  });

  return (
    <main className="split-layout artifact-layout">
      <section className="panel">
        <div className="filters">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search deliverables" />
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="all">All types</option>
            {types.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
          <select value={stageFilter} onChange={(event) => setStageFilter(event.target.value)}>
            <option value="all">All stages</option>
            {stages.map((stage) => <option key={stage} value={stage}>{stage}</option>)}
          </select>
          <select value={existenceFilter} onChange={(event) => setExistenceFilter(event.target.value)}>
            <option value="all">All outputs</option>
            <option value="existing">Produced</option>
            <option value="missing">Planned / missing</option>
            <option value="required">Required</option>
            <option value="optional">Optional</option>
          </select>
          <select value={audienceFilter} onChange={(event) => setAudienceFilter(event.target.value)}>
            <option value="deliverables">Deliverables</option>
            <option value="all">All audiences</option>
            <option value="diagnostic">Diagnostics</option>
            <option value="prompt">Prompts</option>
            <option value="log">Logs</option>
            <option value="system_state">System state</option>
          </select>
        </div>
        <ArtifactRows artifacts={filtered} />
      </section>
      <PreviewPanel preview={state.artifactPreview} />
    </main>
  );
}

function DiagnosticsTab({ state }) {
  const events = state.campaignEvents || [];
  const diagnosticArtifacts = state.campaignDiagnosticArtifacts || [];
  const logs = state.runLog || [];
  return (
    <main className="split-layout">
      <section className="panel">
        <p className="eyebrow">Diagnostics</p>
        <h2>Technical State</h2>
        <dl className="definition-list">
          <dt>Events</dt><dd>{events.length}</dd>
          <dt>Diagnostic files</dt><dd>{diagnosticArtifacts.length}</dd>
          <dt>Process log lines</dt><dd>{logs.length}</dd>
          <dt>Execution attempts</dt><dd>{state.campaignExecution?.attempts?.length || 0}</dd>
          <dt>Read source</dt><dd>{state.campaignWorkspace?.provenance?.source || '-'}</dd>
        </dl>
        <h3>Diagnostic Files</h3>
        <ArtifactRows artifacts={diagnosticArtifacts} />
      </section>
      <section className="panel">
        <h2>Process Log</h2>
        {logs.length ? (
          <div className="run-log diagnostic-log">
            {logs.slice(-80).map((entry, index) => (
              <code key={`${entry.timestamp || ''}-${index}`} className={`log-${entry.stream || 'system'}`}>
                {entry.text}
              </code>
            ))}
          </div>
        ) : (
          <p className="subtle">No local process log is attached to this dashboard session.</p>
        )}
      </section>
      <section className="panel">
        <h2>Recent Events</h2>
        <div className="log-list">
          {events.slice(-24).map((event) => (
            <code key={event.id || `${event.type}-${event.created_at}`}>{event.created_at} {event.type}</code>
          ))}
        </div>
      </section>
    </main>
  );
}

function ArtifactRows({ artifacts, compact = false }) {
  if (!artifacts.length) {
    return <p className="subtle">No produced deliverables found for the current filters.</p>;
  }
  return (
    <div className="artifact-list">
      {artifacts.map((artifact) => (
        <div key={artifact.id || artifact.path} className="artifact-row">
          <div>
            <strong>{artifact.label || basename(artifact.path) || 'Output'}</strong>
            <span>{artifact.path || 'No file path'}</span>
          </div>
          <div className="artifact-actions">
            <span className="pill">{artifact.required ? 'required' : 'optional'}</span>
            <span className="pill">{artifact.exists ? 'produced' : 'planned'}</span>
            {artifact.audience || artifact.metadata?.audience ? <span className="pill">{artifact.audience || artifact.metadata?.audience}</span> : null}
            {compact ? null : <button disabled={!artifact.exists} onClick={() => vscode.postMessage({ type: 'previewArtifact', artifact })}>Preview</button>}
            {compact ? null : <button disabled={!artifact.exists} onClick={() => vscode.postMessage({ type: 'openArtifact', artifact })}>Open</button>}
            <button disabled={!artifact.exists} onClick={() => vscode.postMessage({ type: 'linkArtifactContext', artifact })}>Link to Chat</button>
            {compact ? null : <button disabled={!artifact.exists} onClick={() => vscode.postMessage({ type: 'openClaudeSend', text: `Review ${artifact.path} and tell me what is strong, weak, and what should change.` })}>Ask About</button>}
            {compact ? null : <button disabled={!artifact.exists} onClick={() => vscode.postMessage({ type: 'linkArtifactContext', artifact, note: `I have a concern about ${artifact.path}. Please inspect it carefully before using it as evidence.` })}>Record Concern</button>}
          </div>
        </div>
      ))}
    </div>
  );
}

function PreviewPanel({ preview }) {
  if (!preview) {
    return <aside className="preview-panel"><h2>Preview</h2><p className="subtle">Select an artifact to preview it here.</p></aside>;
  }
  return (
    <aside className="preview-panel">
      <div className="preview-head">
        <div>
          <h2>{preview.title}</h2>
          <p>{preview.relativePath}</p>
        </div>
        <button onClick={() => vscode.postMessage({ type: 'openArtifact', artifact: preview })}>Open in VS Code</button>
      </div>
      {preview.kind === 'image' ? <img src={preview.uri} alt={preview.title} /> : null}
      {preview.kind === 'pdf' ? <iframe title={preview.title} src={preview.uri} /> : null}
      {preview.content ? <pre>{preview.content}</pre> : null}
      {preview.message ? <p className="subtle">{preview.message}</p> : null}
      {preview.truncated ? <p className="subtle">Preview truncated.</p> : null}
    </aside>
  );
}

function DiagnosticsModal({ state }) {
  const settings = state.settings || {};
  const commands = state.diagnostics?.commands || [];
  return (
    <div className="modal-backdrop">
      <section className="modal">
        <div className="modal-head">
          <h2>Diagnostics</h2>
          <button onClick={() => vscode.postMessage({ type: 'closeSettings' })}>Close</button>
        </div>
        <dl className="definition-list">
          <dt>Workspace root</dt><dd>{settings.workspaceRoot || state.root}</dd>
          <dt>CLI path</dt><dd>{settings.cliPath || 'msc'}</dd>
          <dt>Campaign DB</dt><dd>{settings.localDbPath || '-'}</dd>
          <dt>Bundle exports</dt><dd>{settings.bundleExportRoot || '-'}</dd>
          <dt>OpenRouter</dt><dd>{settings.openRouterConfigured ? 'configured' : 'missing'}</dd>
          <dt>Default budget</dt><dd>{settings.defaultBudget}</dd>
          <dt>Default tier</dt><dd>{settings.defaultTier}</dd>
          <dt>Default output</dt><dd>{settings.defaultOutput}</dd>
        </dl>
        <h3>Budget</h3>
        <pre>{settings.budgetSummary || 'No budget summary available.'}</pre>
        <h3>Config</h3>
        <pre>{settings.configList || 'No config output available.'}</pre>
        <h3>Diagnostics</h3>
        <p className="subtle">{commands.length} CLI commands detected.</p>
      </section>
    </div>
  );
}

function NewCampaignModal({ onClose }) {
  const [draft, setDraft] = useState({
    title: '',
    objective: '',
    budgetCap: 20,
    tier: 'budget',
    outputFormat: 'markdown',
    template: 'consortium_scaffold'
  });

  function update(key, value) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function submit(event) {
    event.preventDefault();
    vscode.postMessage({ type: 'createCampaign', draft });
  }

  return (
    <div className="modal-backdrop">
      <form className="modal" onSubmit={submit}>
        <div className="modal-head">
          <h2>New Campaign</h2>
          <button type="button" onClick={onClose}>Cancel</button>
        </div>
        <label>Title<input value={draft.title} onChange={(event) => update('title', event.target.value)} required /></label>
        <label>Research objective<textarea value={draft.objective} onChange={(event) => update('objective', event.target.value)} required /></label>
        <div className="form-grid">
          <label>Budget cap<input type="number" min="1" value={draft.budgetCap} onChange={(event) => update('budgetCap', event.target.value)} /></label>
          <label>Tier<select value={draft.tier} onChange={(event) => update('tier', event.target.value)}>
            {['live-smoke', 'budget', 'light', 'medium', 'pro', 'max', 'ultra'].map((tier) => <option key={tier} value={tier}>{tier}</option>)}
          </select></label>
          <label>Output<select value={draft.outputFormat} onChange={(event) => update('outputFormat', event.target.value)}>
            <option value="markdown">markdown</option>
            <option value="latex">latex</option>
          </select></label>
          <label>Template<select value={draft.template} onChange={(event) => update('template', event.target.value)}>
            <option value="consortium_scaffold">consortium scaffold</option>
            <option value="consortium_budget">consortium budget</option>
            <option value="literature_only">literature only</option>
            <option value="experiment_design">experiment design</option>
            <option value="blank">blank</option>
          </select></label>
        </div>
        <button className="primary" type="submit">Create Draft</button>
      </form>
    </div>
  );
}

function LoadingNotice({ text = 'Loading local campaign state...' }) {
  return (
    <div className="loading-bar" role="status">
      <span className="spinner" />
      <span>{text}</span>
    </div>
  );
}

function DashboardLoadingState() {
  return (
    <section className="dashboard-loading" aria-label="Loading dashboard">
      <div className="metric skeleton-card">
        <span className="skeleton-line short" />
        <strong className="skeleton-line medium" />
        <small className="skeleton-line tiny" />
      </div>
      <div className="metric skeleton-card">
        <span className="skeleton-line short" />
        <strong className="skeleton-line medium" />
        <small className="skeleton-line tiny" />
      </div>
      <div className="metric skeleton-card">
        <span className="skeleton-line short" />
        <strong className="skeleton-line medium" />
        <small className="skeleton-line tiny" />
      </div>
      <div className="empty-panel loading-empty">
        <h2>Loading campaigns</h2>
        <p>Reading local campaign specs, deliverables, budget posture, and diagnostics.</p>
      </div>
    </section>
  );
}

function ErrorSummary({ errors }) {
  const visible = (errors || []).filter(Boolean);
  if (!visible.length) return null;
  return (
    <details className="error-summary">
      <summary>{visible.length} dashboard command {visible.length === 1 ? 'issue' : 'issues'}</summary>
      <div className="error-list">
        {visible.map((error, index) => (
          <div key={`${error.command || 'error'}-${index}`} className="error-row">
            <strong>{error.command || 'Dashboard command'}</strong>
            <span>{error.message || error.error || 'Command failed'}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

function Metric({ label, value, detail }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function Empty({ title, detail }) {
  return <div className="empty-panel"><h2>{title}</h2><p>{detail}</p></div>;
}

function LogList({ logs }) {
  if (!logs.length) return <p className="subtle">No logs reported.</p>;
  return <div className="log-list">{logs.map((log, index) => <code key={index}>{String(log)}</code>)}</div>;
}

function graphNodesFromState(state) {
  const graph = state.campaignGraph || {};
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  if (nodes.length) {
    return nodes.map((node) => ({
      id: node.id || node.stage_id || node.name,
      label: node.label || node.title || node.name || node.id || node.stage_id,
      kind: node.metadata?.kind || node.type,
      purpose: node.metadata?.purpose || '',
      tools: node.metadata?.toolFamilies || [],
      validators: node.metadata?.validators || [],
      pausePolicy: node.metadata?.humanPausePolicy || [],
      routes: node.metadata?.allowedRoutes || [],
      order: Number(node.metadata?.order || 0),
      status: node.status || 'unknown',
      workspace: node.workspace,
      budget: node.budget || node.budgetPolicy,
      fail_reason: node.fail_reason,
      logs: node.logs || [],
      artifactCount: node.artifact_count || artifactCountForNode(node)
    }));
  }
  return (state.campaignDetails?.stages || []).map((stage) => ({
    id: stage.stage_id || stage.id || stage.name,
    label: stage.name || stage.stage_id || stage.id,
    order: Number(stage.order || 0),
    status: stage.status || 'unknown',
    workspace: stage.workspace,
    budget: stage.budget,
    fail_reason: stage.fail_reason,
    logs: stage.logs || [],
    artifactCount: [...(stage.required_artifacts || []), ...(stage.optional_artifacts || [])].length
  }));
}

function graphEdgesFromState(state, nodes) {
  const graph = state.campaignGraph || {};
  const nodeIds = new Set(nodes.map((node) => node.id));
  const explicitEdges = Array.isArray(graph.edges) ? graph.edges : [];
  const edges = explicitEdges
    .map((edge) => ({
      source: edge.source || edge.from,
      target: edge.target || edge.to,
      kind: edge.kind || edge.type || 'stage_order',
      metadata: edge.metadata || {}
    }))
    .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  if (edges.length || nodes.length < 2) return edges;
  return nodes.slice(0, -1).map((node, index) => ({
    source: node.id,
    target: nodes[index + 1].id,
    kind: 'stage_order',
    metadata: { inferred: true }
  }));
}

function layoutGraph(nodes, edges) {
  const width = 230;
  const height = 94;
  const gapX = 54;
  const gapY = 76;
  const marginX = 92;
  const marginY = 42;
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const incoming = new Map(nodes.map((node) => [node.id, []]));
  const outgoing = new Map(nodes.map((node) => [node.id, []]));
  const orders = new Map(nodes.map((node, index) => [node.id, Number(node.order || index + 1)]));
  edges.forEach((edge) => {
    if (!nodeMap.has(edge.source) || !nodeMap.has(edge.target)) return;
    incoming.get(edge.target).push(edge.source);
    outgoing.get(edge.source).push(edge.target);
  });

  const ranks = new Map(nodes.map((node) => [node.id, 0]));
  const orderedNodes = [...nodes].sort((a, b) => {
    const orderDelta = (orders.get(a.id) || 0) - (orders.get(b.id) || 0);
    return orderDelta || String(a.id).localeCompare(String(b.id));
  });

  // Rank top-down using only forward edges. Back edges and loop edges are still
  // drawn, but they should not collapse the readable happy-path layout.
  orderedNodes.forEach((node) => {
    for (const target of outgoing.get(node.id) || []) {
      if ((orders.get(target) || 0) <= (orders.get(node.id) || 0)) continue;
      ranks.set(target, Math.max(ranks.get(target) || 0, (ranks.get(node.id) || 0) + 1));
    }
  });

  const rows = new Map();
  orderedNodes.forEach((node) => {
    const rank = ranks.get(node.id) || 0;
    if (!rows.has(rank)) rows.set(rank, []);
    rows.get(rank).push(node);
  });
  const rowEntries = Array.from(rows.entries()).sort(([a], [b]) => a - b);
  const maxRowWidth = Math.max(
    ...rowEntries.map(([, row]) => row.length * width + Math.max(row.length - 1, 0) * gapX),
    width
  );
  const laidOutNodes = orderedNodes.map((node) => {
    const rank = ranks.get(node.id) || 0;
    const row = rows.get(rank) || [];
    const col = row.findIndex((candidate) => candidate.id === node.id);
    const rowWidth = row.length * width + Math.max(row.length - 1, 0) * gapX;
    const xOffset = marginX + Math.max(0, (maxRowWidth - rowWidth) / 2);
    return {
      ...node,
      x: xOffset + col * (width + gapX),
      y: marginY + rank * (height + gapY),
      rank,
      width,
      height
    };
  });
  const positioned = new Map(laidOutNodes.map((node) => [node.id, node]));
  const maxRank = Math.max(...Array.from(ranks.values()), 0);
  const baseGraphWidth = maxRowWidth + marginX * 2;
  const graphHeight = marginY * 2 + (maxRank + 1) * height + maxRank * gapY;
  let backLaneCount = 0;
  let sideLaneCount = 0;
  const classifiedEdges = edges.map((edge) => {
    const source = positioned.get(edge.source);
    const target = positioned.get(edge.target);
    if (!source || !target) return { edge, routeDirection: 'missing', lane: 0 };
    const sourceRank = source.rank || 0;
    const targetRank = target.rank || 0;
    if (targetRank === sourceRank + 1) {
      return { edge, routeDirection: 'forward', lane: 0 };
    }
    if (targetRank > sourceRank) {
      sideLaneCount += 1;
      return { edge, routeDirection: 'long-forward', lane: sideLaneCount };
    }
    backLaneCount += 1;
    return { edge, routeDirection: targetRank === sourceRank ? 'same-rank' : 'back', lane: backLaneCount };
  });
  const graphWidth = baseGraphWidth + Math.max(sideLaneCount - 1, 0) * 28;
  const laidOutEdges = edges
    .map((edge, index) => {
      const source = positioned.get(edge.source);
      const target = positioned.get(edge.target);
      if (!source || !target) return null;
      const routed = classifiedEdges[index];
      const sourceRank = source.rank || 0;
      const targetRank = target.rank || 0;
      const adjacentForward = targetRank === sourceRank + 1;
      const forward = targetRank > sourceRank;
      const lane = routed.lane || 0;
      const rightLane = baseGraphWidth - 44 + Math.max(lane - 1, 0) * 28;
      const leftLane = 28 + Math.max(lane - 1, 0) * 18;
      const path = adjacentForward
        ? routeAdjacentEdge(source, target)
        : forward
          ? routeSideLaneEdge(source, target, rightLane)
          : targetRank === sourceRank
            ? routeSameRankEdge(source, target, rightLane)
            : routeBackEdge(source, target, leftLane);
      return {
        ...edge,
        edgeId: `${edge.source}-${edge.target}-${edge.kind}-${index}`,
        lane,
        routeDirection: routed.routeDirection,
        path
      };
    })
    .filter(Boolean);
  return {
    nodes: laidOutNodes,
    edges: laidOutEdges,
    width: graphWidth,
    height: graphHeight
  };
}

function routeAdjacentEdge(source, target) {
  const startX = source.x + source.width / 2;
  const startY = source.y + source.height;
  const endX = target.x + target.width / 2;
  const endY = target.y;
  const midY = startY + Math.max(24, (endY - startY) / 2);
  return `M ${startX} ${startY} L ${startX} ${midY} L ${endX} ${midY} L ${endX} ${endY}`;
}

function routeSideLaneEdge(source, target, laneX) {
  const startX = source.x + source.width / 2;
  const startY = source.y + source.height;
  const endX = target.x + target.width / 2;
  const endY = target.y;
  const exitY = startY + 30;
  const enterY = endY - 30;
  return `M ${startX} ${startY} L ${startX} ${exitY} L ${laneX} ${exitY} L ${laneX} ${enterY} L ${endX} ${enterY} L ${endX} ${endY}`;
}

function routeBackEdge(source, target, laneX) {
  const startX = source.x;
  const startY = source.y + source.height / 2;
  const endX = target.x;
  const endY = target.y + target.height / 2;
  const exitX = Math.min(laneX + 12, startX - 28);
  const enterX = Math.min(laneX + 12, endX - 28);
  return `M ${startX} ${startY} L ${exitX} ${startY} L ${laneX} ${startY} L ${laneX} ${endY} L ${enterX} ${endY} L ${endX} ${endY}`;
}

function routeSameRankEdge(source, target, laneX) {
  const sourceOnLeft = source.x < target.x;
  const startX = sourceOnLeft ? source.x + source.width : source.x;
  const startY = source.y + source.height / 2;
  const endX = sourceOnLeft ? target.x : target.x + target.width;
  const endY = target.y + target.height / 2;
  const elbowX = sourceOnLeft ? laneX : Math.min(28, source.x - 36);
  return `M ${startX} ${startY} L ${elbowX} ${startY} L ${elbowX} ${endY} L ${endX} ${endY}`;
}

function wrapLabel(value, limit, maxLines) {
  const words = String(value || '').split(/\s+/).filter(Boolean);
  const lines = [];
  let current = '';
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length > limit && current) {
      lines.push(current);
      current = word;
    } else {
      current = next;
    }
    if (lines.length === maxLines) break;
  }
  if (lines.length < maxLines && current) lines.push(current);
  if (lines.length > maxLines) return lines.slice(0, maxLines);
  if (lines.length === maxLines && words.join(' ').length > lines.join(' ').length) {
    lines[maxLines - 1] = `${lines[maxLines - 1].slice(0, Math.max(0, limit - 3))}...`;
  }
  return lines.length ? lines : [String(value || '')];
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function artifactCountForNode(node) {
  const required = Array.isArray(node.required_artifacts) ? node.required_artifacts.length : 0;
  const optional = Array.isArray(node.optional_artifacts) ? node.optional_artifacts.length : 0;
  if (Array.isArray(node.artifacts)) return node.artifacts.length;
  return required + optional;
}

function statusOf(details) {
  if (typeof details.status === 'string') return details.status;
  if (details.status?.state) return details.status.state;
  if (!details.stages?.length) return 'draft';
  return 'planned';
}

function campaignStarted(state) {
  const execution = state.campaignExecution || {};
  if (execution.status && execution.status !== 'not_started') return true;
  const latest = state.campaignRunSummary?.latestRun;
  return Boolean(latest);
}

function statusClass(status) {
  return String(status || 'unknown').toLowerCase().replace(/[^a-z0-9]+/g, '-');
}

function formatBudget(budget) {
  if (!budget) return '$0';
  if (typeof budget === 'number') return `$${budget}`;
  if (budget.total_usd != null) return `$${Number(budget.total_usd).toFixed(2)}`;
  if (budget.budget_usd != null) return `$${Number(budget.budget_usd).toFixed(2)}`;
  if (budget.cap_usd != null) return `$${Number(budget.cap_usd).toFixed(2)}`;
  if (budget.limit_usd != null) return `$${Number(budget.limit_usd).toFixed(2)}`;
  if (budget.maxUsd != null) return `$${Number(budget.maxUsd).toFixed(2)}`;
  return '$0';
}

function formatRoutes(routes) {
  if (!Array.isArray(routes) || !routes.length) return '-';
  return routes.map((route) => `${route.target || '?'} (${route.kind || 'route'})`).join(', ');
}

function basename(value) {
  return String(value || '').split('/').filter(Boolean).pop() || '';
}

function formatTime(value) {
  if (!value) return '-';
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return String(value);
  return new Date(parsed).toLocaleString();
}

function capitalize(value) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function unique(values) {
  return Array.from(new Set(values));
}

createRoot(document.getElementById('root')).render(<App />);
