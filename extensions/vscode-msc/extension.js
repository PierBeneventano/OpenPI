const vscode = require('vscode');
const childProcess = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const path = require('path');

const MAX_LOG_LINES = 400;
const MAX_CHAT_MESSAGES = 120;
const MAX_PROMPT_HISTORY_MESSAGES = 14;
const MAX_PROMPT_HISTORY_CHARS = 1800;
const RUN_CONFIRMATION = 'RUN LOCAL';
const STEERING_URL = 'http://127.0.0.1:5002';
const TEXT_PREVIEW_BYTES = 256 * 1024;

function activate(context) {
  const disposable = vscode.commands.registerCommand('mscDashboard.open', () => {
    const root = getWorkspaceRoot();
    const panel = vscode.window.createWebviewPanel(
      'mscDashboard',
      'MSc Campaigns',
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [
          vscode.Uri.file(path.join(context.extensionPath, 'webview-ui', 'dist')),
          vscode.Uri.file(root)
        ]
      }
    );

    const session = createDashboardSession(panel, root);
    panel.webview.html = renderHtml(context, panel.webview);

    panel.webview.onDidReceiveMessage(async (message) => {
      if (!message || !message.type) {
        return;
      }
      await handleMessage(session, message);
    }, undefined, context.subscriptions);

    panel.onDidDispose(() => cleanupSession(session), undefined, context.subscriptions);
  });

  context.subscriptions.push(disposable);
}

function deactivate() {}

async function handleMessage(session, message) {
  if (message.type === 'ready' || message.type === 'refresh') {
    await refresh(session);
  } else if (message.type === 'selectCampaign') {
    await selectCampaign(session, message.campaign);
  } else if (message.type === 'backToCampaigns') {
    session.state.view = 'home';
    session.state.selectedCampaign = null;
    session.state.campaignDetails = null;
    session.state.campaignWorkspace = null;
    session.state.campaignGraph = null;
    session.state.campaignArtifacts = [];
    session.state.campaignEvents = [];
    session.state.campaignExecution = null;
    session.state.campaignDecisions = [];
    session.state.campaignFeedback = [];
    session.state.campaignDeliverables = [];
    session.state.campaignPlannedOutputs = [];
    session.state.campaignDiagnosticArtifacts = [];
    session.state.campaignRunSummary = defaultRunSummary();
    session.state.selectedGraphNode = null;
    session.state.artifactPreview = null;
    saveOpenClaudeHistory(session);
    session.state.openClaude = defaultOpenClaudeState();
    postState(session);
  } else if (message.type === 'createCampaign' || message.type === 'createCampaignDraft') {
    await createCampaignDraftForSession(session, message.draft || {});
  } else if (message.type === 'deleteCampaign') {
    await deleteCampaignForSession(session, message);
  } else if (message.type === 'openSettings') {
    session.state.settingsOpen = true;
    postState(session);
  } else if (message.type === 'closeSettings') {
    session.state.settingsOpen = false;
    postState(session);
  } else if (message.type === 'refreshCampaign') {
    await selectCampaign(session, session.state.selectedCampaign);
  } else if (message.type === 'selectGraphNode') {
    session.state.selectedGraphNode = String(message.nodeId || '');
    postState(session);
  } else if (message.type === 'previewArtifact') {
    await previewArtifactForSession(session, message.artifact || {});
  } else if (message.type === 'openArtifact') {
    await openArtifactForSession(session, message.artifact || {});
  } else if (message.type === 'startCampaign' || message.type === 'startRun') {
    await startCampaignExecution(session, message);
  } else if (message.type === 'stopCampaign' || message.type === 'stopRun') {
    stopCampaignExecution(session);
  } else if (message.type === 'interruptRun') {
    await interruptRun(session);
  } else if (message.type === 'sendInstruction') {
    await sendInstruction(session, message);
  } else if (message.type === 'submitFeedback') {
    await submitFeedback(session, message);
  } else if (message.type === 'openClaudeStart') {
    await startOpenClaudeSession(session, message);
  } else if (message.type === 'openClaudeStop') {
    stopOpenClaudeSession(session);
  } else if (message.type === 'openClaudeSend') {
    await sendOpenClaudeMessage(session, message);
  } else if (message.type === 'openClaudeRestartModel') {
    await restartOpenClaudeWithModel(session, message);
  } else if (message.type === 'openClaudeClearHistory') {
    clearOpenClaudeHistory(session);
  } else if (message.type === 'linkArtifactContext') {
    await linkArtifactContext(session, message);
  } else if (message.type === 'updateContextLink') {
    await updateContextLink(session, message);
  }
}

function createDashboardSession(panel, root) {
  return {
    panel,
    root,
    activeProcess: null,
    openClaudeProcess: null,
    stopTimer: null,
    steeringTimer: null,
    refreshPromise: null,
    state: initialState(root)
  };
}

function cleanupSession(session) {
  stopSteeringPolling(session);
  if (session.stopTimer) {
    clearTimeout(session.stopTimer);
    session.stopTimer = null;
  }
  // Closing a dashboard panel should not be a destructive run-control action.
  // The explicit Stop button owns process termination; panel disposal only
  // detaches this UI session from further log/steering updates.
  session.activeProcess = null;
  if (session.openClaudeProcess) {
    session.openClaudeProcess.kill('SIGTERM');
    session.openClaudeProcess = null;
  }
}

function getWorkspaceRoot() {
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
  return findProjectRoot(folder ? folder.uri.fsPath : process.cwd());
}

