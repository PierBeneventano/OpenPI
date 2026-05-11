import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const vscode = acquireVsCodeApi();

const emptyState = {
  root: '',
  view: 'home',
  loading: true,
  campaigns: [],
  settings: {},
  diagnostics: {},
  campaignDetails: null,
  campaignGraph: null,
  campaignArtifacts: [],
  selectedGraphNode: null,
  artifactPreview: null,
  activeRun: null,
  runLog: [],
  steering: {}
};

function App() {
  const [state, setState] = useState(emptyState);
  const [newCampaignOpen, setNewCampaignOpen] = useState(false);
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

  if (state.view === 'campaign') {
    return (
      <CampaignWorkspace
        state={state}
        tab={workspaceTab}
        setTab={setWorkspaceTab}
      />
    );
  }

  return (
    <Home
      state={state}
      newCampaignOpen={newCampaignOpen}
      setNewCampaignOpen={setNewCampaignOpen}
    />
  );
}

function Home({ state, newCampaignOpen, setNewCampaignOpen }) {
  const campaigns = state.campaigns || [];
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
          <button onClick={() => vscode.postMessage({ type: 'openSettings' })}>Settings</button>
          <button className="primary" onClick={() => setNewCampaignOpen(true)}>New Campaign</button>
        </div>
      </header>

      {state.actionError ? <div className="notice error">{state.actionError}</div> : null}

      <section className="metrics-grid">
        <Metric label="Campaigns" value={campaigns.length} detail="local specs" />
        <Metric label="Active" value={activeCampaigns} detail="draft, planned, or running" />
        <Metric label="Artifacts" value={totalArtifacts} detail="declared and discovered" />
        <Metric label="OpenRouter" value={state.settings?.openRouterConfigured ? 'Ready' : 'Missing'} detail="settings check" />
      </section>

      <section className="campaign-grid">
        {campaigns.length ? campaigns.map((campaign) => (
          <button
            key={campaign.path || campaign.name}
            className="campaign-card"
            onClick={() => vscode.postMessage({ type: 'selectCampaign', campaign: campaign.path || campaign.name })}
          >
            <div className="card-topline">
              <span className={`status-dot status-${statusClass(campaign.status)}`} />
              <span>{campaign.status || 'unknown'}</span>
            </div>
            <h2>{campaign.title || campaign.name || 'Untitled campaign'}</h2>
            <p>{campaign.path || campaign.workspaceRoot || 'No path'}</p>
            <div className="card-facts">
              <span>{formatBudget(campaign.budget)}</span>
              <span>{campaign.artifactCount || 0} artifacts</span>
              <span>{campaign.requiredMissing || 0} missing</span>
            </div>
          </button>
        )) : (
          <div className="empty-panel">
            <h2>No campaigns yet</h2>
            <p>Create a draft campaign to start shaping the local workflow without launching anything.</p>
            <button className="primary" onClick={() => setNewCampaignOpen(true)}>New Campaign</button>
          </div>
        )}
      </section>

      {state.settingsOpen ? <SettingsModal state={state} /> : null}
      {newCampaignOpen ? <NewCampaignModal onClose={() => setNewCampaignOpen(false)} /> : null}
    </div>
  );
}

function CampaignWorkspace({ state, tab, setTab }) {
  const [runOpen, setRunOpen] = useState(false);
  const details = state.campaignDetails || {};
  const title = details.name || details.campaign_id || basename(state.selectedCampaign) || 'Campaign';
  const active = state.activeRun && ['running', 'stopping'].includes(state.activeRun.status);

  return (
    <div className="app-shell workspace-shell">
      <header className="workspace-header">
        <div className="workspace-title">
          <button onClick={() => vscode.postMessage({ type: 'backToCampaigns' })}>Back</button>
          <div>
            <p className="eyebrow">Campaign</p>
            <h1>{title}</h1>
            <p className="subtle">{details.path || state.selectedCampaign}</p>
          </div>
        </div>
        <div className="header-actions">
          <span className={`pill status-${statusClass(statusOf(details))}`}>{statusOf(details)}</span>
          <span className="pill">{formatBudget(details.budget)}</span>
          {active ? (
            <button className="danger" onClick={() => vscode.postMessage({ type: 'stopRun' })}>Stop Run</button>
          ) : (
            <button className="primary" onClick={() => setRunOpen(true)}>Start Run</button>
          )}
          <button onClick={() => vscode.postMessage({ type: 'refreshCampaign' })}>Refresh</button>
        </div>
      </header>

      {state.actionError ? <div className="notice error">{state.actionError}</div> : null}

      <nav className="workspace-tabs">
        {['graph', 'steer', 'artifacts'].map((name) => (
          <button key={name} className={tab === name ? 'active' : ''} onClick={() => setTab(name)}>
            {capitalize(name)}
          </button>
        ))}
      </nav>

      <RunStatusPanel state={state} onStart={() => setRunOpen(true)} />
      {tab === 'graph' ? <GraphTab state={state} /> : null}
      {tab === 'steer' ? <SteerTab state={state} /> : null}
      {tab === 'artifacts' ? <ArtifactsTab state={state} /> : null}
      {runOpen ? <RunCampaignModal state={state} onClose={() => setRunOpen(false)} /> : null}
    </div>
  );
}

