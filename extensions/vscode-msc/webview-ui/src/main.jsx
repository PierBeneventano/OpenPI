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
  onboardingOpen: false,
  setupOpen: false,
  setup: null,
  setupOps: { openclaudeInstall: null },
  keyStatus: null,
  openClaude: { status: 'idle', model: 'openai/gpt-5-mini', models: [], transcript: [], actions: [], contextLinks: [] },
  openClaudeContextLinks: []
};

function App() {
  const [state, setState] = useState(emptyState);
  const [newCampaignOpen, setNewCampaignOpen] = useState(false);
  const [deleteCandidate, setDeleteCandidate] = useState(null);
  const [workspaceTab, setWorkspaceTab] = useState('deliverables');
  const [keyOpStatus, setKeyOpStatus] = useState(null);

  useEffect(() => {
    const listener = (event) => {
      if (event.data && event.data.type === 'state') {
        setState({ ...emptyState, ...event.data.state });
      } else if (event.data && event.data.type === 'setApiKeyResult') {
        setKeyOpStatus({
          ok: Boolean(event.data.ok),
          env_var: event.data.env_var,
          message: event.data.ok ? `Saved ${event.data.env_var}.` : (event.data.error || 'Save failed.'),
          timestamp: Date.now()
        });
      } else if (event.data && event.data.type === 'unsetApiKeyResult') {
        setKeyOpStatus({
          ok: Boolean(event.data.ok),
          env_var: event.data.env_var,
          message: event.data.ok ? `Removed ${event.data.env_var}.` : (event.data.error || 'Remove failed.'),
          timestamp: Date.now()
        });
      } else if (event.data && event.data.type === 'deleteCampaignResult') {
        if (event.data.ok && event.data.status === 'completed') {
          const deletedRef = String(event.data.campaign || '');
          const deletedId = String(event.data.result?.campaign_id || '');
          setState((current) => ({
            ...current,
            loading: false,
            view: 'home',
            selectedCampaign: null,
            campaigns: (current.campaigns || []).filter((campaign) => !campaignMatchesAny(campaign, [deletedRef, deletedId])),
            actionError: null
          }));
          setDeleteCandidate(null);
        } else if (event.data.ok === false) {
          setState((current) => ({
            ...current,
            loading: false,
            actionError: event.data.error || 'Campaign could not be deleted.'
          }));
        }
      }
    };
    window.addEventListener('message', listener);
    vscode.postMessage({ type: 'ready' });
    return () => window.removeEventListener('message', listener);
  }, []);

  const onboardingOpen = Boolean(state.onboardingOpen);
  const setupOpen = Boolean(state.setupOpen);
  const keyStatus = state.keyStatus;
  useEffect(() => {
    if ((onboardingOpen || setupOpen) && !keyStatus) {
      vscode.postMessage({ type: 'refreshKeyStatus' });
    }
  }, [onboardingOpen, setupOpen, keyStatus]);

  useEffect(() => {
    if (!state.loaded || setupOpen || onboardingOpen) return;
    // Open the Setup tab automatically when setup-state reports any required
    // item is missing. Guarded by state.loaded so a transient spawn failure
    // (e.g. EAGAIN under HPC node load) doesn't flash the panel open.
    const warnings = state.setup && Array.isArray(state.setup.warnings) ? state.setup.warnings : null;
    if (warnings && warnings.length > 0) {
      vscode.postMessage({ type: 'openSetup' });
    }
  }, [state.loaded, setupOpen, onboardingOpen, state.setup]);

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
        {setupOpen ? <SetupTab state={state} opStatus={keyOpStatus} /> : null}
        {onboardingOpen ? <OnboardingPanel state={state} opStatus={keyOpStatus} /> : null}
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
      {setupOpen ? <SetupTab state={state} opStatus={keyOpStatus} /> : null}
      {onboardingOpen ? <OnboardingPanel state={state} opStatus={keyOpStatus} /> : null}
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
          <SetupHeaderButton state={state} />
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

function campaignMatchesAny(campaign, refs) {
  const targets = new Set((refs || []).filter(Boolean).map(String));
  const values = [campaign.path, campaign.name, campaign.campaign_id, campaign.id, campaign.title].filter(Boolean).map(String);
  return values.some((value) => targets.has(value));
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
  const details = state.campaignDetails || {};
  const title = details.name || details.campaign_id || basename(state.selectedCampaign) || 'Campaign';
  const execution = state.campaignExecution || {};
  const graphNodes = graphNodesFromState(state);
  const currentStageId = execution.current_stage_id || '';
  const currentStage = graphNodes.find((node) => node.id === currentStageId);
  const selectedNodeId = state.selectedGraphNode || '';
  const selectedNode = graphNodes.find((node) => node.id === selectedNodeId) || null;
  // Restart confirmation modal state. `kind` is 'restart-node' or 'restart-campaign'.
  // Lives at the workspace level so the modal can render in front of everything.
  const [restartConfirm, setRestartConfirm] = useState(null);
  const detailTab = ['decisions', 'deliverables', 'feedback', 'diagnostics', 'budget'].includes(tab) ? tab : 'deliverables';
  const previewArtifact = useCallback((artifact, options = {}) => {
    if (options.openDeliverables) {
      setTab('deliverables');
    }
    vscode.postMessage({ type: 'previewArtifact', artifact });
    if (options.scrollToPreview) {
      window.setTimeout(() => {
        document.getElementById('inline-preview')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 80);
    }
  }, [setTab]);

  return (
    <div className="app-shell workspace-shell campaign-shell">
      <header className="workspace-header campaign-header">
        <div className="workspace-title">
          <button onClick={() => vscode.postMessage({ type: 'backToCampaigns' })}>Back</button>
          <div>
            <p className="eyebrow">Campaign</p>
            <h1>{title}</h1>
            <p className="subtle">{details.metadata?.objective || details.path || state.selectedCampaign}</p>
          </div>
        </div>
        <div className="header-actions">
          <span className={`pill status-${statusClass(statusOf(details))}`}>{statusOf(details)}</span>
          <span className={`pill ${currentStage ? `status-${statusClass(currentStage.status || 'running')}` : ''}`}>
            {currentStage ? `Active: ${currentStage.label || currentStage.id}` : 'No active stage'}
          </span>
          <button onClick={() => vscode.postMessage({ type: 'refreshCampaign' })}>Refresh</button>
          <button
            className="restart-campaign-btn"
            onClick={() => setRestartConfirm({ kind: 'restart-campaign', campaignName: title })}
          >Restart campaign</button>
          <button className="danger" onClick={() => requestDelete(deleteTargetForCampaign({ path: state.selectedCampaign, title }))}>Delete</button>
        </div>
      </header>

      {state.loading ? <LoadingNotice /> : null}
      {state.actionError ? <div className="notice error">{state.actionError}</div> : null}
      <ErrorSummary errors={state.errors || []} />

      <main className="campaign-layout">
        <section className="campaign-main">
          <GraphTab
            state={state}
            currentStageId={currentStageId}
            selectedNodeId={selectedNodeId}
            onPreviewArtifact={(artifact) => previewArtifact(artifact, { openDeliverables: false, scrollToPreview: true })}
          />
          <NodeDetailPanel
            state={state}
            node={selectedNode}
            isActive={Boolean(selectedNode && selectedNode.id === currentStageId)}
            tab={detailTab}
            setTab={setTab}
            onPreviewArtifact={previewArtifact}
            onRequestRestart={(node) => setRestartConfirm({
              kind: 'restart-node',
              nodeId: node.id,
              nodeLabel: node.label || node.id,
            })}
          />
        </section>
        <aside className="campaign-chat-column">
          <OverseerChat state={state} selectedNode={selectedNode} currentStage={currentStage} />
        </aside>
      </main>
      {restartConfirm ? (
        <RestartConfirmModal
          confirm={restartConfirm}
          onClose={() => setRestartConfirm(null)}
        />
      ) : null}
    </div>
  );
}

function OverseerChat({ state, selectedNode, currentStage }) {
  const openClaude = state.openClaude || emptyState.openClaude;
  return (
    <section className="overseer-chat" aria-label="Overseer chat">
      <div className="overseer-head">
        <div>
          <p className="eyebrow">Overseer</p>
          <h2>Steer the campaign</h2>
          <p className="subtle">
            {currentStage
              ? `Active stage: ${currentStage.label || currentStage.id}`
              : 'No stage is currently running.'}
            {selectedNode && (!currentStage || selectedNode.id !== currentStage.id)
              ? ` · Selected: ${selectedNode.label || selectedNode.id}`
              : ''}
          </p>
        </div>
        <div className="overseer-head-actions">
          <span className={`pill status-${statusClass(openClaude.status || 'idle')}`}>{openClaude.status || 'idle'}</span>
          <button onClick={() => vscode.postMessage({ type: 'openClaudeStart', model: openClaude.model })}>Refresh</button>
          <button onClick={() => vscode.postMessage({ type: 'openClaudeClearHistory' })}>Clear</button>
          <button className="danger" onClick={() => vscode.postMessage({ type: 'openClaudeStop' })}>Stop</button>
        </div>
      </div>
      <ModelSelector openClaude={openClaude} />
      <OpenClaudeChat state={state} selectedNode={selectedNode} />
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

function OpenClaudeChat({ state, selectedNode }) {
  const [text, setText] = useState('');
  const chatEndRef = useRef(null);
  const openClaude = state.openClaude || emptyState.openClaude;
  const transcript = openClaude.transcript || [];
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ block: 'end' });
  }, [transcript.length, transcript[transcript.length - 1]?.text, openClaude.status]);
  const nodeLabel = selectedNode ? (selectedNode.label || selectedNode.id) : '';
  const suggestions = nodeLabel
    ? [
        `Rerun "${nodeLabel}" with the latest feedback.`,
        `Compare the last two iterations of "${nodeLabel}".`,
        `Spawn a debug agent to investigate "${nodeLabel}".`,
        `Summarize what "${nodeLabel}" produced and flag weaknesses.`
      ]
    : [
        'Continue the campaign and tell me what you changed.',
        'Review the latest deliverables and flag weaknesses.',
        'Rerun the current stage using my feedback.',
        'Spawn a debug agent to investigate the most recent failure.'
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
            <button
              type="button"
              className="link"
              onClick={() => vscode.postMessage({ type: 'openClaudeCancel' })}
            >
              Cancel
            </button>
          </article>
        ) : null}
        <div ref={chatEndRef} />
      </div>
      <form className="chat-composer" onSubmit={(event) => {
        event.preventDefault();
        send();
      }}>
        <textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="Tell OpenClaude what you want to inspect, change, rerun, or improve..." />
        {openClaude.status === 'responding' ? (
          <button
            type="button"
            className="secondary"
            onClick={() => vscode.postMessage({ type: 'openClaudeCancel' })}
          >
            Cancel OpenClaude
          </button>
        ) : (
          <button className="primary" type="submit" disabled={!text.trim()}>
            Send to OpenClaude
          </button>
        )}
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
    tier: 'standard',
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
            <option value="scaffold">Scaffold</option>
            <option value="lean">Lean</option>
            <option value="standard">Standard</option>
            <option value="serious">Serious</option>
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