function findProjectRoot(startPath) {
  const start = path.resolve(startPath || process.cwd());
  const candidates = [
    start,
    path.join(start, 'PoggioAI_MSc'),
    path.dirname(start)
  ];
  let current = start;
  for (let index = 0; index < 5; index += 1) {
    candidates.push(current);
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  for (const candidate of candidates) {
    if (isMscProjectRoot(candidate)) {
      return candidate;
    }
  }
  return start;
}

function isMscProjectRoot(candidate) {
  return Boolean(candidate && fs.existsSync(path.join(candidate, 'msc_sdk')) && fs.existsSync(path.join(candidate, 'extensions', 'vscode-msc', 'extension.js')));
}

function initialState(root) {
  return {
    root,
    view: 'home',
    loading: true,
    generatedAt: new Date().toISOString(),
    errors: [],
    loaded: false,
    campaigns: [],
    selectedCampaign: null,
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
    campaignRunSummary: defaultRunSummary(),
    selectedGraphNode: null,
    artifactPreview: null,
    settingsOpen: false,
    settings: defaultSettings(root),
    diagnostics: {},
    activeRun: null,
    runLog: [],
    steering: { available: false, paused: false, queueDepth: 0, lastError: null },
    openClaude: defaultOpenClaudeState(),
    actionError: null
  };
}

function defaultOpenClaudeState() {
  return {
    status: 'idle',
    model: 'openai/gpt-5-mini',
    models: [],
    error: null,
    transcript: [],
    actions: [],
    contextPack: null,
    contextLinks: [],
    campaignRef: null,
    chatStoragePath: null,
    historyLoaded: false,
    lastStartedAt: null
  };
}

function defaultSettings(root) {
  return {
    workspaceRoot: root,
    cliPath: resolveMscBin(root),
    openRouterConfigured: Boolean(process.env.OPENROUTER_API_KEY),
    defaultBudget: 20,
    defaultTier: 'budget',
    defaultOutput: 'markdown',
    localDbPath: path.join(root, '.msc', 'campaigns.db'),
    bundleExportRoot: path.join(root, 'campaigns'),
    budgetSummary: null
  };
}

async function refresh(session) {
  if (session.refreshPromise) {
    return session.refreshPromise;
  }
  session.refreshPromise = doRefresh(session).finally(() => {
    session.refreshPromise = null;
  });
  return session.refreshPromise;
}

async function doRefresh(session) {
  const previous = session.state;
  session.state = { ...previous, loading: true, actionError: null };
  postState(session);

  const state = await collectDashboardData(session.root);
  state.view = previous.view || 'home';
  state.selectedCampaign = previous.selectedCampaign;
  state.settingsOpen = previous.settingsOpen;
  state.activeRun = previous.activeRun;
  state.runLog = previous.runLog;
  state.steering = previous.steering;
  state.openClaude = previous.openClaude || defaultOpenClaudeState();
  state.actionError = previous.actionError;

  if (state.selectedCampaign) {
    Object.assign(state, await loadCampaignWorkspace(session.root, state.selectedCampaign));
  }

  session.state = state;
  if (state.selectedCampaign) {
    hydrateOpenClaudeHistory(session, state.selectedCampaign);
  }
  postState(session);
}

async function collectDashboardData(root) {
  const state = initialState(root);
  state.loading = false;
  state.loaded = true;

  const campaigns = await runJson(root, ['campaigns', '--root', root, 'list', '--json']);
  state.campaigns = await enrichCampaigns(root, unwrap(campaigns, 'campaigns').campaigns || []);

  const readiness = await runJson(root, ['project', 'readiness', '--json']);
  const commands = await runJson(root, ['selftest', 'commands', '--json']);
  const config = await runText(root, ['config', 'list']);
  const budget = await runText(root, ['budget']);
  const openclaude = await runJson(root, ['openclaude', 'readiness', '--json']);
  const openclaudeEnv = await runJson(root, ['openclaude', 'env', '--json']);
  const openclaudeModels = await runJson(root, ['openclaude', 'models', '--json']);

  state.settings = {
    ...defaultSettings(root),
    openRouterConfigured: openRouterConfigured(openclaude, openclaudeEnv),
    budgetSummary: budget.stdout || budget.stderr || null,
    configList: config.stdout || config.stderr || null,
    readiness: unwrap(readiness, 'readiness')
  };
  state.diagnostics = {
    readiness: unwrap(readiness, 'readiness'),
    commands: unwrap(commands, 'commands').commands || [],
    openclaude: unwrap(openclaude, 'openclaude'),
    openclaudeEnv: unwrap(openclaudeEnv, 'openclaudeEnv'),
    openclaudeModels: unwrap(openclaudeModels, 'openclaudeModels')
  };
  state.errors = [
    ...campaigns.errors,
    ...readiness.errors,
    ...commands.errors,
    ...config.errors,
    ...budget.errors,
    ...openclaude.errors,
    ...openclaudeEnv.errors,
    ...openclaudeModels.errors
  ];
  state.openClaude = {
    ...state.openClaude,
    model: state.diagnostics.openclaude?.model || state.openClaude.model,
    models: state.diagnostics.openclaudeModels?.aliases || []
  };
  state.generatedAt = new Date().toISOString();
  return state;
}

async function enrichCampaigns(root, campaigns) {
  const enriched = [];
  for (const campaign of campaigns) {
    const ref = campaign.path || campaign.name || campaign.campaign_id;
    if (!ref) {
      enriched.push(campaign);
      continue;
    }
    const inspect = await runJson(root, ['campaigns', '--root', root, 'inspect', ref, '--json']);
    const artifacts = await runJson(root, ['campaigns', '--root', root, 'artifacts', ref, '--json']);
    const details = unwrap(inspect, 'campaign');
    const artifactRows = normalizeArtifacts(unwrap(artifacts, 'artifacts'));
    enriched.push({
      ...campaign,
      campaign_id: details.campaign_id || campaign.campaign_id || campaign.name,
      title: details.name || campaign.name || path.basename(ref),
      status: normalizeCampaignStatus(details),
      budget: details.budget || campaign.budget || {},
      artifactCount: artifactRows.length,
      requiredMissing: artifactRows.filter((item) => item.required && !item.exists).length,
      workspaceRoot: details.workspace_root || null,
      path: campaign.path || details.path || ref,
      inspectError: inspect.ok ? null : inspect.error
    });
  }
  return enriched;
}

async function selectCampaign(session, campaignRef) {
  if (!campaignRef) {
    setActionError(session, 'No campaign was selected.');
    return;
  }
  session.state = {
    ...session.state,
    loading: true,
    view: 'campaign',
    selectedCampaign: campaignRef,
    selectedGraphNode: null,
    artifactPreview: null,
    actionError: null
  };
  postState(session);

  const workspace = await loadCampaignWorkspace(session.root, campaignRef);
  session.state = {
    ...session.state,
    ...workspace,
    loading: false,
    view: 'campaign',
    selectedCampaign: campaignRef
  };
  hydrateOpenClaudeHistory(session, campaignRef, { replace: true });
  session.state.errors = [...(session.state.errors || []), ...(workspace.errors || [])];
  postState(session);
}

async function loadCampaignWorkspace(root, campaignRef) {
  const workspaceResult = await runJson(root, ['campaigns', '--root', root, 'workspace', campaignRef, '--json']);
  const workspace = unwrap(workspaceResult, 'campaignWorkspace');
  const diagnostics = workspace.diagnostics || {};
  const campaignEvents = Array.isArray(diagnostics.events) ? diagnostics.events : [];
  const deliverables = Array.isArray(workspace.deliverables) ? workspace.deliverables : [];
  const plannedOutputs = Array.isArray(workspace.planned_outputs) ? workspace.planned_outputs : [];
  const diagnosticArtifacts = Array.isArray(diagnostics.artifacts) ? diagnostics.artifacts : [];
  const allArtifacts = [...deliverables, ...plannedOutputs, ...diagnosticArtifacts];
  const contextLinks = Array.isArray(workspace.context?.links) ? workspace.context.links : [];
  return {
    campaignWorkspace: workspace,
    campaignDetails: campaignDetailsFromWorkspace(workspace),
    campaignGraph: workspace.graph || {},
    campaignArtifacts: normalizeArtifacts({ artifacts: allArtifacts }),
    campaignExecution: workspace.execution || null,
    campaignDecisions: workspace.pending_decisions || workspace.decisions || [],
    campaignFeedback: workspace.feedback || [],
    campaignDeliverables: normalizeArtifacts({ artifacts: deliverables }),
    campaignPlannedOutputs: normalizeArtifacts({ artifacts: plannedOutputs }),
    campaignDiagnosticArtifacts: normalizeArtifacts({ artifacts: diagnosticArtifacts }),
    campaignEvents,
    campaignRunSummary: summarizeCampaignWorkspace(workspace, campaignEvents),
    openClaudeContextLinks: contextLinks,
    errors: workspaceResult.errors || []
  };
}

function campaignDetailsFromWorkspace(workspace) {
  const campaign = workspace.campaign || {};
  return {
    campaign_id: campaign.id,
    path: campaign.bundle_path || campaign.id,
    name: campaign.title || campaign.id,
    objective: campaign.objective || '',
    workspace_root: campaign.workspace_root || '',
    status: campaign.status || workspace.execution?.status || 'unknown',
    budget: { limit_usd: campaign.budget_cap_usd, metadata: { tier: campaign.tier, output_format: campaign.output_format } },
    stages: stagesFromWorkspace(workspace),
    metadata: {
      source: workspace.provenance?.source || 'campaign_workspace',
      objective: campaign.objective || '',
      safe_next_actions: workspace.safe_next_actions || []
    },
    provenance: workspace.provenance || {}
  };
}

function stagesFromWorkspace(workspace) {
  const graph = workspace.graph || {};
  const artifacts = normalizeArtifacts({
    artifacts: [
      ...(workspace.deliverables || []),
      ...(workspace.planned_outputs || []),
      ...((workspace.diagnostics && workspace.diagnostics.artifacts) || [])
    ]
  });
  return (graph.nodes || []).map((node) => {
    const stageArtifacts = artifacts.filter((artifact) => artifact.stage_id === node.id);
    return {
      stage_id: node.id,
      name: node.title || node.label || node.id,
      status: node.status || 'planned',
      workspace: node.workspace,
      required_artifacts: stageArtifacts.filter((artifact) => artifact.required),
      optional_artifacts: stageArtifacts.filter((artifact) => !artifact.required),
      metadata: node.metadata || {}
    };
  });
}

async function createCampaignDraftForSession(session, draft) {
  const title = String(draft.title || '').trim();
  const objective = String(draft.objective || '').trim();
  if (!title) {
    setActionError(session, 'Campaign title is required.');
    return;
  }
  if (!objective) {
    setActionError(session, 'Research objective is required.');
    return;
  }
  const budget = normalizeBudget(draft.budgetCap);
  const tier = oneOf(draft.tier, ['live-smoke', 'budget', 'light', 'medium', 'pro', 'max', 'ultra'], 'budget');
  const outputFormat = oneOf(draft.outputFormat, ['markdown', 'latex'], 'markdown');
  const template = oneOf(draft.template, ['consortium_scaffold', 'consortium_budget', 'literature_only', 'experiment_design', 'blank'], 'consortium_scaffold');
  const result = await runJson(session.root, [
    'campaigns',
    '--root',
    session.root,
    'create',
    '--title',
    title,
    '--objective',
    objective,
    '--template',
    template,
    '--budget',
    String(budget),
    '--tier',
    tier,
    '--output-format',
    outputFormat,
    '--json'
  ]);
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Campaign creation failed.');
    return;
  }
  const created = result.data && result.data.campaign ? result.data.campaign : result.data;
  await refresh(session);
  await selectCampaign(session, created.campaign_id || created.path || created.name);
}