function RunStatusPanel({ state, onStart }) {
  const run = state.activeRun;
  const logs = state.runLog || [];
  if (!run && !logs.length) {
    return (
      <section className="run-strip idle">
        <div>
          <strong>No active run</strong>
          <span>Start the local pipeline when the campaign graph and budget look right.</span>
        </div>
        <button className="primary" onClick={onStart}>Start Run</button>
      </section>
    );
  }
  return (
    <section className="run-strip">
      <div className="run-summary">
        <span className={`status-dot status-${statusClass(run?.status || 'idle')}`} />
        <div>
          <strong>{run?.status || 'idle'}</strong>
          <span>{run?.dryRun ? 'dry run' : 'real local run'}{run?.pid ? ` | pid ${run.pid}` : ''}</span>
        </div>
      </div>
      <div className="run-actions">
        {run && ['running', 'stopping'].includes(run.status) ? (
          <button className="danger" onClick={() => vscode.postMessage({ type: 'stopRun' })}>Stop</button>
        ) : (
          <button onClick={onStart}>Run Again</button>
        )}
      </div>
      <div className="run-log">
        {logs.slice(-8).map((entry, index) => (
          <code key={`${entry.timestamp || ''}-${index}`} className={`log-${entry.stream || 'system'}`}>
            {entry.text}
          </code>
        ))}
      </div>
    </section>
  );
}

function RunCampaignModal({ state, onClose }) {
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

  function update(key, value) {
    setRun((current) => ({ ...current, [key]: value }));
  }

  function submit(event) {
    event.preventDefault();
    vscode.postMessage({ type: 'startRun', ...run });
    onClose();
  }

  const realRun = !run.dryRun;
  const blocked = realRun && (!run.allowSpend || run.confirmation !== 'RUN LOCAL');

  return (
    <div className="modal-backdrop">
      <form className="modal run-modal" onSubmit={submit}>
        <div className="modal-head">
          <div>
            <h2>Start Campaign Run</h2>
            <p className="subtle">Runs execute locally in this workspace and attach events/artifacts to the selected campaign.</p>
          </div>
          <button type="button" onClick={onClose}>Cancel</button>
        </div>
        <label>Task<textarea value={run.task} onChange={(event) => update('task', event.target.value)} required /></label>
        <div className="form-grid">
          <label>Run profile<select value={run.tier} onChange={(event) => update('tier', event.target.value)}>
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
            <option value="dry">Dry run</option>
            <option value="real">Real local run</option>
          </select></label>
        </div>
        <div className="toggle-row">
          <label><input type="checkbox" checked={run.counsel} onChange={(event) => update('counsel', event.target.checked)} /> Persona counsel</label>
          <label><input type="checkbox" checked={run.math} onChange={(event) => update('math', event.target.checked)} /> Math track</label>
          <label><input type="checkbox" checked={run.treeSearch} onChange={(event) => update('treeSearch', event.target.checked)} /> Tree search</label>
        </div>
        {realRun ? (
          <section className="spend-gate">
            <label><input type="checkbox" checked={run.allowSpend} onChange={(event) => update('allowSpend', event.target.checked)} /> Allow OpenRouter spend for this local run</label>
            <label>Confirmation<input value={run.confirmation} onChange={(event) => update('confirmation', event.target.value)} placeholder="RUN LOCAL" /></label>
          </section>
        ) : null}
        <button className="primary" type="submit" disabled={blocked}>
          {run.dryRun ? 'Start Dry Run' : 'Start Real Local Run'}
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
        <h3>Artifacts</h3>
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
      <text x="14" y={node.height - 15} className="node-meta">{node.status || 'unknown'} | {node.artifactCount || 0} artifacts</text>
    </g>
  );
}