function GraphTab({ state, currentStageId, selectedNodeId }) {
  const graphNodes = graphNodesFromState(state);
  const graphEdges = graphEdgesFromState(state, graphNodes);
  const activeId = currentStageId || '';
  const selectedId = selectedNodeId || '';

  return (
    <section className="graph-board">
      {graphNodes.length ? (
        <InteractiveGraph nodes={graphNodes} edges={graphEdges} selectedNodeId={selectedId} activeNodeId={activeId} />
      ) : (
        <Empty title="No graph yet" detail="Draft campaigns show their graph after stages are planned." />
      )}
    </section>
  );
}

function InteractiveGraph({ nodes, edges, selectedNodeId, activeNodeId }) {
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
              <GraphSvgNode
                key={node.id}
                node={node}
                selected={selectedNodeId === node.id}
                active={activeNodeId === node.id}
              />
            ))}
          </g>
        </g>
      </svg>
    </div>
  );
}

function GraphSvgNode({ node, selected, active }) {
  const titleLines = wrapLabel(node.label || node.id, 24, 2);
  const classes = [
    'graph-svg-node',
    `status-${statusClass(node.status)}`,
    selected ? 'selected' : '',
    active ? 'active' : ''
  ].filter(Boolean).join(' ');
  return (
    <g
      className={classes}
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
      {active ? <rect className="active-ring" x="-4" y="-4" width={node.width + 8} height={node.height + 8} rx="9" /> : null}
      <rect width={node.width} height={node.height} rx="6" />
      <circle cx="16" cy="18" r="5" />
      <text x="28" y="21" className="node-kind">{node.kind || 'node'}</text>
      <circle className="node-port node-port-in" cx={node.width / 2} cy="0" r="4" />
      <circle className="node-port node-port-out" cx={node.width / 2} cy={node.height} r="4" />
      <circle className="node-port node-port-side" cx="0" cy={node.height / 2} r="4" />
      {titleLines.map((line, index) => (
        <text key={`${line}-${index}`} x="14" y={46 + index * 17} className="node-title">{line}</text>
      ))}
      <text x="14" y={node.height - 15} className="node-meta">{active ? 'running · ' : ''}{node.status || 'unknown'} | {node.artifactCount || 0} outputs</text>
    </g>
  );
}

function NodeDetailPanel({ state, node, isActive, tab, setTab, onPreviewArtifact, onRequestRestart }) {
  if (!node) {
    return (
      <section className="node-detail empty">
        <p className="eyebrow">Stage Detail</p>
        <h2>Click a node on the graph</h2>
        <p className="subtle">
          Select any node in the campaign graph to see its latest artifact rendered inline, its metadata,
          and the history of runs that led up to it.
        </p>
        <DetailInspectorTabs state={state} tab={tab} setTab={setTab} stageId="" onPreviewArtifact={onPreviewArtifact} />
      </section>
    );
  }

  const allArtifacts = state.campaignArtifacts || [];
  const stageArtifacts = allArtifacts.filter((artifact) => artifact.stage_id === node.id);
  const headline = pickHeadlineArtifact(stageArtifacts);
  const isHeadlineActivePreview = state.artifactPreview && headline && (state.artifactPreview.id === headline.id || state.artifactPreview.path === headline.path);
  const history = runHistoryForNode(state.campaignEvents || [], stageArtifacts, node.id);

  function previewHeadline() {
    if (!headline) return;
    onPreviewArtifact(headline, { scrollToPreview: false });
  }

  return (
    <section className="node-detail">
      <header className="node-detail-head">
        <div>
          <p className="eyebrow">Stage Detail</p>
          <h2>
            {node.label || node.id}
            {isActive ? <span className="pill running-pill">Running</span> : null}
            <span className={`pill status-${statusClass(node.status)}`}>{node.status || 'unknown'}</span>
          </h2>
          {node.purpose ? <p className="subtle">{node.purpose}</p> : null}
        </div>
        <div className="node-detail-actions">
          {headline && headline.exists ? (
            <button onClick={previewHeadline}>Show latest artifact</button>
          ) : null}
          <button
            title="Open the most recently modified file under this stage's workspace"
            onClick={() => vscode.postMessage({ type: 'openStageNewestFile', stageId: node.id })}
          >
            Open newest file
          </button>
          <button
            title="Reveal this stage's workspace directory in your file explorer"
            onClick={() => vscode.postMessage({ type: 'revealStageWorkspace', stageId: node.id })}
          >
            Reveal in finder
          </button>
          <button onClick={() => vscode.postMessage({ type: 'openClaudeSend', text: `Summarize what stage "${node.label || node.id}" produced and flag what is weak or missing.` })}>Ask Overseer</button>
          {/* Restart this node. Disabled when the node hasn't run yet — there's
              nothing meaningful to restart from a pending/planned state. */}
          <button
            className="restart-node-btn"
            disabled={['pending', 'planned', 'unknown'].includes(node.status)}
            title={['pending', 'planned', 'unknown'].includes(node.status)
              ? 'This node has not run yet; nothing to restart.'
              : 'Cancel any active run + sbatch a fresh orchestrator from this node.'}
            onClick={() => onRequestRestart && onRequestRestart(node)}
          >
            Restart node
          </button>
        </div>
      </header>

      <div className="node-detail-headline">
        {headline ? (
          <>
            <p className="eyebrow">
              {headline.exists
                ? (isActive ? 'Latest output so far' : 'Final artifact')
                : (isActive ? 'Planned output (stage running)' : 'Planned output (not yet produced)')}
            </p>
            {isHeadlineActivePreview && state.artifactPreview ? (
              <InlinePreview preview={state.artifactPreview} />
            ) : (
              <div className="inline-preview headline-placeholder" id="inline-preview">
                <PreviewHead preview={{
                  title: headline.label || basename(headline.path) || 'Artifact',
                  relativePath: headline.path
                }} />
                {headline.exists ? (
                  <button className="primary" onClick={previewHeadline}>Render preview</button>
                ) : (
                  <p className="subtle">
                    This artifact has not been written to disk yet. Use <strong>Open newest file</strong> above
                    to see whatever this stage has written so far, or wait for the stage to complete.
                  </p>
                )}
              </div>
            )}
          </>
        ) : (
          <div className="inline-preview empty" id="inline-preview">
            <p className="eyebrow">{isActive ? 'Stage is running' : 'No artifacts yet'}</p>
            <p className="subtle">
              {isActive
                ? 'No artifact has been produced for this stage yet. Watch the chat for progress.'
                : 'No artifact has been produced for this stage.'}
            </p>
          </div>
        )}
      </div>

      {node.id === 'persona_council' ? (
        <PersonaCouncilAttemptsFeed events={state.campaignEvents || []} />
      ) : null}

      <StageHistoryDrawer history={history} headline={headline} onPreviewArtifact={onPreviewArtifact} />

      <details className="node-detail-meta">
        <summary>Stage metadata</summary>
        <dl className="definition-list">
          <dt>Kind</dt><dd>{node.kind || '-'}</dd>
          <dt>Status</dt><dd>{node.status || 'unknown'}</dd>
          <dt>Workspace</dt><dd>{node.workspace || '-'}</dd>
          <dt>Budget</dt><dd>{formatBudget(node.budget)}</dd>
          <dt>Council</dt><dd>{formatCouncil(node.councilPolicy)}</dd>
          <dt>Tier</dt><dd>{node.tierPolicy?.id || node.tierPolicy?.label || '-'}</dd>
          <dt>Duality</dt><dd>{node.requiresDualityPass ? 'required before this stage' : node.dualityRequired ? 'gate' : '-'}</dd>
          <dt>Tools</dt><dd>{(node.tools || []).join(', ') || '-'}</dd>
          <dt>Validators</dt><dd>{(node.validators || []).join(', ') || '-'}</dd>
          <dt>Pause</dt><dd>{(node.pausePolicy || []).join(', ') || '-'}</dd>
          <dt>Router</dt><dd>{formatRouter(node.routerSpec)}</dd>
          <dt>Retry Caps</dt><dd>{formatRetryCaps(node.routerSpec)}</dd>
          <dt>Subgraph</dt><dd>{formatSubgraph(node)}</dd>
          <dt>State Reads</dt><dd>{(node.stateReads || []).join(', ') || '-'}</dd>
          <dt>State Writes</dt><dd>{(node.stateWrites || []).join(', ') || '-'}</dd>
          <dt>Routes</dt><dd>{formatRoutes(node.routes)}</dd>
          <dt>Failure</dt><dd>{node.fail_reason || '-'}</dd>
        </dl>
      </details>

      <details className="node-detail-outputs" open>
        <summary>All outputs for this stage ({stageArtifacts.length})</summary>
        <ArtifactRows artifacts={stageArtifacts} onPreview={(artifact) => onPreviewArtifact(artifact, { scrollToPreview: true })} />
      </details>

      <DetailInspectorTabs state={state} tab={tab} setTab={setTab} stageId={node.id} onPreviewArtifact={onPreviewArtifact} />
    </section>
  );
}