async function deleteCampaignForSession(session, message) {
  const campaignRef = String(message.campaign || session.state.selectedCampaign || '').trim();
  if (!campaignRef) {
    setActionError(session, 'Select a campaign before deleting it.');
    return;
  }
  if (message.confirm !== 'DELETE') {
    setActionError(session, 'Campaign deletion requires confirmation text DELETE.');
    return;
  }
  if (session.activeProcess) {
    setActionError(session, 'Stop the active campaign process before deleting this campaign.');
    return;
  }
  session.state = {
    ...session.state,
    loading: true,
    actionError: null
  };
  postState(session);
  const result = await runJson(session.root, [
    'campaigns',
    '--root',
    session.root,
    'delete',
    campaignRef,
    '--confirm',
    'DELETE',
    '--json'
  ]);
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Campaign could not be deleted.');
    return;
  }
  if (session.openClaudeProcess) {
    session.openClaudeProcess.kill('SIGTERM');
    session.openClaudeProcess = null;
  }
  const state = await collectDashboardData(session.root);
  state.view = 'home';
  state.selectedCampaign = null;
  state.actionError = null;
  state.openClaude = defaultOpenClaudeState();
  session.state = state;
  postState(session);
}

async function previewArtifactForSession(session, artifact) {
  try {
    session.state.artifactPreview = await previewArtifact(session, artifact);
    session.state.actionError = null;
    postState(session);
  } catch (error) {
    setActionError(session, error.message || String(error));
  }
}

async function previewArtifact(session, artifact) {
  const resolved = safeResolveArtifactPath(session.root, session.state.campaignDetails, artifact);
  const stat = fs.statSync(resolved);
  const ext = path.extname(resolved).toLowerCase();
  const base = {
    title: artifact.label || artifact.name || path.basename(resolved),
    path: resolved,
    relativePath: path.relative(session.root, resolved),
    size: stat.size
  };

  if (isImage(ext)) {
    return { ...base, kind: 'image', uri: session.panel.webview.asWebviewUri(vscode.Uri.file(resolved)).toString() };
  }
  if (ext === '.pdf') {
    return { ...base, kind: 'pdf', uri: session.panel.webview.asWebviewUri(vscode.Uri.file(resolved)).toString() };
  }
  if (!isPreviewableText(ext) && stat.size > TEXT_PREVIEW_BYTES) {
    return { ...base, kind: 'object', message: 'Preview is large; open it in VS Code.' };
  }

  const buffer = fs.readFileSync(resolved);
  return {
    ...base,
    kind: previewKind(ext),
    content: formatTextPreview(buffer.slice(0, TEXT_PREVIEW_BYTES).toString('utf8'), ext),
    truncated: buffer.length > TEXT_PREVIEW_BYTES
  };
}

async function openArtifactForSession(session, artifact) {
  try {
    const resolved = safeResolveArtifactPath(session.root, session.state.campaignDetails, artifact);
    await vscode.commands.executeCommand('vscode.open', vscode.Uri.file(resolved));
  } catch (error) {
    setActionError(session, error.message || String(error));
  }
}

function safeResolveArtifactPath(root, campaignDetails, artifact) {
  const artifactPath = String(artifact.path || artifact.file || artifact.workspace_path || artifact.relativePath || '').trim();
  if (!artifactPath) {
    throw new Error('Artifact has no path to preview.');
  }
  const allowedRoots = artifactAllowedRoots(root, campaignDetails, artifact);
  const candidates = path.isAbsolute(artifactPath)
    ? [artifactPath]
    : artifactCandidateRoots(root, campaignDetails, artifact).map((candidateRoot) => path.join(candidateRoot, artifactPath));

  for (const candidate of candidates) {
    const resolved = path.resolve(candidate);
    if (!allowedRoots.some((allowedRoot) => isWithin(resolved, allowedRoot))) {
      continue;
    }
    if (fs.existsSync(resolved) && fs.statSync(resolved).isFile()) {
      return resolved;
    }
  }
  throw new Error('Artifact preview path is outside allowed roots or does not exist.');
}

function artifactAllowedRoots(root, campaignDetails, artifact = {}) {
  const baseRoots = artifactBaseRoots(root, campaignDetails);
  const roots = new Set(baseRoots);
  for (const baseRoot of baseRoots) {
    roots.add(path.resolve(baseRoot, 'results'));
    roots.add(path.resolve(baseRoot, 'campaigns'));
  }
  if (campaignDetails && campaignDetails.workspace_root) {
    for (const baseRoot of baseRoots) {
      roots.add(path.resolve(baseRoot, campaignDetails.workspace_root));
    }
    if (path.isAbsolute(campaignDetails.workspace_root)) {
      roots.add(path.resolve(campaignDetails.workspace_root));
    }
  }
  if (campaignDetails && campaignDetails.path) {
    roots.add(path.isAbsolute(campaignDetails.path) ? path.resolve(campaignDetails.path) : path.resolve(root, campaignDetails.path));
  }
  const stages = campaignDetails && Array.isArray(campaignDetails.stages) ? campaignDetails.stages : [];
  for (const stage of stages) {
    if (stage.workspace) {
      roots.add(path.isAbsolute(stage.workspace) ? path.resolve(stage.workspace) : path.resolve(root, stage.workspace));
      for (const baseRoot of baseRoots) {
        roots.add(path.isAbsolute(stage.workspace) ? path.resolve(stage.workspace) : path.resolve(baseRoot, stage.workspace));
      }
    }
  }
  if (artifact.workspace) {
    roots.add(path.isAbsolute(artifact.workspace) ? path.resolve(artifact.workspace) : path.resolve(root, artifact.workspace));
    for (const baseRoot of baseRoots) {
      roots.add(path.isAbsolute(artifact.workspace) ? path.resolve(artifact.workspace) : path.resolve(baseRoot, artifact.workspace));
    }
  }
  if (artifact.root) {
    roots.add(path.isAbsolute(artifact.root) ? path.resolve(artifact.root) : path.resolve(root, artifact.root));
    for (const baseRoot of baseRoots) {
      roots.add(path.isAbsolute(artifact.root) ? path.resolve(artifact.root) : path.resolve(baseRoot, artifact.root));
    }
  }
  return Array.from(roots);
}

function artifactCandidateRoots(root, campaignDetails, artifact = {}) {
  const roots = [];
  const baseRoots = artifactBaseRoots(root, campaignDetails);
  const push = (value) => {
    if (!value) {
      return;
    }
    const resolved = path.isAbsolute(value) ? path.resolve(value) : path.resolve(root, value);
    if (!roots.includes(resolved)) {
      roots.push(resolved);
    }
  };
  const pushFromBases = (value) => {
    if (!value) {
      return;
    }
    if (path.isAbsolute(value)) {
      push(value);
      return;
    }
    for (const baseRoot of baseRoots) {
      push(path.resolve(baseRoot, value));
    }
  };

  pushFromBases(artifact.workspace);
  pushFromBases(artifact.root);

  const stages = campaignDetails && Array.isArray(campaignDetails.stages) ? campaignDetails.stages : [];
  const stage = stages.find((candidate) => {
    return candidate.stage_id === artifact.stage_id || candidate.id === artifact.stage_id || candidate.name === artifact.stage_id;
  });
  pushFromBases(stage && stage.workspace);
  pushFromBases(campaignDetails && campaignDetails.workspace_root);
  for (const baseRoot of baseRoots) {
    push(baseRoot);
  }
  return roots.length ? roots : [path.resolve(root)];
}