function SteerTab({ state }) {
  const active = state.activeRun && ['running', 'stopping'].includes(state.activeRun.status);
  const steering = state.steering || {};
  return (
    <main className="split-layout">
      <section className="panel">
        <p className="eyebrow">Downstream assistant layer</p>
        <h2>OpenClaude Placeholder</h2>
        <p>
          OpenClaude will become the assistant layer after the campaign UX has stable campaign, graph, artifact,
          run, and preview primitives. This pass keeps it visible for readiness context only.
        </p>
        <dl className="definition-list">
          <dt>OpenClaude</dt><dd>{state.diagnostics?.openclaude?.launch_ready ? 'ready' : 'not ready'}</dd>
          <dt>OpenRouter</dt><dd>{state.settings?.openRouterConfigured ? 'configured' : 'missing'}</dd>
          <dt>Model</dt><dd>{state.diagnostics?.openclaude?.model || '-'}</dd>
        </dl>
      </section>

      <section className="panel">
        <h2>Low-Level Local Steering</h2>
        {active ? (
          <LowLevelSteering steering={steering} />
        ) : (
          <p className="subtle">Start a locally hosted campaign run before low-level steering controls appear here.</p>
        )}
      </section>
    </main>
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
      {steering.lastError ? <div className="notice">Steering not available for this run: {steering.lastError}</div> : null}
      <button onClick={() => vscode.postMessage({ type: 'interruptRun' })}>Interrupt / Pause</button>
      <textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="Instruction for the active local run" />
      <button className="primary" onClick={() => vscode.postMessage({ type: 'sendInstruction', text, instructionType: 'm' })}>
        Send Instruction
      </button>
    </div>
  );
}

function ArtifactsTab({ state }) {
  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [stageFilter, setStageFilter] = useState('all');
  const [existenceFilter, setExistenceFilter] = useState('all');
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
    return matchesQuery && matchesType && matchesStage && matchesExistence;
  });

  return (
    <main className="split-layout artifact-layout">
      <section className="panel">
        <div className="filters">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search artifacts" />
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="all">All types</option>
            {types.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
          <select value={stageFilter} onChange={(event) => setStageFilter(event.target.value)}>
            <option value="all">All stages</option>
            {stages.map((stage) => <option key={stage} value={stage}>{stage}</option>)}
          </select>
          <select value={existenceFilter} onChange={(event) => setExistenceFilter(event.target.value)}>
            <option value="all">All artifacts</option>
            <option value="existing">Existing</option>
            <option value="missing">Missing</option>
            <option value="required">Required</option>
            <option value="optional">Optional</option>
          </select>
        </div>
        <ArtifactRows artifacts={filtered} />
      </section>
      <PreviewPanel preview={state.artifactPreview} />
    </main>
  );
}

function ArtifactRows({ artifacts }) {
  if (!artifacts.length) {
    return <p className="subtle">No artifacts found.</p>;
  }
  return (
    <div className="artifact-list">
      {artifacts.map((artifact) => (
        <div key={artifact.id || artifact.path} className="artifact-row">
          <div>
            <strong>{artifact.label || basename(artifact.path) || 'Artifact'}</strong>
            <span>{artifact.path || 'No file path'}</span>
          </div>
          <div className="artifact-actions">
            <span className="pill">{artifact.required ? 'required' : 'optional'}</span>
            <span className="pill">{artifact.exists ? 'exists' : 'missing'}</span>
            <button disabled={!artifact.exists} onClick={() => vscode.postMessage({ type: 'previewArtifact', artifact })}>Preview</button>
            <button disabled={!artifact.exists} onClick={() => vscode.postMessage({ type: 'openArtifact', artifact })}>Open</button>
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

function SettingsModal({ state }) {
  const settings = state.settings || {};
  const commands = state.diagnostics?.commands || [];
  return (
    <div className="modal-backdrop">
      <section className="modal">
        <div className="modal-head">
          <h2>Settings</h2>
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
    localMode: true,
    template: 'consortium_scaffold'
  });

  function update(key, value) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function submit(event) {
    event.preventDefault();
    vscode.postMessage({ type: 'createCampaign', draft });
    onClose();
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
        <label className="check-line"><input type="checkbox" checked={draft.localMode} onChange={(event) => update('localMode', event.target.checked)} /> Local mode</label>
        <button className="primary" type="submit">Create Draft</button>
      </form>
    </div>
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

function capitalize(value) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function unique(values) {
  return Array.from(new Set(values));
}

createRoot(document.getElementById('root')).render(<App />);