function DetailInspectorTabs({ state, tab, setTab, stageId, onPreviewArtifact }) {
  return (
    <div className="inspector-stack node-detail-inspector">
      <div className="inspector-tabs">
        {['deliverables', 'decisions', 'feedback', 'budget', 'diagnostics'].map((name) => (
          <button key={name} className={tab === name ? 'active' : ''} onClick={() => setTab(name)}>
            {capitalize(name)}
          </button>
        ))}
      </div>
      <div className="inspector-body">
        {tab === 'deliverables' ? <DeliverablesTab state={state} stageId={stageId} onPreviewArtifact={(artifact) => onPreviewArtifact(artifact, { scrollToPreview: true })} /> : null}
        {tab === 'decisions' ? <DecisionsTab state={state} stageId={stageId} /> : null}
        {tab === 'feedback' ? <FeedbackTab state={state} stageId={stageId} /> : null}
        {tab === 'budget' ? <BudgetTab state={state} /> : null}
        {tab === 'diagnostics' ? <DiagnosticsTab state={state} stageId={stageId} /> : null}
      </div>
    </div>
  );
}

function BudgetTab({ state }) {
  const budget = state.campaignBudget || {};
  const runtime = budget.runtime || {};
  const cap = Number(budget.limit_usd || 0);
  const spent = Number(runtime.total_usd || 0);
  const pct = cap > 0 ? Math.min(100, Math.round((spent / cap) * 100)) : 0;
  const byModel = runtime.by_model || {};
  const uncovered = state.campaignUncoveredModels || [];
  const [newCap, setNewCap] = useState(cap || '');
  const [pricingOpen, setPricingOpen] = useState(false);
  useEffect(() => { setNewCap(cap || ''); }, [cap]);

  function setCap() {
    const value = Number(newCap);
    if (!Number.isFinite(value) || value <= 0) return;
    vscode.postMessage({ type: 'updateBudgetCap', newCap: value });
  }

  return (
    <main className="panel budget-tab">
      <header className="budget-head">
        <div>
          <p className="eyebrow">Budget</p>
          <h2>${spent.toFixed(4)} / ${cap.toFixed(2)} ({pct}%)</h2>
          <p className="subtle">
            {pct >= 100 ? 'Cap reached — runner will halt.' :
             pct >= 95  ? 'Approaching cap (95%+).' :
             pct >= 85  ? 'Above 85% — consider raising cap.' :
                          'Healthy.'}
          </p>
        </div>
        <div className="budget-head-actions">
          <input type="number" min="1" step="0.5" value={newCap}
                 onChange={(event) => setNewCap(event.target.value)}
                 style={{width: '7em'}}
                 placeholder="new cap"/>
          <button onClick={setCap}>Update cap</button>
          <button onClick={() => setPricingOpen(true)}>Pricing…</button>
        </div>
      </header>
      <div className="budget-bar">
        <div className="budget-bar-fill" style={{ width: `${pct}%`,
              background: pct >= 95 ? 'var(--bad)' : pct >= 85 ? 'var(--warn)' : 'var(--accent)' }} />
      </div>
      <h3>Per-model spend</h3>
      {Object.keys(byModel).length ? (
        <table className="budget-table">
          <thead><tr><th>Model</th><th style={{textAlign: 'right'}}>USD</th></tr></thead>
          <tbody>
            {Object.entries(byModel).sort((a, b) => b[1] - a[1]).map(([model, usd]) => (
              <tr key={model}><td>{model}</td><td style={{textAlign: 'right'}}>${Number(usd).toFixed(4)}</td></tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="subtle">No spend recorded yet.</p>
      )}
      <h3>Uncovered models</h3>
      {uncovered.length ? (
        <div className="uncovered-list">
          {uncovered.map((entry) => (
            <UncoveredModelRow key={entry.model_id} entry={entry} />
          ))}
        </div>
      ) : (
        <p className="subtle">All models used so far have pricing entries.</p>
      )}
      {pricingOpen ? <PricingModal onClose={() => setPricingOpen(false)} state={state} /> : null}
    </main>
  );
}

function UncoveredModelRow({ entry }) {
  const suggestion = entry.suggestion || {};
  function dispose(action, extra = {}) {
    vscode.postMessage({ type: 'setPricingDisposition',
                         modelId: entry.model_id, action, ...extra });
  }
  return (
    <div className="uncovered-row">
      <strong>{entry.model_id}</strong>
      {suggestion.sibling ? (
        <span className="subtle">
          suggested rate from {suggestion.sibling}: ${suggestion.input_per_1k}/1k in, ${suggestion.output_per_1k}/1k out
        </span>
      ) : null}
      <div className="uncovered-actions">
        {suggestion.sibling ? <button onClick={() => dispose('use_suggested')}>Use suggested</button> : null}
        <button onClick={() => dispose('treat_as_zero')}>Allow $0</button>
        <button onClick={() => dispose('skip_model')}>Skip model</button>
      </div>
    </div>
  );
}

function PricingModal({ onClose, state }) {
  const [pricing, setPricing] = useState(null);
  const [draft, setDraft] = useState({ model: '', input: '', output: '' });
  useEffect(() => {
    vscode.postMessage({ type: 'pricingList' });
    function listener(event) {
      if (event.data && event.data.type === 'pricingListResult') {
        setPricing(event.data.pricing || {});
      }
    }
    window.addEventListener('message', listener);
    return () => window.removeEventListener('message', listener);
  }, []);

  function save() {
    if (!draft.model || !draft.input || !draft.output) return;
    vscode.postMessage({ type: 'pricingSet', model: draft.model,
                         inputPer1k: Number(draft.input),
                         outputPer1k: Number(draft.output) });
    setDraft({ model: '', input: '', output: '' });
  }
  function remove(model) {
    vscode.postMessage({ type: 'pricingUnset', model });
  }

  return (
    <div className="modal-backdrop">
      <section className="modal" style={{ maxWidth: '720px' }}>
        <div className="modal-head">
          <h2>Model pricing (.llm_config.yaml)</h2>
          <button onClick={onClose}>Close</button>
        </div>
        {pricing === null ? <p className="subtle">Loading pricing…</p> : (
          <table className="budget-table">
            <thead><tr><th>Model</th><th>in/1k</th><th>out/1k</th><th></th></tr></thead>
            <tbody>
              {Object.entries(pricing).sort().map(([model, rates]) => (
                <tr key={model}>
                  <td>{model}</td>
                  <td>${Number(rates.input_per_1k || 0).toFixed(5)}</td>
                  <td>${Number(rates.output_per_1k || 0).toFixed(5)}</td>
                  <td><button className="danger" onClick={() => remove(model)}>Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <h3>Add / update</h3>
        <div className="form-grid">
          <label>Model id<input value={draft.model}
                                onChange={(e) => setDraft({...draft, model: e.target.value})}
                                placeholder="openrouter/perplexity/sonar-deep-research" /></label>
          <label>Input $/1k<input type="number" step="0.00001" value={draft.input}
                                  onChange={(e) => setDraft({...draft, input: e.target.value})} /></label>
          <label>Output $/1k<input type="number" step="0.00001" value={draft.output}
                                   onChange={(e) => setDraft({...draft, output: e.target.value})} /></label>
        </div>
        <button className="primary" onClick={save}>Save</button>
      </section>
    </div>
  );
}

function StageHistoryDrawer({ history, headline, onPreviewArtifact }) {
  if (!history || history.runs.length < 2) {
    return null;
  }
  return (
    <details className="node-history-drawer">
      <summary>
        Run history · {history.runs.length} run{history.runs.length === 1 ? '' : 's'}
        {history.fallback ? ' (reconstructed from events)' : ''}
      </summary>
      <ol className="run-history-list">
        {history.runs.map((run, index) => {
          const isLatest = index === 0;
          return (
            <li key={run.run_id || `run-${index}`} className={`run-history-item ${isLatest ? 'latest' : ''}`}>
              <div className="run-history-head">
                <strong>{isLatest ? 'Latest' : `Run -${index}`}</strong>
                <span className="subtle">{formatTime(run.ended_at) || formatTime(run.started_at) || '—'}</span>
                <span className={`pill status-${statusClass(run.status || 'completed')}`}>{run.status || 'completed'}</span>
              </div>
              {run.reason ? <p className="subtle">{run.reason}</p> : null}
              {run.artifacts && run.artifacts.length ? (
                <ul className="run-history-artifacts">
                  {run.artifacts.map((artifact) => {
                    const isHeadline = headline && (artifact.id === headline.id || artifact.path === headline.path);
                    return (
                      <li key={artifact.id || artifact.path}>
                        <span>{artifact.label || basename(artifact.path) || 'artifact'}</span>
                        <span className="subtle">{artifact.path}</span>
                        {isHeadline ? <span className="pill">featured above</span> : null}
                        <button
                          disabled={!artifact.exists}
                          onClick={() => onPreviewArtifact(artifact, { scrollToPreview: true })}
                        >
                          Preview
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="subtle">No artifacts recorded for this run.</p>
              )}
            </li>
          );
        })}
      </ol>
    </details>
  );
}

function PersonaCouncilAttemptsFeed({ events }) {
  const attempts = (events || [])
    .filter((event) => event && event.type === 'PersonaCouncilAttempt')
    .map((event) => ({
      attempt: event.payload?.attempt,
      maxAttempts: event.payload?.max_attempts,
      verdicts: event.payload?.verdicts || {},
      acceptCount: event.payload?.accept_count,
      rejectCount: event.payload?.reject_count,
      proposalPreview: event.payload?.proposal_preview || '',
      ts: event.created_at
    }))
    .sort((a, b) => String(a.ts || '').localeCompare(String(b.ts || '')));
  if (!attempts.length) {
    return (
      <details className="council-feed">
        <summary>Persona council attempts (none yet)</summary>
        <p className="subtle">
          Once the council runs, each synthesize-vote attempt appears here with
          live verdicts and the latest proposal preview.
        </p>
      </details>
    );
  }
  const latest = attempts[attempts.length - 1];
  return (
    <details className="council-feed" open>
      <summary>
        Persona council attempts · {attempts.length}
        {latest.maxAttempts ? ` of ${latest.maxAttempts}` : ''}
        {latest.acceptCount >= 2 ? ' (CONSENSUS)' : ''}
      </summary>
      <ol className="council-attempts-list">
        {attempts.map((entry, index) => {
          const isLatest = index === attempts.length - 1;
          return (
            <li key={`${entry.ts}-${entry.attempt}`} className={`council-attempt-row ${isLatest ? 'latest' : ''}`}>
              <div className="council-attempt-head">
                <strong>Attempt {entry.attempt}</strong>
                <span className="subtle">{formatTime(entry.ts)}</span>
                <span className={`pill ${entry.acceptCount >= 2 ? 'status-completed' : entry.rejectCount >= 2 ? 'status-failed' : ''}`}>
                  {entry.acceptCount} accept / {entry.rejectCount} reject
                </span>
              </div>
              <ul className="council-verdicts">
                {Object.entries(entry.verdicts).map(([persona, verdict]) => (
                  <li key={persona}>
                    <span className={`pill status-${verdict === 'ACCEPT' ? 'completed' : verdict === 'REJECT' ? 'failed' : ''}`}>
                      {verdict}
                    </span>
                    <span>{persona}</span>
                  </li>
                ))}
              </ul>
              {isLatest && entry.proposalPreview ? (
                <div className="council-proposal-preview">
                  <p className="eyebrow">Latest proposal preview (first 400 chars)</p>
                  <pre>{entry.proposalPreview}</pre>
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
    </details>
  );
}

function pickHeadlineArtifact(stageArtifacts) {
  if (!stageArtifacts || !stageArtifacts.length) return null;
  const existing = stageArtifacts.filter((artifact) => artifact.exists);
  // If nothing exists yet, return the first declared artifact as a *preview placeholder*
  // (NodeDetailPanel checks `headline.exists` before enabling the Render button).
  const pool = existing.length ? existing : stageArtifacts;
  const byMtime = pool.filter((artifact) => artifact.mtime || artifact.modified_at || artifact.created_at);
  if (byMtime.length) {
    return [...byMtime].sort((a, b) => artifactTimestamp(b) - artifactTimestamp(a))[0];
  }
  const required = pool.find((artifact) => artifact.required);
  return required || pool[pool.length - 1];
}

function artifactTimestamp(artifact) {
  const value = artifact.mtime || artifact.modified_at || artifact.created_at;
  if (!value) return 0;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : 0;
}

function runHistoryForNode(events, stageArtifacts, nodeId) {
  if (!nodeId || !Array.isArray(events) || !events.length) {
    return { runs: stageArtifacts.length ? [{ run_id: 'current', status: 'completed', artifacts: stageArtifacts, ended_at: null, started_at: null, reason: null }] : [], fallback: false };
  }
  const runs = new Map();
  const orderKeys = [];
  function bucketForRun(runId, startedAt) {
    const key = runId || `unknown-${orderKeys.length}`;
    if (!runs.has(key)) {
      runs.set(key, { run_id: runId || '', status: '', started_at: startedAt || null, ended_at: null, reason: null, artifacts: [], event_count: 0 });
      orderKeys.push(key);
    }
    return runs.get(key);
  }
  for (const event of events) {
    if (!event || !event.type) continue;
    const payload = event.payload || event.data || {};
    const eventNode = payload.node_id || payload.stage_id || payload.target_node_id || event.node_id || event.stage_id;
    if (eventNode && String(eventNode) !== String(nodeId)) continue;
    const runId = String(payload.run_id || payload.execution_id || event.run_id || event.execution_id || '');
    if (!runId && !eventNode) continue;
    const bucket = bucketForRun(runId, event.created_at);
    bucket.event_count += 1;
    if (event.type.includes('start') || event.type.includes('queued')) {
      bucket.started_at = bucket.started_at || event.created_at || null;
    }
    if (event.type.includes('complete') || event.type.includes('finish') || event.type.includes('exit') || event.type.includes('fail')) {
      bucket.ended_at = event.created_at || bucket.ended_at;
      if (event.type.includes('fail')) bucket.status = 'failed';
      else if (!bucket.status) bucket.status = 'completed';
    }
    if (payload.reason && !bucket.reason) bucket.reason = String(payload.reason).slice(0, 200);
  }
  const orderedRuns = orderKeys
    .map((key) => runs.get(key))
    .filter((run) => run && run.event_count > 0)
    .sort((a, b) => String(b.ended_at || b.started_at || '').localeCompare(String(a.ended_at || a.started_at || '')));
  if (!orderedRuns.length) {
    if (stageArtifacts.length) {
      return { runs: [{ run_id: 'current', status: 'completed', artifacts: stageArtifacts, ended_at: null, started_at: null, reason: null }], fallback: false };
    }
    return { runs: [], fallback: false };
  }
  if (orderedRuns.length) {
    orderedRuns[0].artifacts = stageArtifacts;
  }
  return { runs: orderedRuns, fallback: true };
}

function DecisionsTab({ state, stageId = '' }) {
  const allDecisions = state.campaignDecisions || [];
  const decisions = stageId
    ? allDecisions.filter((decision) => !decision.target_id || decision.target_id === stageId || decision.target_label === stageId)
    : allDecisions;
  // If the persona_council deadlock blob is `open` (or `accepted_as_is`/`edited`
  // but not yet consumed by a fresh run), render the specialized banner with
  // per-persona rationales + edit/accept actions. This replaces the generic
  // decision card for that specific decision; other decisions render as today.
  const deadlock = (state.campaignMetadata || {}).persona_council_deadlock || null;
  const hasOpenDeadlock = deadlock && deadlock.status === 'open';
  const stageMatch = !stageId || stageId === 'persona_council';
  const showBanner = hasOpenDeadlock && stageMatch;
  // Hide the generic "graph_change for persona_council" decision when the
  // banner is rendering — the banner replaces it.
  const filteredDecisions = showBanner
    ? decisions.filter((d) => d.target_id !== 'persona_council')
    : decisions;

  if (!showBanner && !filteredDecisions.length) {
    return (
      <main className="panel">
        <p className="eyebrow">Human decisions</p>
        <h2>{stageId ? `No decisions for ${stageId}` : 'No Decisions Pending'}</h2>
        <p className="subtle">When the graph reaches a review point, the decision, evidence, and safe actions will appear here.</p>
      </main>
    );
  }
  return (
    <main className="panel">
      <p className="eyebrow">Human decisions</p>
      <h2>{stageId ? `Decisions for ${stageId}` : 'Pending Decisions'}</h2>
      {showBanner ? <PersonaCouncilDeadlockBanner deadlock={deadlock} /> : null}
      {filteredDecisions.length ? (
        <div className="feedback-list">
          {filteredDecisions.map((decision) => (
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
      ) : null}
    </main>
  );
}

function PersonaCouncilDeadlockBanner({ deadlock }) {
  const [mode, setMode] = useState('summary');  // 'summary' | 'editing' | 'confirm-accept'
  const [reason, setReason] = useState('');
  const verdicts = deadlock.verdicts || {};
  const rationales = deadlock.rationales || {};
  const proposalPath = deadlock.proposal_path || '';

  function startEdit() {
    if (proposalPath) {
      vscode.postMessage({
        type: 'openArtifact',
        artifact: { path: proposalPath },
      });
    }
    setMode('editing');
  }

  function applyEdited() {
    vscode.postMessage({
      type: 'resumeFromCouncilDeadlock',
      mode: 'edited',
      reason: reason.trim() || 'human edited proposal',
    });
  }

  function acceptAsIs() {
    vscode.postMessage({
      type: 'resumeFromCouncilDeadlock',
      mode: 'accept_as_is',
      reason: reason.trim() || 'human accepted draft as-is',
    });
  }

  return (
    <section className="deadlock-banner">
      <header className="deadlock-banner-head">
        <p className="eyebrow">Persona council didn't converge</p>
        <h3>
          Council exhausted {deadlock.attempts || '?'} attempts without consensus.
        </h3>
        <p className="subtle">
          The personas could not agree on the research plan. Read each persona's
          rationale below, then either edit the latest draft yourself or accept
          it as-is to advance the pipeline.
        </p>
      </header>

      <div className="deadlock-rationales">
        {Object.keys(verdicts).map((persona) => (
          <details key={persona} className={`persona-rationale verdict-${(verdicts[persona] || 'unknown').toLowerCase()}`}>
            <summary>
              <strong>{persona}</strong>
              <span className={`pill status-${(verdicts[persona] || '').toLowerCase() === 'accept' ? 'completed' : (verdicts[persona] || '').toLowerCase() === 'reject' ? 'failed' : ''}`}>
                {verdicts[persona] || 'UNKNOWN'}
              </span>
            </summary>
            <pre className="persona-rationale-text">{rationales[persona] || '(no rationale captured)'}</pre>
          </details>
        ))}
      </div>

      <div className="deadlock-proposal">
        <p className="eyebrow">Latest proposal draft</p>
        <p className="subtle">
          <code>{proposalPath}</code>
        </p>
      </div>

      {mode === 'editing' ? (
        <div className="deadlock-editing-note">
          <p>
            <strong>Editing.</strong> A VSCode tab should have opened with{' '}
            <code>research_proposal.md</code>. Edit the file, save (Ctrl+S), then{' '}
            click <em>Apply edits</em> below.
          </p>
          <label>Reason (optional, for the audit log)
            <input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="e.g. tightened the empirical methodology section"
            />
          </label>
          <div className="deadlock-actions">
            <button type="button" onClick={() => setMode('summary')}>Cancel</button>
            <button type="button" className="primary" onClick={applyEdited}>
              Apply edits and proceed
            </button>
          </div>
        </div>
      ) : mode === 'confirm-accept' ? (
        <div className="deadlock-confirm-accept">
          <p>
            Accept the latest synthesized draft as the council's output and
            advance the pipeline to <code>literature_review_agent</code>?
          </p>
          <label>Reason (optional, for the audit log)
            <input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="e.g. the draft is close enough; let's move on"
            />
          </label>
          <div className="deadlock-actions">
            <button type="button" onClick={() => setMode('summary')}>Cancel</button>
            <button type="button" className="primary" onClick={acceptAsIs}>
              Confirm accept and proceed
            </button>
          </div>
        </div>
      ) : (
        <div className="deadlock-actions">
          <button type="button" className="primary" onClick={startEdit}>
            Edit plan and proceed
          </button>
          <button type="button" onClick={() => setMode('confirm-accept')}>
            Accept as-is and proceed
          </button>
        </div>
      )}
    </section>
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

function FeedbackTab({ state, stageId = '' }) {
  const active = state.activeRun && ['running', 'stopping'].includes(state.activeRun.status);
  const steering = state.steering || {};
  const allFeedback = state.campaignFeedback?.length ? state.campaignFeedback : state.campaignRunSummary?.feedback || [];
  const feedback = stageId ? allFeedback.filter((item) => !item.node_id || item.node_id === stageId) : allFeedback;
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

function DeliverablesTab({ state, stageId = '', onPreviewArtifact }) {
  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [stageFilter, setStageFilter] = useState(stageId || 'all');
  const [existenceFilter, setExistenceFilter] = useState('existing');
  const [audienceFilter, setAudienceFilter] = useState('deliverables');
  useEffect(() => {
    if (stageId) setStageFilter(stageId);
  }, [stageId]);
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
      <section className="panel artifact-library-panel">
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
        <ArtifactRows artifacts={filtered} onPreview={onPreviewArtifact} />
      </section>
      <PreviewPanel preview={state.artifactPreview} />
    </main>
  );
}

function DiagnosticsTab({ state, stageId = '' }) {
  const allEvents = state.campaignEvents || [];
  const events = stageId ? allEvents.filter((event) => {
    const payload = event.payload || event.data || {};
    const eventNode = payload.node_id || payload.stage_id || event.node_id || event.stage_id;
    return !eventNode || String(eventNode) === String(stageId);
  }) : allEvents;
  const allDiagnostics = state.campaignDiagnosticArtifacts || [];
  const diagnosticArtifacts = stageId ? allDiagnostics.filter((artifact) => !artifact.stage_id || artifact.stage_id === stageId) : allDiagnostics;
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

function ArtifactRows({ artifacts, compact = false, onPreview }) {
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
            {compact ? null : <button disabled={!artifact.exists} onClick={() => onPreview ? onPreview(artifact) : vscode.postMessage({ type: 'previewArtifact', artifact })}>Preview</button>}
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
    return <aside id="deliverables-preview" className="preview-panel"><h2>Preview</h2><p className="subtle">Select an artifact to preview it here.</p></aside>;
  }
  return (
    <aside id="deliverables-preview" className="preview-panel">
      <PreviewHead preview={preview} />
      <PreviewBody preview={preview} />
      {preview.message ? <p className="subtle">{preview.message}</p> : null}
      {preview.truncated ? <p className="subtle">Preview truncated.</p> : null}
    </aside>
  );
}

function InlinePreview({ preview, emptyText = 'Select an artifact to preview it here.', emptyTitle = 'Preview' }) {
  if (!preview) {
    return (
      <div className="inline-preview empty" id="inline-preview">
        <h3>{emptyTitle}</h3>
        <p className="subtle">{emptyText}</p>
      </div>
    );
  }
  return (
    <div className="inline-preview" id="inline-preview">
      <PreviewHead preview={preview} />
      <PreviewBody preview={preview} />
      {preview.message ? <p className="subtle">{preview.message}</p> : null}
      {preview.truncated ? <p className="subtle">Preview truncated.</p> : null}
    </div>
  );
}

function PreviewHead({ preview }) {
  return (
    <div className="preview-head">
      <div>
        <h2>{preview.title}</h2>
        <p>{preview.relativePath}</p>
      </div>
      <button onClick={() => vscode.postMessage({ type: 'openArtifact', artifact: preview })}>Open in VS Code</button>
    </div>
  );
}

function PreviewBody({ preview }) {
  return (
    <div className="preview-body">
      {preview.kind === 'image' ? <img src={preview.uri} alt={preview.title} /> : null}
      {preview.kind === 'pdf' ? <iframe title={preview.title} src={preview.uri} /> : null}
      {preview.html ? <div className={`rendered-preview rendered-${preview.kind}`} dangerouslySetInnerHTML={{ __html: preview.html }} /> : null}
      {!preview.html && preview.content ? <pre>{preview.content}</pre> : null}
    </div>
  );
}

function DiagnosticsModal({ state }) {
  const settings = state.settings || {};
  const checks = [
    ['Campaigns', Boolean(state.campaigns)],
    ['Project readiness', Boolean(state.diagnostics?.readiness)],
    ['OpenClaude readiness', Boolean(state.diagnostics?.openclaude)],
    ['OpenClaude models', Boolean(state.diagnostics?.openclaudeModels)]
  ];
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
          <dt>
            OpenRouter
          </dt>
          <dd>
            {settings.openRouterConfigured ? 'configured' : 'missing'}
            {' '}
            <button onClick={() => vscode.postMessage({ type: 'openOnboarding' })}>Configure keys</button>
          </dd>
          <dt>Default budget</dt><dd>{settings.defaultBudget}</dd>
          <dt>Default tier</dt><dd>{settings.defaultTier}</dd>
          <dt>Default output</dt><dd>{settings.defaultOutput}</dd>
        </dl>
        <h3>Dashboard Checks</h3>
        <dl className="definition-list">
          {checks.map(([label, ok]) => (
            <React.Fragment key={label}>
              <dt>{label}</dt><dd>{ok ? 'loaded' : 'not loaded'}</dd>
            </React.Fragment>
          ))}
        </dl>
      </section>
    </div>
  );
}

function SetupHeaderButton({ state }) {
  const warnings = state.setup && Array.isArray(state.setup.warnings) ? state.setup.warnings : null;
  const optionalActions = state.setup && Array.isArray(state.setup.next_actions) ? state.setup.next_actions.length : 0;
  const needsRequired = warnings && warnings.length > 0;
  const label = needsRequired ? `Setup (${warnings.length} required)` : optionalActions ? 'Setup' : 'Setup ✓';
  const className = needsRequired ? 'primary' : '';
  return (
    <button className={className} onClick={() => vscode.postMessage({ type: 'openSetup' })}>{label}</button>
  );
}

function SetupTab({ state, opStatus }) {
  const setup = state.setup || null;
  const setupOps = state.setupOps || { openclaudeInstall: null };
  const keyStatus = state.keyStatus;
  const keys = keyStatus && Array.isArray(keyStatus.keys) ? keyStatus.keys : [];
  const openrouterEntry = keys.find((entry) => entry.env_var === 'OPENROUTER_API_KEY');
  const installOp = setupOps.openclaudeInstall;

  const requiredItems = [
    {
      key: 'openrouter',
      label: 'OpenRouter API key',
      help: 'Required to run any campaign or chat session. Stored locally in ~/.msc/.env (owner-only).',
      ok: Boolean(setup ? setup.openrouter_configured : openrouterEntry?.configured),
      detail: openrouterEntry?.source ? `Source: ${openrouterEntry.source}` : null,
      render: () => (
        openrouterEntry
          ? <KeyRow entry={openrouterEntry} />
          : <p className="subtle">Loading key status…</p>
      )
    },
    {
      key: 'openclaude',
      label: 'OpenClaude binary',
      help: 'Powers the steering chat. Installs @gitlawb/openclaude into the same env as msc.',
      ok: Boolean(setup ? setup.openclaude_available : false),
      detail: setup && setup.openclaude_launch_ready ? 'Launch-ready (skill, key, and binary all present).' : null,
      render: () => <OpenClaudeInstallRow installOp={installOp} setup={setup} />
    }
  ];

  const envItems = [
    { key: 'python', label: 'Python ≥ 3.10', ok: Boolean(setup?.python_ready) },
    { key: 'cli', label: 'msc CLI on PATH', ok: Boolean(setup?.cli_ready) },
    { key: 'sdk', label: 'msc_sdk available', ok: Boolean(setup?.sdk_json_ready) },
    { key: 'vscode', label: 'VS Code extension files present', ok: Boolean(setup?.vscode_extension_ready) }
  ];

  const optionalItems = [
    { key: 'results', label: 'Results directory', ok: Boolean(setup?.results_dir_ready), help: 'Created on first run.' },
    { key: 'slurm', label: 'SLURM (sbatch)', ok: Boolean(setup?.slurm_available), help: 'Needed for cluster jobs.' },
    { key: 'latex', label: 'pdflatex', ok: Boolean(setup?.latex_available), help: 'Needed for PDF deliverables.' },
    { key: 'rg', label: 'ripgrep (rg)', ok: Boolean(setup?.rg_available), help: 'Fast in-repo search.' },
    { key: 'openclaw', label: 'OpenClaw gateway', ok: Boolean(setup?.openclaw_enabled), help: 'Optional autonomous oversight.' },
    { key: 'telegram', label: 'Telegram notifications', ok: Boolean(setup?.telegram_enabled), help: 'Optional notifications.' }
  ];

  const requiredOkCount = requiredItems.filter((row) => row.ok).length;
  const allRequiredOk = requiredOkCount === requiredItems.length;
  const warningsCount = setup && Array.isArray(setup.warnings) ? setup.warnings.length : 0;
  const progressLabel = setup
    ? (allRequiredOk
        ? 'All required items complete.'
        : `${requiredOkCount} of ${requiredItems.length} required items complete${warningsCount ? `, ${warningsCount} warning${warningsCount === 1 ? '' : 's'}` : ''}.`)
    : 'Loading setup state…';

  return (
    <div className="modal-backdrop">
      <section className="modal" style={{ maxWidth: '720px' }}>
        <div className="modal-head">
          <h2>MSc Setup</h2>
          <button onClick={() => vscode.postMessage({ type: 'closeSetup' })}>Close</button>
        </div>
        <p className="subtle">{progressLabel}</p>
        {opStatus ? (
          <div className={`notice ${opStatus.ok ? '' : 'error'}`}>{opStatus.message}</div>
        ) : null}

        <h3>Required</h3>
        {requiredItems.map((row) => (
          <SetupChecklistRow key={row.key} row={row}>
            {!row.ok ? row.render() : null}
          </SetupChecklistRow>
        ))}

        <h3>Environment</h3>
        {envItems.map((row) => <SetupChecklistRow key={row.key} row={row} />)}

        <h3>Optional helpers</h3>
        {optionalItems.map((row) => <SetupChecklistRow key={row.key} row={row} />)}

        {keyStatus && keyStatus.config_path ? (
          <p className="subtle" style={{ marginTop: '0.75rem' }}>Config: {keyStatus.config_path}</p>
        ) : null}

        {allRequiredOk ? (
          <div className="notice" style={{ marginTop: '0.75rem' }}>
            <p style={{ margin: 0 }}>Setup complete. You can start a campaign or open the OpenClaude chat.</p>
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
              <button onClick={() => vscode.postMessage({ type: 'closeSetup' })}>Close</button>
              <button onClick={() => vscode.postMessage({ type: 'refresh' })}>Refresh dashboard</button>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function SetupChecklistRow({ row, children }) {
  return (
    <div className="setup-checklist-row" style={{ borderTop: '1px solid var(--vscode-panel-border)', padding: '0.55rem 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '0.75rem' }}>
        <strong>
          <span style={{ marginRight: '0.4rem' }}>{row.ok ? '✓' : '•'}</span>
          {row.label}
        </strong>
        <span className="subtle">{row.ok ? 'ready' : 'needs setup'}</span>
      </div>
      {row.help ? <p className="subtle" style={{ margin: '0.2rem 0' }}>{row.help}</p> : null}
      {row.detail ? <p className="subtle" style={{ margin: '0.2rem 0' }}>{row.detail}</p> : null}
      {children}
    </div>
  );
}

function OpenClaudeInstallRow({ installOp, setup }) {
  const status = installOp?.status || 'idle';
  const log = installOp && Array.isArray(installOp.log) ? installOp.log : [];
  const tail = log.slice(-30).map((entry) => entry.text).join('');
  const disabled = status === 'running';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginTop: '0.4rem' }}>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button
          className="primary"
          disabled={disabled}
          onClick={() => vscode.postMessage({ type: 'installOpenClaude' })}
        >
          {status === 'running' ? 'Installing…' : 'Install OpenClaude'}
        </button>
        <span className="subtle">Runs `msc openclaude install` (~30–60s on first install).</span>
      </div>
      {status === 'failed' && installOp?.error ? (
        <div className="notice error">{String(installOp.error)}</div>
      ) : null}
      {status === 'completed' && installOp?.path ? (
        <p className="subtle" style={{ margin: 0 }}>
          {installOp.alreadyInstalled ? 'Already installed at ' : 'Installed at '}{installOp.path}
        </p>
      ) : null}
      {tail ? (
        <pre style={{ maxHeight: '120px', overflow: 'auto', margin: 0, padding: '0.4rem', background: 'var(--vscode-textCodeBlock-background)' }}>{tail}</pre>
      ) : null}
      {setup && setup.openclaude_available && !setup.openclaude_launch_ready ? (
        <p className="subtle" style={{ margin: 0 }}>Binary is installed but launch isn't ready yet — usually the OpenRouter key is still missing.</p>
      ) : null}
    </div>
  );
}

function OnboardingPanel({ state, opStatus }) {
  const status = state.keyStatus || null;
  const keys = status && Array.isArray(status.keys) ? status.keys : [];
  const required = keys.filter((entry) => entry.level === 'required');
  const optional = keys.filter((entry) => entry.level !== 'required');
  const allRequiredSet = required.length > 0 && required.every((entry) => entry.configured);
  return (
    <div className="modal-backdrop">
      <section className="modal" style={{ maxWidth: '640px' }}>
        <div className="modal-head">
          <h2>Configure API Keys</h2>
          <button onClick={() => vscode.postMessage({ type: 'closeOnboarding' })}>Close</button>
        </div>
        <p className="subtle">
          One-time setup. Keys are stored in <code>~/.msc/.env</code> with owner-only permissions.
          The same file is used by the <code>msc</code> CLI — both surfaces share one configuration.
        </p>
        <p className="subtle">
          Don't have an OpenRouter key yet? Sign up at{' '}
          <a href="https://openrouter.ai/keys">openrouter.ai/keys</a>.
        </p>
        {opStatus ? (
          <div className={`notice ${opStatus.ok ? '' : 'error'}`}>{opStatus.message}</div>
        ) : null}
        {!status ? (
          <p>Loading key status…</p>
        ) : !status.ok ? (
          <div className="notice error">
            <p>Could not reach the MSc CLI to read key status.</p>
            <p className="subtle" style={{ marginTop: '0.25rem' }}>
              On shared systems this often clears in a moment. If it persists, run{' '}
              <code>msc config keys list</code> in a terminal — it uses the same code path.
            </p>
            <p className="subtle" style={{ marginTop: '0.25rem' }}>Details: {status.error || 'unknown'}</p>
            <button onClick={() => vscode.postMessage({ type: 'refreshKeyStatus' })} style={{ marginTop: '0.4rem' }}>
              Retry
            </button>
          </div>
        ) : (
          <>
            <h3>Required</h3>
            {required.length === 0 ? <p className="subtle">None.</p> : null}
            {required.map((entry) => <KeyRow key={entry.env_var} entry={entry} />)}
            <h3>Optional</h3>
            {optional.length === 0 ? <p className="subtle">None.</p> : null}
            {optional.map((entry) => <KeyRow key={entry.env_var} entry={entry} />)}
            {status.config_path ? <p className="subtle" style={{ marginTop: '0.75rem' }}>Config: {status.config_path}</p> : null}
            {allRequiredSet ? (
              <div className="notice">All required keys set. You can close this panel and continue.</div>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
}

function KeyRow({ entry }) {
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const submit = () => {
    if (!draft.trim() || pending) return;
    setPending(true);
    vscode.postMessage({ type: 'setApiKey', env_var: entry.env_var, value: draft });
    setDraft('');
    setTimeout(() => setPending(false), 1500);
  };
  return (
    <div className="key-row" style={{ borderTop: '1px solid var(--vscode-panel-border)', padding: '0.6rem 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '0.75rem' }}>
        <strong>{entry.name}</strong>
        <span className="subtle">{entry.env_var}</span>
      </div>
      <p className="subtle" style={{ margin: '0.25rem 0' }}>{entry.description || ''}</p>
      {entry.configured ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span>✓ set</span>
          <span className="subtle">source: {entry.source || '—'}</span>
          <button onClick={() => vscode.postMessage({ type: 'unsetApiKey', env_var: entry.env_var })}>Remove</button>
        </div>
      ) : (
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <input
            type="password"
            placeholder={`Paste ${entry.env_var}`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') submit(); }}
            style={{ flex: 1 }}
            autoComplete="off"
            spellCheck={false}
          />
          <button className="primary" disabled={pending || !draft.trim()} onClick={submit}>Save</button>
        </div>
      )}
    </div>
  );
}

function RestartConfirmModal({ confirm, onClose }) {
  const isNode = confirm.kind === 'restart-node';
  const [reason, setReason] = useState('');
  const [archive, setArchive] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  function fire() {
    if (submitting) return;
    setSubmitting(true);
    if (isNode) {
      vscode.postMessage({
        type: 'restartNode',
        nodeId: confirm.nodeId,
        reason: reason.trim() || `restart node ${confirm.nodeLabel}`,
      });
    } else {
      vscode.postMessage({
        type: 'restartCampaign',
        archive,
        reason: reason.trim() || 'restart campaign from scratch',
      });
    }
    onClose();
  }

  return (
    <div className="modal-backdrop">
      <section className="modal restart-confirm-modal" style={{ maxWidth: '560px' }}>
        <div className="modal-head">
          <h2>{isNode ? 'Restart node' : 'Restart campaign'}</h2>
          <button type="button" onClick={onClose} disabled={submitting}>Cancel</button>
        </div>
        {isNode ? (
          <p>
            Restart node <strong>{confirm.nodeLabel}</strong>?
          </p>
        ) : (
          <p>
            Restart campaign <strong>{confirm.campaignName}</strong> from the entry node?
          </p>
        )}
        <p className="subtle">
          This will:
        </p>
        <ul className="subtle restart-checklist">
          <li>cancel any orchestrator + heartbeat SLURM job currently running for this campaign</li>
          {isNode ? (
            <li>re-execute the node only (other nodes' prior outputs are preserved)</li>
          ) : (
            <>
              <li>reset every node's status back to <code>pending</code></li>
              <li>
                {archive ? 'archive' : 'leave in place'} the prior <code>runs/&lt;run_id&gt;</code>{' '}
                directories so prior artifacts remain inspectable
              </li>
              <li>submit a fresh orchestrator that re-executes the graph from the entry node</li>
            </>
          )}
          <li>write an audit event so the run-history drawer shows the boundary</li>
        </ul>
        <label>Reason (optional)
          <input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={isNode ? 'e.g. unanimous reject; retry with broader prompt' : 'e.g. wrong objective; starting over'}
            disabled={submitting}
          />
        </label>
        {!isNode ? (
          <label className="check-line">
            <input
              type="checkbox"
              checked={archive}
              onChange={(event) => setArchive(event.target.checked)}
              disabled={submitting}
            />
            Archive prior artifacts (recommended)
          </label>
        ) : null}
        <div className="restart-actions">
          <button type="button" onClick={onClose} disabled={submitting}>Cancel</button>
          <button type="button" className="danger" onClick={fire} disabled={submitting}>
            {submitting ? 'Submitting…' : (isNode ? 'Confirm restart node' : 'Confirm restart campaign')}
          </button>
        </div>
      </section>
    </div>
  );
}

function NewCampaignModal({ onClose }) {
  const [draft, setDraft] = useState({
    title: '',
    objective: '',
    budgetCap: 3,
    tier: 'standard',
    outputFormat: 'markdown',
    template: 'target_research',
    autoStart: true,
    dryRun: true,
    model: '',
    maxRunSeconds: 3600,
    counsel: false,
    math: false,
    treeSearch: false,
    allowSpend: false,
    confirmation: '',
    // Persona-council overrides (per-campaign metadata).
    personaDebateRounds: 3,
    personaMaxSynthesisAttempts: 5,
    personaDeadlockPolicy: 'pause'
  });
  const [advancedOpen, setAdvancedOpen] = useState(false);

  function update(key, value) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function submit(event) {
    event.preventDefault();
    vscode.postMessage({ type: 'createCampaign', draft });
  }

  const realRun = draft.autoStart && !draft.dryRun;
  const blocked = realRun && (!draft.allowSpend || draft.confirmation !== 'RUN LOCAL');

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
            {['scaffold', 'lean', 'standard', 'serious', 'ultra'].map((tier) => <option key={tier} value={tier}>{tier}</option>)}
          </select></label>
          <label>Output<select value={draft.outputFormat} onChange={(event) => update('outputFormat', event.target.value)}>
            <option value="markdown">markdown</option>
            <option value="latex">latex</option>
          </select></label>
          <label>Template<select value={draft.template} onChange={(event) => update('template', event.target.value)}>
            <option value="target_research">target research</option>
            <option value="consortium_scaffold">consortium scaffold (legacy alias)</option>
            <option value="consortium_budget">consortium budget</option>
            <option value="literature_only">literature only</option>
            <option value="experiment_design">experiment design</option>
            <option value="blank">blank</option>
          </select></label>
        </div>
        <section className="spend-gate">
          <label className="check-line"><input type="checkbox" checked={draft.autoStart} onChange={(event) => update('autoStart', event.target.checked)} /> Start automatically after creation</label>
          {draft.autoStart ? (
            <>
              <div className="form-grid">
                <label>Run mode<select value={draft.dryRun ? 'dry' : 'real'} onChange={(event) => update('dryRun', event.target.value === 'dry')}>
                  <option value="dry">Dry validation (no spend)</option>
                  <option value="real">Real local execution</option>
                </select></label>
                <label>Model<input value={draft.model} onChange={(event) => update('model', event.target.value)} placeholder="tier default" /></label>
                <label>Max seconds<input type="number" min="1" value={draft.maxRunSeconds} onChange={(event) => update('maxRunSeconds', event.target.value)} /></label>
              </div>
              <div className="toggle-row">
                <label><input type="checkbox" checked={draft.counsel} onChange={(event) => update('counsel', event.target.checked)} /> Persona counsel</label>
                <label><input type="checkbox" checked={draft.math} onChange={(event) => update('math', event.target.checked)} /> Math agents</label>
                <label><input type="checkbox" checked={draft.treeSearch} onChange={(event) => update('treeSearch', event.target.checked)} /> Tree search</label>
              </div>
              {realRun ? (
                <>
                  <label className="check-line"><input type="checkbox" checked={draft.allowSpend} onChange={(event) => update('allowSpend', event.target.checked)} /> Allow OpenRouter spend for this campaign execution</label>
                  <label>Confirmation<input value={draft.confirmation} onChange={(event) => update('confirmation', event.target.value)} placeholder="RUN LOCAL" /></label>
                </>
              ) : null}
            </>
          ) : null}
        </section>
        <section className="advanced-section">
          <button type="button" className="advanced-toggle" onClick={() => setAdvancedOpen((v) => !v)}>
            {advancedOpen ? '▾ Advanced (persona council)' : '▸ Advanced (persona council)'}
          </button>
          {advancedOpen ? (
            <div className="form-grid">
              <label>Debate rounds
                <input type="number" min="1" max="10"
                       value={draft.personaDebateRounds}
                       onChange={(event) => update('personaDebateRounds', event.target.value)} />
              </label>
              <label>Max synthesis attempts
                <input type="number" min="1" max="20"
                       value={draft.personaMaxSynthesisAttempts}
                       onChange={(event) => update('personaMaxSynthesisAttempts', event.target.value)} />
              </label>
              <label>On deadlock
                <select value={draft.personaDeadlockPolicy}
                        onChange={(event) => update('personaDeadlockPolicy', event.target.value)}>
                  <option value="pause">Pause (halt + ask)</option>
                  <option value="best_effort">Best effort (advance anyway)</option>
                </select>
              </label>
            </div>
          ) : null}
        </section>
        {blocked ? <div className="notice error">Real local execution requires allow spend plus confirmation text RUN LOCAL.</div> : null}
        <button className="primary" type="submit" disabled={blocked}>{draft.autoStart ? 'Create and Start Campaign' : 'Create Draft'}</button>
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
  const anyTransient = visible.some((error) => {
    const text = String(error.message || error.error || '');
    return /\b(EAGAIN|ENOMEM|EMFILE|ENFILE)\b/.test(text);
  });
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
        {anyTransient ? (
          <p className="subtle" style={{ margin: '0.4rem 0 0' }}>
            The node was busy — retrying often clears this.
          </p>
        ) : null}
        <div style={{ marginTop: '0.5rem' }}>
          <button onClick={() => vscode.postMessage({ type: 'refresh' })}>Retry</button>
        </div>
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
      councilPolicy: node.metadata?.councilPolicy || {},
      tierPolicy: node.metadata?.tierPolicy || {},
      modelPolicy: node.metadata?.modelPolicy || {},
      dualityRequired: Boolean(node.metadata?.dualityRequired),
      requiresDualityPass: Boolean(node.metadata?.requiresDualityPass),
      routerSpec: node.metadata?.routerSpec || null,
      subgraphSpec: node.metadata?.subgraphSpec || null,
      subgraphId: node.metadata?.subgraphId || '',
      stateReads: node.metadata?.stateReads || [],
      stateWrites: node.metadata?.stateWrites || [],
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
  return routes.map((route) => {
    const label = route.metadata?.routeLabel || route.metadata?.condition || route.condition || 'always';
    return `${label} -> ${route.target || '?'} (${route.kind || 'route'})`;
  }).join(', ');
}

function formatRouter(router) {
  if (!router || !Array.isArray(router.branches) || !router.branches.length) return '-';
  return router.branches.map((branch) => {
    const target = branch.terminal ? 'END' : (branch.target || '?');
    return `${branch.label} -> ${target}`;
  }).join(', ');
}

function formatRetryCaps(router) {
  if (!router || !Array.isArray(router.branches)) return '-';
  const caps = router.branches
    .filter((branch) => branch.retry && branch.retry.max_attempts != null)
    .map((branch) => `${branch.label}: ${branch.retry.max_attempts} via ${branch.retry.counter || 'counter'}`);
  return caps.length ? caps.join(', ') : '-';
}

function formatSubgraph(node) {
  if (!node) return '-';
  if (!node.subgraphSpec) return node.subgraphId || '-';
  const stages = Array.isArray(node.subgraphSpec.stageIds) ? node.subgraphSpec.stageIds.length : 0;
  const label = node.subgraphSpec.metadata?.label || node.subgraphSpec.id || node.subgraphId;
  return `${label}${stages ? ` (${stages} internal nodes)` : ''}`;
}

function formatCouncil(policy) {
  if (!policy || !policy.kind || policy.kind === 'none') return '-';
  const modelCount = Array.isArray(policy.model_ids) ? policy.model_ids.length : 0;
  return `${String(policy.kind).replace(/_/g, ' ')}${modelCount ? ` (${modelCount} models)` : ''}`;
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