function artifactBaseRoots(root, campaignDetails) {
  const roots = [path.resolve(root)];
  const push = (value) => {
    if (!value) {
      return;
    }
    const resolved = path.resolve(value);
    if (!roots.includes(resolved)) {
      roots.push(resolved);
    }
  };
  if (campaignDetails && campaignDetails.path && path.isAbsolute(campaignDetails.path)) {
    const campaignPath = path.resolve(campaignDetails.path);
    push(campaignPath);
    if (path.basename(path.dirname(campaignPath)) === 'campaigns') {
      push(path.dirname(path.dirname(campaignPath)));
    }
  }
  const source = campaignDetails && campaignDetails.provenance && campaignDetails.provenance.source;
  if (source && path.isAbsolute(source)) {
    const sourcePath = path.resolve(source);
    if (path.basename(path.dirname(sourcePath)) === '.msc') {
      push(path.dirname(path.dirname(sourcePath)));
    }
  }
  return roots;
}

function isWithin(filePath, root) {
  const relative = path.relative(path.resolve(root), path.resolve(filePath));
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

function normalizeArtifacts(data) {
  if (Array.isArray(data.stages)) {
    return data.stages.flatMap((stage) => {
      const stageId = stage.stage_id || stage.id || stage.name || '';
      const required = Array.isArray(stage.required_artifacts) ? stage.required_artifacts : [];
      const optional = Array.isArray(stage.optional_artifacts) ? stage.optional_artifacts : [];
      return [
        ...required.map((item) => normalizeArtifactItem({ ...item, stage_id: item.stage_id || stageId, required: true })),
        ...optional.map((item) => normalizeArtifactItem({ ...item, stage_id: item.stage_id || stageId, required: false }))
      ];
    });
  }
  const raw = data.artifacts || data.items || data.stage_artifacts || [];
  if (Array.isArray(raw)) {
    return raw.map(normalizeArtifactItem);
  }
  if (typeof raw === 'object' && raw) {
    return Object.entries(raw).flatMap(([stageId, items]) => {
      if (!Array.isArray(items)) {
        return [];
      }
      return items.map((item) => normalizeArtifactItem({ ...item, stage_id: item.stage_id || stageId }));
    });
  }
  return [];
}

function normalizeArtifactItem(item) {
  return {
    ...item,
    id: item.id || item.name || item.path || item.file || item.workspace_path || `${item.stage_id || 'artifact'}-${Math.random()}`,
    label: item.label || item.name || path.basename(String(item.path || item.file || item.workspace_path || 'artifact')),
    path: item.path || item.file || item.workspace_path || '',
    stage_id: item.stage_id || item.stage || item.stageId || '',
    type: item.type || inferType(item.path || item.file || item.workspace_path),
    required: Boolean(item.required || item.kind === 'required'),
    exists: item.exists !== false
  };
}

function normalizeCampaignStatus(details) {
  if (!details || details.ok === false) {
    return 'unknown';
  }
  if (details.status && details.status.state) {
    return details.status.state;
  }
  if (typeof details.status === 'string') {
    return details.status;
  }
  const stages = Array.isArray(details.stages) ? details.stages : [];
  if (!stages.length) {
    return 'draft';
  }
  if (stages.some((stage) => stage.status === 'failed')) {
    return 'failed';
  }
  if (stages.every((stage) => stage.status === 'completed')) {
    return 'completed';
  }
  if (stages.some((stage) => stage.status === 'running')) {
    return 'running';
  }
  return 'planned';
}

function inferType(value) {
  return path.extname(String(value || '')).replace('.', '').toLowerCase() || 'artifact';
}

function isImage(ext) {
  return ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'].includes(ext);
}

function isPreviewableText(ext) {
  return ['', '.txt', '.md', '.markdown', '.json', '.yaml', '.yml', '.tex', '.log', '.py', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.csv'].includes(ext);
}

function previewKind(ext) {
  if (ext === '.json') return 'json';
  if (ext === '.md' || ext === '.markdown') return 'markdown';
  if (ext === '.yaml' || ext === '.yml') return 'yaml';
  if (ext === '.tex') return 'tex';
  if (ext === '.log') return 'log';
  return 'text';
}

function formatTextPreview(content, ext) {
  if (ext === '.json') {
    try {
      return JSON.stringify(JSON.parse(content), null, 2);
    } catch (_) {
      return content;
    }
  }
  return content;
}

function defaultRunSummary() {
  return { runs: [], latestRun: null, feedback: [] };
}

function summarizeCampaignWorkspace(workspace, events) {
  const summary = summarizeCampaignEvents(events || []);
  const execution = workspace.execution || {};
  if (execution.latest_attempt) {
    summary.latestRun = {
      ...(summary.latestRun || {}),
      ...execution.latest_attempt,
      run_id: execution.latest_attempt.run_id || execution.latest_attempt.execution_id
    };
  }
  summary.feedback = Array.isArray(workspace.feedback) ? workspace.feedback : summary.feedback;
  return summary;
}

function summarizeCampaignEvents(events) {
  const runs = new Map();
  const feedback = [];
  for (const event of events || []) {
    const type = String(event.type || '');
    const payload = event.payload || {};
    if (type === 'RunStarted' || type === 'CampaignExecutionStarted') {
      const runId = String(payload.run_id || payload.execution_id || event.id || '');
      if (!runId) {
        continue;
      }
      const current = runs.get(runId) || { run_id: runId };
      runs.set(runId, {
        ...current,
        run_id: runId,
        status: 'running',
        pid: payload.pid || current.pid || null,
        graph_version: payload.graph_version || current.graph_version || null,
        command: payload.command || current.command || null,
        started_at: event.created_at || current.started_at || null,
        updated_at: event.created_at || current.updated_at || null
      });
    } else if (type === 'RunExited' || type === 'CampaignExecutionCompleted' || type === 'CampaignExecutionFailed') {
      const runId = String(payload.run_id || payload.execution_id || event.id || '');
      if (!runId) {
        continue;
      }
      const current = runs.get(runId) || { run_id: runId };
      runs.set(runId, {
        ...current,
        run_id: runId,
        status: String(payload.status || (payload.exit_code === 0 ? 'completed' : 'failed')),
        exit_code: payload.exit_code,
        exited_at: event.created_at || current.exited_at || null,
        updated_at: event.created_at || current.updated_at || null
      });
    } else if (type === 'InstructionSent' || type === 'HumanFeedbackRecorded') {
      feedback.push({
        id: payload.message_id || payload.feedback_id || event.id,
        text: payload.text || '',
        type: payload.type || payload.feedback_type || 'feedback',
        run_id: payload.run_id || payload.execution_id || null,
        node_id: (payload.metadata && payload.metadata.node_id) || null,
        direction: payload.direction || null,
        created_at: event.created_at || null,
        actor: event.actor || null
      });
    }
  }
  const runList = Array.from(runs.values()).sort((left, right) => String(right.updated_at || '').localeCompare(String(left.updated_at || '')));
  return {
    runs: runList,
    latestRun: runList[0] || null,
    feedback: feedback.sort((left, right) => String(right.created_at || '').localeCompare(String(left.created_at || '')))
  };
}

function unwrap(result, label) {
  if (result.ok) {
    return result.data || {};
  }
  return { ok: false, label, error: result.error, stdout: result.stdout, stderr: result.stderr };
}

async function runJson(root, args) {
  const result = await runMsc(root, args);
  if (!result.ok) {
    return { ...result, errors: [commandError(args, result)] };
  }
  try {
    return { ok: true, data: JSON.parse(result.stdout), errors: [] };
  } catch (error) {
    return { ok: false, stdout: result.stdout, stderr: result.stderr, error: String(error), errors: [commandError(args, { ...result, error: String(error) })] };
  }
}

async function runText(root, args) {
  const result = await runMsc(root, args);
  if (!result.ok) {
    return { ...result, errors: [commandError(args, result)] };
  }
  return { ...result, errors: [] };
}

function commandError(args, result) {
  return {
    command: ['msc', ...args].join(' '),
    message: result.error || result.stderr || 'Command failed',
    code: result.code || null
  };
}

function runMsc(root, args) {
  const bin = resolveMscBin(root);
  return new Promise((resolve) => {
    childProcess.execFile(
      bin,
      ['--no-banner', ...args],
      { cwd: root, timeout: 15000, maxBuffer: 1024 * 1024, env: runtimeEnv(root) },
      (error, stdout, stderr) => {
        if (error) {
          resolve({ ok: false, code: error.code, error: error.message, stdout: stdout || '', stderr: stderr || '' });
          return;
        }
        resolve({ ok: true, stdout: stdout || '', stderr: stderr || '' });
      }
    );
  });
}

async function startCampaignExecution(session, message) {
  if (session.activeProcess) {
    setActionError(session, 'Campaign execution is already active in this dashboard.');
    return;
  }

  const options = normalizeRunOptions(message);
  if (session.state.selectedCampaign) {
    options.campaignId = String(session.state.selectedCampaign);
    options.campaignGraphVersion = session.state.campaignGraph && session.state.campaignGraph.version;
  }
  const validationError = validateRunOptions(options);
  if (validationError) {
    setActionError(session, validationError);
    return;
  }

  const bin = resolveMscBin(session.root);
  const args = buildRunArgs(options, session.root);
  const command = [bin, ...args].join(' ');
  session.state.activeRun = {
    status: 'running',
    startedAt: new Date().toISOString(),
    exitedAt: null,
    exitCode: null,
    command,
    pid: null,
    dryRun: options.dryRun,
    campaign: session.state.selectedCampaign || null
  };
  session.state.runLog = [];
  session.state.actionError = null;
  appendRunLog(session, 'system', '$ ' + command);

  const proc = childProcess.spawn(bin, args, { cwd: session.root, env: runtimeEnv(session.root), shell: false });
  session.activeProcess = proc;
  session.state.activeRun.pid = proc.pid || null;
  postState(session);
  startSteeringPolling(session);

  proc.stdout.on('data', (chunk) => appendRunLog(session, 'stdout', chunk.toString()));
  proc.stderr.on('data', (chunk) => appendRunLog(session, 'stderr', chunk.toString()));
  proc.on('error', (error) => appendRunLog(session, 'stderr', error.message || String(error)));
  proc.on('exit', async (code, signal) => {
    session.activeProcess = null;
    if (session.stopTimer) {
      clearTimeout(session.stopTimer);
      session.stopTimer = null;
    }
    stopSteeringPolling(session);
    session.state.activeRun = {
      ...session.state.activeRun,
      status: code === 0 ? 'exited' : 'failed',
      exitedAt: new Date().toISOString(),
      exitCode: code,
      signal: signal || null
    };
    appendRunLog(session, 'system', `Process exited with code ${code == null ? 'null' : code}${signal ? ` (${signal})` : ''}.`);
    await refresh(session);
  });
}

function normalizeRunOptions(message) {
  return {
    task: String(message.task || '').trim(),
    dryRun: message.dryRun !== false,
    tier: oneOf(message.tier, ['live-smoke', 'budget', 'light', 'medium', 'pro', 'max', 'ultra'], 'budget'),
    outputFormat: oneOf(message.outputFormat, ['markdown', 'latex'], 'markdown'),
    budget: normalizeBudget(message.budget),
    counsel: Boolean(message.counsel),
    math: Boolean(message.math),
    treeSearch: Boolean(message.treeSearch),
    allowSpend: Boolean(message.allowSpend),
    confirmation: String(message.confirmation || '').trim(),
    campaignId: message.campaignId ? String(message.campaignId) : null,
    campaignGraphVersion: message.campaignGraphVersion || null
  };
}

function validateRunOptions(options) {
  if (!options.task) {
    return 'The campaign goal is required before starting execution.';
  }
  if (!Number.isInteger(options.budget) || options.budget < 1 || options.budget > 10000) {
    return 'Budget must be an integer between 1 and 10000.';
  }
  if (!options.dryRun && (!options.allowSpend || options.confirmation !== RUN_CONFIRMATION)) {
    return `Real local execution requires allow spend plus confirmation text ${RUN_CONFIRMATION}.`;
  }
  return null;
}

function buildRunArgs(options, root) {
  const args = [
    '--no-banner',
    'run',
    '--mode',
    'local',
    '--tier',
    options.tier,
    '--output-format',
    options.outputFormat,
    '--budget',
    String(options.budget),
    options.counsel ? '--counsel' : '--no-counsel',
    options.math ? '--math' : '--no-math',
    options.treeSearch ? '--tree-search' : '--no-tree-search'
  ];
  if (options.campaignId) {
    args.push('--campaign-id', String(options.campaignId));
    if (root) {
      args.push('--campaign-root', String(root));
    }
    if (options.campaignGraphVersion) {
      args.push('--campaign-graph-version', String(options.campaignGraphVersion));
    }
  }
  if (options.dryRun) {
    args.push('--dry-run');
  }
  args.push(options.task);
  return args;
}

function stopCampaignExecution(session) {
  if (!session.activeProcess) {
    setActionError(session, 'No active campaign execution to stop.');
    return;
  }
  appendRunLog(session, 'system', 'Stop requested: sending SIGINT.');
  session.state.activeRun = { ...session.state.activeRun, status: 'stopping' };
  postState(session);
  session.activeProcess.kill('SIGINT');
  session.stopTimer = setTimeout(() => {
    if (session.activeProcess) {
      appendRunLog(session, 'system', 'Campaign execution still active after SIGINT; sending SIGTERM.');
      session.activeProcess.kill('SIGTERM');
    }
  }, 5000);
}

async function interruptRun(session) {
  if (!session.activeProcess) {
    setActionError(session, 'Start campaign execution before sending steering commands.');
    return;
  }
  const result = await steeringRequest('POST', '/interrupt');
  if (!result.ok) {
    updateSteering(session, false, 0, false, result.error);
    return;
  }
  appendRunLog(session, 'system', 'Steering interrupt sent.');
  await pollSteering(session);
}

async function sendInstruction(session, message) {
  if (!session.activeProcess) {
    setActionError(session, 'Start campaign execution before sending steering instructions.');
    return;
  }
  const text = String(message.text || '').trim();
  if (!text) {
    setActionError(session, 'Enter a steering instruction first.');
    return;
  }
  const instructionType = oneOf(message.instructionType, ['m', 'n'], 'm');
  const result = await steeringRequest('POST', '/instruction', { text, type: instructionType });
  if (!result.ok) {
    updateSteering(session, false, 0, false, result.error);
    return;
  }
  appendRunLog(session, 'system', 'Steering instruction sent.');
  await pollSteering(session);
}

async function submitFeedback(session, message) {
  if (!session.state.selectedCampaign) {
    setActionError(session, 'Select a campaign before recording feedback.');
    return;
  }
  const text = String(message.text || '').trim();
  if (!text) {
    setActionError(session, 'Enter feedback before recording it.');
    return;
  }
  const args = [
    'campaigns',
    '--root',
    session.root,
    'feedback',
    session.state.selectedCampaign,
    '--text',
    text,
    '--type',
    oneOf(message.feedbackType, ['feedback', 'revision', 'question', 'approval_note'], 'feedback'),
    '--json'
  ];
  const nodeId = String(message.nodeId || '').trim();
  const artifactId = String(message.artifactId || '').trim();
  const artifactPath = String(message.artifactPath || '').trim();
  const decisionId = String(message.decisionId || '').trim();
  const runId = String(message.runId || '').trim();
  if (nodeId) {
    args.push('--node', nodeId);
  }
  if (artifactId) {
    args.push('--artifact-id', artifactId);
  }
  if (artifactPath) {
    args.push('--artifact-path', artifactPath);
  }
  if (decisionId) {
    args.push('--decision-id', decisionId);
  }
  if (runId) {
    args.push('--run-id', runId);
  }
  const result = await runJson(session.root, args);
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Feedback could not be recorded.');
    return;
  }
  session.state.actionError = null;
  Object.assign(session.state, await loadCampaignWorkspace(session.root, session.state.selectedCampaign));
  postState(session);
}

function hydrateOpenClaudeHistory(session, campaignRef, options = {}) {
  const current = session.state.openClaude || defaultOpenClaudeState();
  const sameCampaign = current.campaignRef === campaignRef;
  const base = sameCampaign
    ? current
    : {
        ...defaultOpenClaudeState(),
        model: current.model || defaultOpenClaudeState().model,
        models: current.models || []
      };
  const transcript = (options.replace || !sameCampaign || !current.historyLoaded)
    ? loadOpenClaudeHistory(session.root, campaignRef)
    : (current.transcript || []);
  session.state.openClaude = {
    ...base,
    campaignRef,
    chatStoragePath: openClaudeChatPath(session.root, campaignRef),
    historyLoaded: true,
    transcript
  };
}

function openClaudeChatPath(root, campaignRef) {
  return path.join(root, '.msc', 'openclaude_chats', `${safeChatName(campaignRef)}.json`);
}

function safeChatName(value) {
  const raw = String(value || 'campaign');
  const basename = path.basename(raw).replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'campaign';
  const hash = crypto.createHash('sha1').update(raw).digest('hex').slice(0, 10);
  return `${basename.slice(0, 70)}-${hash}`;
}

function loadOpenClaudeHistory(root, campaignRef) {
  const filePath = openClaudeChatPath(root, campaignRef);
  if (!fs.existsSync(filePath)) {
    return [];
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    const transcript = Array.isArray(parsed) ? parsed : parsed.transcript;
    return sanitizeChatMessages(transcript || []).map((item) => ({ ...item, streaming: false }));
  } catch (_) {
    return [];
  }
}

function saveOpenClaudeHistory(session) {
  const campaignRef = session.state.selectedCampaign || session.state.openClaude?.campaignRef;
  if (!campaignRef) {
    return;
  }
  const current = session.state.openClaude || defaultOpenClaudeState();
  const transcript = sanitizeChatMessages(current.transcript || []);
  const filePath = current.chatStoragePath || openClaudeChatPath(session.root, campaignRef);
  try {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, JSON.stringify({
      schema: 'msc.openclaude.chat_history.v1',
      campaign: campaignRef,
      updated_at: new Date().toISOString(),
      transcript
    }, null, 2), 'utf8');
    session.state.openClaude = {
      ...current,
      chatStoragePath: filePath,
      historyLoaded: true,
      transcript
    };
  } catch (error) {
    appendOpenClaudeAction(session, {
      kind: 'history',
      status: 'failed',
      text: `Chat history could not be saved: ${error.message || String(error)}`,
      timestamp: new Date().toISOString()
    });
  }
}

function clearOpenClaudeHistory(session) {
  if (!session.state.selectedCampaign) {
    setActionError(session, 'Select a campaign before clearing chat history.');
    return;
  }
  const filePath = openClaudeChatPath(session.root, session.state.selectedCampaign);
  try {
    fs.rmSync(filePath, { force: true });
  } catch (_) {}
  session.state.openClaude = {
    ...(session.state.openClaude || defaultOpenClaudeState()),
    campaignRef: session.state.selectedCampaign,
    chatStoragePath: filePath,
    historyLoaded: true,
    transcript: [],
    error: null
  };
  postState(session);
}

async function startOpenClaudeSession(session, message = {}) {
  if (!session.state.selectedCampaign) {
    setActionError(session, 'Select a campaign before starting OpenClaude.');
    return;
  }
  hydrateOpenClaudeHistory(session, session.state.selectedCampaign);
  const model = String(message.model || session.state.openClaude?.model || session.state.diagnostics?.openclaude?.model || 'openai/gpt-5-mini').trim();
  const contextPack = await loadOpenClaudeContextPack(session, model);
  const existingTranscript = session.state.openClaude?.transcript || [];
  const transcript = existingTranscript.length ? existingTranscript : appendChatMessage(existingTranscript, {
    role: 'system',
    text: `OpenClaude is ready for ${session.state.selectedCampaign}. Ask it to inspect, steer, rerun, reroute, or review produced artifacts.`,
    timestamp: new Date().toISOString()
  });
  session.state.openClaude = {
    ...defaultOpenClaudeState(),
    ...(session.state.openClaude || {}),
    status: 'ready',
    model,
    error: null,
    contextPack,
    contextLinks: contextPack?.active_context_links || session.state.openClaudeContextLinks || [],
    lastStartedAt: new Date().toISOString(),
    transcript
  };
  saveOpenClaudeHistory(session);
  postState(session);
}

function stopOpenClaudeSession(session) {
  if (session.openClaudeProcess) {
    session.openClaudeProcess.kill('SIGTERM');
    session.openClaudeProcess = null;
  }
  session.state.openClaude = {
    ...(session.state.openClaude || defaultOpenClaudeState()),
    status: 'stopped',
    error: null
  };
  finalizeStreamingAssistantMessage(session);
  saveOpenClaudeHistory(session);
  postState(session);
}

async function restartOpenClaudeWithModel(session, message) {
  stopOpenClaudeSession(session);
  session.state.openClaude = {
    ...(session.state.openClaude || defaultOpenClaudeState()),
    model: String(message.model || '').trim() || 'openai/gpt-5-mini',
    transcript: appendChatMessage(session.state.openClaude?.transcript || [], {
      role: 'system',
      text: `Model changed to ${String(message.model || '').trim() || 'openai/gpt-5-mini'}. Session context will be refreshed on the next message.`,
      timestamp: new Date().toISOString()
    })
  };
  saveOpenClaudeHistory(session);
  await startOpenClaudeSession(session, { model: session.state.openClaude.model });
}

async function sendOpenClaudeMessage(session, message) {
  if (!session.state.selectedCampaign) {
    setActionError(session, 'Select a campaign before chatting with OpenClaude.');
    return;
  }
  const text = String(message.text || '').trim();
  if (!text) {
    setActionError(session, 'Enter a message for OpenClaude first.');
    return;
  }
  if (session.openClaudeProcess) {
    setActionError(session, 'OpenClaude is already responding.');
    return;
  }
  const model = String(message.model || session.state.openClaude?.model || 'openai/gpt-5-mini').trim();
  hydrateOpenClaudeHistory(session, session.state.selectedCampaign);
  session.state.openClaude = {
    ...(session.state.openClaude || defaultOpenClaudeState()),
    status: 'responding',
    model,
    error: null,
    transcript: appendChatMessage(session.state.openClaude?.transcript || [], { role: 'user', text, timestamp: new Date().toISOString() })
  };
  appendOpenClaudeAction(session, { kind: 'chat', status: 'queued', text: 'Message received by extension backend.', timestamp: new Date().toISOString() });
  saveOpenClaudeHistory(session);
  postState(session);

  const contextPack = await loadOpenClaudeContextPack(session, model);
  session.state.openClaude = {
    ...(session.state.openClaude || defaultOpenClaudeState()),
    contextPack,
    contextLinks: contextPack?.active_context_links || session.state.openClaudeContextLinks || []
  };
  postState(session);

  const prompt = buildOpenClaudePrompt(session, text, contextPack);
  const args = [
    '--no-banner',
    'openclaude',
    '--model',
    model,
    'launch',
    '--execute',
    '--',
    '-p',
    '--verbose',
    '--output-format',
    'stream-json',
    '--include-partial-messages',
    prompt
  ];
  appendOpenClaudeAction(session, { kind: 'command', status: 'started', text: `msc ${args.join(' ')}`, timestamp: new Date().toISOString() });
  const proc = childProcess.spawn(resolveMscBin(session.root), args, { cwd: session.root, env: runtimeEnv(session.root), shell: false });
  session.openClaudeProcess = proc;
  let stdoutBuffer = '';
  let stderrBuffer = '';
  let assistantText = '';
  proc.stdout.on('data', (chunk) => {
    stdoutBuffer += chunk.toString();
    const parsed = consumeJsonLines(stdoutBuffer);
    stdoutBuffer = parsed.remainder;
    for (const item of parsed.items) {
      const textPart = textFromOpenClaudeEvent(item, Boolean(assistantText));
      if (textPart) {
        assistantText += textPart;
        updateStreamingAssistantMessage(session, assistantText);
      }
      const action = actionFromOpenClaudeEvent(item);
      if (action) {
        appendOpenClaudeAction(session, action);
      }
    }
  });
  proc.stderr.on('data', (chunk) => {
    const textChunk = chunk.toString();
    stderrBuffer += textChunk;
    appendOpenClaudeAction(session, { kind: 'stderr', status: 'running', text: textChunk, timestamp: new Date().toISOString() });
  });
  proc.on('error', (error) => {
    session.state.openClaude = {
      ...(session.state.openClaude || defaultOpenClaudeState()),
      status: 'error',
      error: error.message || String(error)
    };
    session.state.openClaude.transcript = appendChatMessage(session.state.openClaude?.transcript || [], {
      role: 'system',
      text: session.state.openClaude.error,
      timestamp: new Date().toISOString()
    });
    finalizeStreamingAssistantMessage(session);
    saveOpenClaudeHistory(session);
    postState(session);
  });
  proc.on('exit', async (code, signal) => {
    session.openClaudeProcess = null;
    if (!assistantText && stdoutBuffer.trim()) {
      assistantText = stdoutBuffer.trim();
      updateStreamingAssistantMessage(session, assistantText);
    }
    finalizeStreamingAssistantMessage(session);
    session.state.openClaude = {
      ...(session.state.openClaude || defaultOpenClaudeState()),
      status: code === 0 ? 'ready' : 'error',
      error: code === 0 ? null : `OpenClaude exited with code ${code == null ? 'null' : code}${signal ? ` (${signal})` : ''}.`
    };
    if (code !== 0 && !assistantText) {
      session.state.openClaude.transcript = appendChatMessage(session.state.openClaude?.transcript || [], {
        role: 'system',
        text: [
          session.state.openClaude.error || 'OpenClaude exited before returning a response.',
          stderrBuffer.trim() ? `stderr:\n${stderrBuffer.trim().slice(0, 1800)}` : ''
        ].filter(Boolean).join('\n\n'),
        timestamp: new Date().toISOString()
      });
    }
    appendOpenClaudeAction(session, { kind: 'command', status: code === 0 ? 'completed' : 'failed', text: `OpenClaude exited with code ${code == null ? 'null' : code}.`, timestamp: new Date().toISOString() });
    Object.assign(session.state, await loadCampaignWorkspace(session.root, session.state.selectedCampaign));
    saveOpenClaudeHistory(session);
    postState(session);
  });
}

async function linkArtifactContext(session, message) {
  if (!session.state.selectedCampaign) {
    setActionError(session, 'Select a campaign before linking context.');
    return;
  }
  const artifact = message.artifact || {};
  const note = String(message.note || `Use ${artifact.label || artifact.path || 'this artifact'} as context.`).trim();
  const args = [
    'campaigns',
    '--root',
    session.root,
    'context',
    'link',
    session.state.selectedCampaign,
    '--note',
    note,
    '--scope',
    'artifact',
    '--json'
  ];
  if (artifact.id) args.push('--artifact-id', String(artifact.id));
  if (artifact.path) args.push('--artifact-path', String(artifact.path));
  if (artifact.stage_id) args.push('--node', String(artifact.stage_id));
  const result = await runJson(session.root, args);
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Artifact context could not be linked.');
    return;
  }
  Object.assign(session.state, await loadCampaignWorkspace(session.root, session.state.selectedCampaign));
  const contextPack = await loadOpenClaudeContextPack(session, session.state.openClaude?.model);
  session.state.openClaude = {
    ...(session.state.openClaude || defaultOpenClaudeState()),
    contextPack,
    contextLinks: contextPack?.active_context_links || [],
    transcript: appendChatMessage(session.state.openClaude?.transcript || [], {
      role: 'system',
      text: `Linked ${artifact.label || artifact.path || 'artifact'} for OpenClaude context.`,
      timestamp: new Date().toISOString()
    })
  };
  saveOpenClaudeHistory(session);
  postState(session);
}

async function updateContextLink(session, message) {
  if (!session.state.selectedCampaign || !message.linkId) {
    setActionError(session, 'Select a campaign and context link first.');
    return;
  }
  const result = await runJson(session.root, [
    'campaigns',
    '--root',
    session.root,
    'context',
    'update',
    session.state.selectedCampaign,
    String(message.linkId),
    '--status',
    oneOf(message.status, ['active', 'resolved', 'superseded', 'ignored'], 'resolved'),
    '--json'
  ]);
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Context link could not be updated.');
    return;
  }
  Object.assign(session.state, await loadCampaignWorkspace(session.root, session.state.selectedCampaign));
  postState(session);
}

async function loadOpenClaudeContextPack(session, model) {
  const result = await runJson(session.root, ['openclaude', '--model', String(model || 'openai/gpt-5-mini'), 'context-pack', session.state.selectedCampaign, '--json']);
  if (!result.ok) {
    appendOpenClaudeAction(session, { kind: 'context', status: 'failed', text: result.error || result.stderr || 'Context pack failed.', timestamp: new Date().toISOString() });
    return null;
  }
  return result.data;
}

function buildOpenClaudePrompt(session, userText, contextPack) {
  const contextJson = JSON.stringify(compactOpenClaudeContext(contextPack), null, 2);
  const history = chatHistoryForPrompt(session.state.openClaude?.transcript || [], userText);
  return [
    `Campaign: ${session.state.selectedCampaign}`,
    `Researcher message: ${userText}`,
    '',
    'Recent chat history:',
    history || 'No prior chat history for this campaign.',
    '',
    'Use the MSc SDK/CLI as the campaign authority. You may autonomously inspect and mutate campaign state through public `msc` commands, including feedback, context links, reruns, reroutes, approvals, and campaign continuation. Do not edit product truth directly, delete campaigns/artifacts, edit repo code, scrape SQLite, or bypass budget limits.',
    '',
    'Current compact campaign context:',
    contextJson
  ].join('\n');
}

function compactOpenClaudeContext(contextPack) {
  if (!contextPack) {
    return {};
  }
  return {
    schema: contextPack.schema,
    campaign: contextPack.campaign,
    execution: contextPack.execution,
    safe_next_actions: contextPack.safe_next_actions,
    current_stage: contextPack.current_stage,
    pending_decisions: contextPack.pending_decisions,
    active_context_links: contextPack.active_context_links,
    recent_feedback: contextPack.recent_feedback,
    selected_artifacts: contextPack.selected_artifacts
  };
}

function chatHistoryForPrompt(messages, currentUserText = '') {
  const clean = sanitizeChatMessages(messages).filter((item) => ['user', 'assistant'].includes(item.role));
  const current = String(currentUserText || '').trim();
  const history = current && clean.length && clean[clean.length - 1].role === 'user' && clean[clean.length - 1].text.trim() === current
    ? clean.slice(0, -1)
    : clean;
  return history.slice(-MAX_PROMPT_HISTORY_MESSAGES).map((item) => {
    const role = item.role === 'assistant' ? 'OpenClaude' : 'Researcher';
    return `${role}: ${truncateForPrompt(item.text, MAX_PROMPT_HISTORY_CHARS)}`;
  }).join('\n\n');
}

function truncateForPrompt(value, maxLength) {
  const text = String(value || '');
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 24)}\n[truncated for prompt]`;
}

function sanitizeChatMessages(messages) {
  if (!Array.isArray(messages)) {
    return [];
  }
  return messages
    .filter((item) => item && typeof item === 'object')
    .map((item) => ({
      id: String(item.id || `${item.role || 'message'}-${item.timestamp || Date.now()}`),
      role: oneOf(item.role, ['user', 'assistant', 'system'], 'system'),
      text: String(item.text || '').slice(0, 50000),
      timestamp: item.timestamp || new Date().toISOString(),
      streaming: Boolean(item.streaming)
    }))
    .filter((item) => item.text.trim())
    .slice(-MAX_CHAT_MESSAGES);
}

function appendChatMessage(messages, item) {
  return [...messages, { id: item.id || `${item.role}-${Date.now()}-${Math.random()}`, ...item }].slice(-MAX_CHAT_MESSAGES);
}

function updateStreamingAssistantMessage(session, text) {
  const messages = session.state.openClaude?.transcript || [];
  const last = messages[messages.length - 1];
  if (last && last.role === 'assistant' && last.streaming) {
    last.text = text;
    last.timestamp = new Date().toISOString();
    session.state.openClaude.transcript = [...messages];
  } else {
    session.state.openClaude.transcript = appendChatMessage(messages, {
      role: 'assistant',
      text,
      streaming: true,
      timestamp: new Date().toISOString()
    });
  }
  saveOpenClaudeHistory(session);
  postState(session);
}

function finalizeStreamingAssistantMessage(session) {
  const messages = session.state.openClaude?.transcript || [];
  const last = messages[messages.length - 1];
  if (last && last.role === 'assistant' && last.streaming) {
    last.streaming = false;
    last.timestamp = new Date().toISOString();
    session.state.openClaude.transcript = [...messages];
  }
}

function appendOpenClaudeAction(session, action) {
  const current = session.state.openClaude || defaultOpenClaudeState();
  session.state.openClaude = {
    ...current,
    actions: [...(current.actions || []), action].slice(-80)
  };
  postState(session);
}

function consumeJsonLines(buffer) {
  const lines = buffer.split(/\r?\n/);
  const remainder = lines.pop() || '';
  const items = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      items.push(JSON.parse(trimmed));
    } catch (_) {
      items.push({ type: 'text', text: trimmed });
    }
  }
  return { items, remainder };
}

function textFromOpenClaudeEvent(event, hasStreamingText = false) {
  if (!event || typeof event !== 'object') return '';
  if (event.type === 'stream_event' && event.event) {
    return textFromOpenClaudeEvent(event.event, hasStreamingText);
  }
  if (typeof event.text === 'string') return event.text;
  if (typeof event.content === 'string') return event.content;
  if (typeof event.delta === 'string') return event.delta;
  if (event.type === 'content_block_delta' && event.delta?.text) return event.delta.text;
  if (event.type === 'assistant' && event.message?.content) return hasStreamingText ? '' : contentToText(event.message.content);
  if (event.type === 'message' && event.message?.role === 'assistant') return hasStreamingText ? '' : contentToText(event.message.content);
  if (event.type === 'result' && event.result) return hasStreamingText ? '' : (typeof event.result === 'string' ? event.result : JSON.stringify(event.result));
  return '';
}

function contentToText(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map((item) => typeof item === 'string' ? item : item.text || '').join('');
  }
  return '';
}

function actionFromOpenClaudeEvent(event) {
  if (!event || typeof event !== 'object') return null;
  const name = event.tool_name || event.name || event.tool;
  if (!name && !String(event.type || '').includes('tool')) return null;
  return {
    kind: 'tool',
    status: event.type || 'tool',
    text: name ? `${name}` : JSON.stringify(event).slice(0, 500),
    timestamp: new Date().toISOString()
  };
}

function startSteeringPolling(session) {
  stopSteeringPolling(session);
  pollSteering(session);
  session.steeringTimer = setInterval(() => pollSteering(session), 3000);
}

function stopSteeringPolling(session) {
  if (session.steeringTimer) {
    clearInterval(session.steeringTimer);
    session.steeringTimer = null;
  }
}

async function pollSteering(session) {
  if (!session.activeProcess) {
    updateSteering(session, false, 0, false, null);
    return;
  }
  const result = await steeringRequest('GET', '/status');
  if (!result.ok) {
    updateSteering(session, false, 0, false, result.error);
    return;
  }
  updateSteering(session, true, Number(result.data.queue_depth || 0), Boolean(result.data.paused), null);
}

function steeringRequest(method, route, payload) {
  const body = payload ? JSON.stringify(payload) : '';
  return new Promise((resolve) => {
    const req = http.request(
      `${STEERING_URL}${route}`,
      {
        method,
        timeout: 2000,
        headers: payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } : undefined
      },
      (res) => {
        let raw = '';
        res.on('data', (chunk) => { raw += chunk.toString(); });
        res.on('end', () => {
          let data = {};
          try {
            data = raw ? JSON.parse(raw) : {};
          } catch (error) {
            resolve({ ok: false, error: `Invalid steering response: ${String(error)}` });
            return;
          }
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
            resolve({ ok: true, data });
          } else {
            resolve({ ok: false, error: data.error || `HTTP ${res.statusCode}` });
          }
        });
      }
    );
    req.on('timeout', () => req.destroy(new Error('Steering server timed out.')));
    req.on('error', (error) => resolve({ ok: false, error: error.message || String(error) }));
    if (body) {
      req.write(body);
    }
    req.end();
  });
}

function updateSteering(session, available, queueDepth, paused, lastError) {
  session.state.steering = { available, queueDepth, paused, lastError };
  postState(session);
}

function appendRunLog(session, stream, text) {
  const lines = String(text).replace(/\r/g, '').split('\n');
  const timestamp = new Date().toISOString();
  for (const line of lines) {
    if (line) {
      session.state.runLog.push({ stream, text: line, timestamp });
    }
  }
  if (session.state.runLog.length > MAX_LOG_LINES) {
    session.state.runLog = session.state.runLog.slice(-MAX_LOG_LINES);
  }
  postState(session);
}

function setActionError(session, message) {
  session.state.actionError = message;
  postState(session);
}

function postState(session) {
  session.state.generatedAt = new Date().toISOString();
  session.panel.webview.postMessage({ type: 'state', state: session.state });
}

function runtimeEnv(root) {
  return { ...process.env, PYTHONPATH: root };
}

function resolveMscBin(root) {
  const local = process.platform === 'win32'
    ? path.join(root, '.venv', 'Scripts', 'msc.exe')
    : path.join(root, '.venv', 'bin', 'msc');
  return fs.existsSync(local) ? local : 'msc';
}

function openRouterConfigured(openclaude, openclaudeEnv) {
  const readiness = unwrap(openclaude, 'openclaude');
  const env = unwrap(openclaudeEnv, 'openclaudeEnv');
  return Boolean(process.env.OPENROUTER_API_KEY || readiness.openrouter_configured || (env.env && env.env.OPENROUTER_API_KEY));
}

function oneOf(value, allowed, fallback) {
  return allowed.includes(value) ? value : fallback;
}

function normalizeBudget(value) {
  const parsed = Number.parseInt(String(value == null ? '20' : value), 10);
  return Number.isFinite(parsed) ? parsed : 20;
}

function renderHtml(context, webview) {
  const distPath = path.join(context.extensionPath, 'webview-ui', 'dist');
  const htmlPath = path.join(distPath, 'index.html');
  if (!fs.existsSync(htmlPath)) {
    return renderMissingBundleHtml(webview);
  }
  const csp = [
    "default-src 'none'",
    `img-src ${webview.cspSource} data:`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `script-src ${webview.cspSource}`,
    `font-src ${webview.cspSource}`,
    `frame-src ${webview.cspSource}`,
    `object-src ${webview.cspSource}`
  ].join('; ');
  return fs.readFileSync(htmlPath, 'utf8')
    .replace(/__CSP__/g, csp)
    .replace(/(src|href)="\/assets\/([^"]+)"/g, (_, attr, asset) => {
      const uri = webview.asWebviewUri(vscode.Uri.file(path.join(distPath, 'assets', asset)));
      return `${attr}="${uri}"`;
    });
}

function renderMissingBundleHtml(webview) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' ${webview.cspSource};">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MSc Campaigns</title>
</head>
<body style="font-family: var(--vscode-font-family); color: var(--vscode-editor-foreground); background: var(--vscode-editor-background); padding: 24px;">
  <h1>MSc Campaigns</h1>
  <p>The webview bundle is missing. Run <code>npm run build --prefix extensions/vscode-msc</code> from the repo root, then reopen the dashboard.</p>
</body>
</html>`;
}

module.exports = {
  activate,
  artifactAllowedRoots,
  buildRunArgs,
  buildOpenClaudePrompt,
  chatHistoryForPrompt,
  collectDashboardData,
  compactOpenClaudeContext,
  consumeJsonLines,
  deactivate,
  findProjectRoot,
  openClaudeChatPath,
  textFromOpenClaudeEvent,
  normalizeRunOptions,
  safeResolveArtifactPath,
  validateRunOptions,
  resolveMscBin
};
