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
const OPENCLAUDE_WATCHDOG_INTERVAL_MS = 15000;
const OPENCLAUDE_STALL_WARN_MS = 180000;
const OPENCLAUDE_STALL_KILL_MS = 300000;
const RUN_CONFIRMATION = 'RUN LOCAL';
const STEERING_URL = 'http://127.0.0.1:5002';
const TEXT_PREVIEW_BYTES = 256 * 1024;

const activeDashboards = new Set();

function openDashboard(context, options = {}) {
  const root = getWorkspaceRoot(context);
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
  if (options.openOnboarding) {
    session.state.onboardingOpen = true;
  }
  if (options.openSetup) {
    session.state.setupOpen = true;
  }
  panel.webview.html = renderHtml(context, panel.webview);

  panel.webview.onDidReceiveMessage(async (message) => {
    if (!message || !message.type) {
      return;
    }
    await handleMessage(session, message);
  }, undefined, context.subscriptions);

  panel.onDidDispose(() => {
    activeDashboards.delete(session);
    cleanupSession(session);
  }, undefined, context.subscriptions);

  activeDashboards.add(session);
  return session;
}

let keyNudgeShown = false;

function activate(context) {
  const openCmd = vscode.commands.registerCommand('mscDashboard.open', () => {
    openDashboard(context);
  });

  const setKeysCmd = vscode.commands.registerCommand('mscDashboard.setKeys', () => {
    // Surface the Setup tab even when the dashboard isn't open yet.
    for (const existing of activeDashboards) {
      existing.state.setupOpen = true;
      existing.state.onboardingOpen = false;
      postState(existing);
      existing.panel.reveal();
      return;
    }
    openDashboard(context, { openSetup: true });
  });

  const openSetupCmd = vscode.commands.registerCommand('mscDashboard.openSetup', () => {
    for (const existing of activeDashboards) {
      existing.state.setupOpen = true;
      existing.state.onboardingOpen = false;
      postState(existing);
      existing.panel.reveal();
      return;
    }
    openDashboard(context, { openSetup: true });
  });

  context.subscriptions.push(openCmd, setKeysCmd, openSetupCmd);

  // Intentionally NOT spawning the CLI here. The activation event is
  // onStartupFinished and VSCode is still bringing up extension hosts;
  // adding our own python3 spawn into that window reliably reproduces
  // spawn EAGAIN on shared HPC nodes. The key-missing nudge runs the
  // first time the dashboard is opened instead.
}

async function maybeNudgeForKeys(session) {
  if (keyNudgeShown) return;
  keyNudgeShown = true;
  try {
    const result = await runJson(session.root, ['config', 'keys', 'list', '--json']);
    if (!result.ok || !result.data) return;
    const openrouter = (result.data.keys || []).find((entry) => entry.env_var === 'OPENROUTER_API_KEY');
    if (!openrouter || openrouter.configured) return;
    const choice = await vscode.window.showWarningMessage(
      'MSc: OpenRouter API key is not configured. The dashboard cannot run campaigns until it is set.',
      'Configure API Keys',
      'Later'
    );
    if (choice === 'Configure API Keys') {
      session.state.onboardingOpen = true;
      postState(session);
      await refreshKeyStatus(session);
    }
  } catch (_) {
    // Silent — the dashboard's own readiness checks will surface the problem.
  }
}

function deactivate() {}

async function handleMessage(session, message) {
  if (message.type === 'ready' || message.type === 'refresh') {
    await refresh(session);
    if (message.type === 'ready') {
      maybeNudgeForKeys(session).catch(() => {});
    }
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
  } else if (message.type === 'openSetup') {
    session.state.setupOpen = true;
    session.state.settingsOpen = false;
    session.state.onboardingOpen = false;
    postState(session);
    await refreshKeyStatus(session);
    await refresh(session);
  } else if (message.type === 'closeSetup') {
    session.state.setupOpen = false;
    postState(session);
  } else if (message.type === 'installOpenClaude') {
    await installOpenClaude(session);
  } else if (message.type === 'openOnboarding') {
    session.state.onboardingOpen = true;
    session.state.settingsOpen = false;
    postState(session);
    await refreshKeyStatus(session);
  } else if (message.type === 'closeOnboarding') {
    session.state.onboardingOpen = false;
    postState(session);
  } else if (message.type === 'refreshKeyStatus') {
    await refreshKeyStatus(session);
  } else if (message.type === 'setApiKey') {
    await setApiKey(session, message);
  } else if (message.type === 'unsetApiKey') {
    await unsetApiKey(session, message);
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
  } else if (message.type === 'openClaudeCancel') {
    cancelOpenClaudeSession(session);
  } else if (message.type === 'openClaudeRestartModel') {
    await restartOpenClaudeWithModel(session, message);
  } else if (message.type === 'openClaudeClearHistory') {
    clearOpenClaudeHistory(session);
  } else if (message.type === 'linkArtifactContext') {
    await linkArtifactContext(session, message);
  } else if (message.type === 'updateContextLink') {
    await updateContextLink(session, message);
  } else if (message.type === 'openStageNewestFile') {
    await openStageNewestFile(session, String(message.stageId || ''));
  } else if (message.type === 'revealStageWorkspace') {
    await revealStageWorkspace(session, String(message.stageId || ''));
  } else if (message.type === 'updateBudgetCap') {
    await updateBudgetCap(session, Number(message.newCap));
  } else if (message.type === 'setPricingDisposition') {
    await setPricingDisposition(session, message);
  } else if (message.type === 'pricingList') {
    await pricingList(session);
  } else if (message.type === 'pricingSet') {
    await pricingSet(session, message);
  } else if (message.type === 'pricingUnset') {
    await pricingUnset(session, message);
  } else if (message.type === 'restartNode') {
    await restartNode(session, message);
  } else if (message.type === 'restartCampaign') {
    await restartCampaign(session, message);
  } else if (message.type === 'resumeFromCouncilDeadlock') {
    await resumeFromCouncilDeadlock(session, message);
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
  if (session.slurmPollTimer) {
    clearTimeout(session.slurmPollTimer);
    session.slurmPollTimer = null;
  }
  if (session.stopTimer) {
    clearTimeout(session.stopTimer);
    session.stopTimer = null;
  }
  // Closing a dashboard panel should not be a destructive run-control action.
  // The explicit Stop button owns process termination; panel disposal only
  // detaches this UI session from further log/steering updates. Detached
  // SLURM runs are owned by the scheduler; nothing to clean up for them.
  session.activeProcess = null;
  if (session.openClaudeProcess) {
    session.openClaudeProcess.kill('SIGTERM');
    session.openClaudeProcess = null;
  }
}

function getWorkspaceRoot(context) {
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
  return findProjectRoot(folder ? folder.uri.fsPath : process.cwd(), context && context.extensionPath);
}

function findProjectRoot(startPath, extensionPath) {
  const start = path.resolve(startPath || process.cwd());
  const candidates = [
    start,
    path.join(start, 'PoggioAI_MSc'),
    path.join(start, 'MSc', 'PoggioAI_MSc'),
    path.dirname(start)
  ];
  if (extensionPath) {
    const extensionRoot = path.resolve(extensionPath);
    candidates.unshift(extensionRoot, path.dirname(path.dirname(extensionRoot)));
  }
  let current = start;
  for (let index = 0; index < 5; index += 1) {
    candidates.push(current);
    candidates.push(path.join(current, 'PoggioAI_MSc'));
    candidates.push(path.join(current, 'MSc', 'PoggioAI_MSc'));
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
    campaignMetadata: {},
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
    setupOpen: false,
    onboardingOpen: false,
    keyStatus: null,
    settings: defaultSettings(root),
    setup: null,
    setupOps: { openclaudeInstall: null },
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
    cliPath: resolveMscCommand(root).label,
    openRouterConfigured: Boolean(process.env.OPENROUTER_API_KEY),
    defaultBudget: 20,
    defaultTier: 'budget',
    defaultOutput: 'markdown',
    localDbPath: path.join(root, '.msc', 'campaigns.db'),
    bundleExportRoot: path.join(root, 'campaigns')
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
  state.setupOpen = previous.setupOpen;
  state.setupOps = previous.setupOps || { openclaudeInstall: null };
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
  const openclaude = await runJson(root, ['openclaude', 'readiness', '--json']);
  const openclaudeModels = await runJson(root, ['openclaude', 'models', '--json']);
  const setupStateResult = await runJson(root, ['project', 'setup-state', '--json']);

  state.settings = {
    ...defaultSettings(root),
    openRouterConfigured: openRouterConfigured(openclaude),
    readiness: unwrap(readiness, 'readiness')
  };
  state.setup = (setupStateResult.ok && setupStateResult.data && setupStateResult.data.setup) || null;
  state.diagnostics = {
    readiness: unwrap(readiness, 'readiness'),
    openclaude: unwrap(openclaude, 'openclaude'),
    openclaudeModels: unwrap(openclaudeModels, 'openclaudeModels'),
    setup: state.setup
  };
  state.errors = [
    ...campaigns.errors,
    ...readiness.errors,
    ...openclaude.errors,
    ...openclaudeModels.errors,
    ...setupStateResult.errors
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

  // OpenClaude is per-campaign: each campaign has its own chat transcript on
  // disk and its own context pack. If an Overseer turn from the previously-
  // selected campaign is still streaming, kill it before switching — otherwise
  // chunks from the old subprocess will land in the new campaign's transcript
  // (saveOpenClaudeHistory reads session.state.selectedCampaign at flush time,
  // which has already moved). Killing now produces a clean interruption note in
  // the *old* campaign's transcript via reconcileZombieResponding the next time
  // it's loaded.
  const switchingCampaigns = session.state.selectedCampaign && session.state.selectedCampaign !== campaignRef;
  if (switchingCampaigns && session.openClaudeProcess) {
    try { session.openClaudeProcess.kill('SIGTERM'); } catch (_) {}
    session.openClaudeProcess = null;
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
  // When switching, also stop polling the previous campaign's SLURM run so the
  // poller doesn't compete with the new one we may start.
  if (switchingCampaigns) {
    stopSlurmRunPolling(session);
    session.state.activeRun = null;
    session.state.runLog = [];
  }
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

  // If a SLURM run for this campaign is still live on the backend (extension
  // was closed while the campaign kept running), reattach so the Stop button,
  // steering, and polling all work without needing a fresh submit.
  await reattachLiveSlurmRun(session, campaignRef).catch(() => null);
}

async function reattachLiveSlurmRun(session, campaignRef) {
  if (!campaignRef) return;
  if (session.state.activeRun && session.state.activeRun.slurmRunId) return;
  // `msc hpc status` returns the latest run + its squeue state. If the
  // orchestrator is still queued/running we adopt it; otherwise leave
  // activeRun alone so the user can submit a fresh one.
  const result = await runJson(session.root, [
    'hpc', '--root', session.root, 'status', String(campaignRef), '--json'
  ], { timeout: 30000 });
  if (!result.ok || !result.data) return;
  const data = result.data;
  const orchState = data.orchestrator_state;
  const lastEventType = data.last_event && data.last_event.type;
  const terminalTypes = new Set(['RunFinished', 'RunFailed', 'RunCancelled']);
  const stillLive = !!orchState || (lastEventType && !terminalTypes.has(lastEventType) && orchState !== null);
  // orchState !== null AND a non-terminal last event = healthy live run; or
  // orchState present (PENDING/RUNNING) regardless of last event.
  if (!data.run_id || !stillLive) return;
  session.state.activeRun = {
    status: 'running',
    startedAt: (data.last_event && data.last_event.created_at) || new Date().toISOString(),
    exitedAt: null,
    exitCode: null,
    command: `(reattached) hpc submit ${campaignRef}`,
    pid: null,
    dryRun: false,
    campaign: campaignRef,
    slurmRunId: String(data.run_id),
    orchestratorJobId: data.orchestrator_job_id ? String(data.orchestrator_job_id) : null,
    heartbeatJobId: data.heartbeat_job_id ? String(data.heartbeat_job_id) : null,
    steeringInbox: data.steering_inbox || null,
    workspaceRoot: data.campaign_id || null,
    reattached: true
  };
  appendRunLog(session, 'system',
    `Reattached to running SLURM run ${data.run_id} ` +
    `(orchestrator state: ${orchState || 'unknown'}).`);
  postState(session);
  startSlurmRunPolling(session);
}

async function loadCampaignWorkspace(root, campaignRef) {
  // Workspace JSON includes the full event projection, so it can legitimately
  // run to several MB on mature campaigns. Give it a generous ceiling; other
  // CLI calls keep the 4MB default.
  const workspaceResult = await runJson(
    root,
    ['campaigns', '--root', root, 'workspace', campaignRef, '--json'],
    { maxBuffer: 32 * 1024 * 1024 }
  );
  const workspace = unwrap(workspaceResult, 'campaignWorkspace');
  const diagnostics = workspace.diagnostics || {};
  const campaignEvents = Array.isArray(diagnostics.events) ? diagnostics.events : [];
  const deliverables = Array.isArray(workspace.deliverables) ? workspace.deliverables : [];
  const plannedOutputs = Array.isArray(workspace.planned_outputs) ? workspace.planned_outputs : [];
  const diagnosticArtifacts = Array.isArray(diagnostics.artifacts) ? diagnostics.artifacts : [];
  const allArtifacts = [...deliverables, ...plannedOutputs, ...diagnosticArtifacts];
  const contextLinks = Array.isArray(workspace.context?.links) ? workspace.context.links : [];

  // Live budget snapshot (cap + per-model spend) for the Budget tab. Best-effort.
  const budgetResult = await runJson(
    root, ['campaigns', '--root', root, 'budget', campaignRef, '--json'],
    { timeout: 20000 }
  ).catch(() => ({ ok: false }));
  const campaignBudget = (budgetResult.ok && budgetResult.data) || {};

  // Uncovered models: collect PricingUnknown events that haven't been resolved
  // by a later disposition. The UI surfaces these with [Use suggested] / [Allow $0]
  // / [Skip] buttons that map straight to `hpc set-pricing-disposition`.
  const seenResolved = new Set();
  const uncovered = [];
  for (let i = campaignEvents.length - 1; i >= 0; i--) {
    const ev = campaignEvents[i];
    if (!ev || !ev.type) continue;
    const model = ev.payload && ev.payload.model_id;
    if (!model) continue;
    if (ev.type === 'CampaignMetadataUpdated') {
      const dispositions = (ev.payload && ev.payload.updates && ev.payload.updates.pricing_dispositions) || {};
      Object.keys(dispositions).forEach((m) => seenResolved.add(m));
    } else if (ev.type === 'PricingUnknown' && !seenResolved.has(model)) {
      seenResolved.add(model);
      uncovered.push({
        model_id: model,
        suggestion: ev.payload.suggestion || null,
        ts: ev.created_at,
      });
    }
  }

  // campaignMetadata: replay CampaignMetadataUpdated events in chronological
  // order to reconstruct the current metadata blob. The same pattern the SDK
  // uses for get_campaign_metadata. Surfaces e.g. persona_council_deadlock
  // so DecisionsTab can render the new banner.
  const campaignMetadata = {};
  for (const ev of campaignEvents) {
    if (!ev || ev.type !== 'CampaignMetadataUpdated') continue;
    const updates = (ev.payload && ev.payload.updates) || {};
    for (const [k, v] of Object.entries(updates)) {
      if (v === null || v === undefined) {
        delete campaignMetadata[k];
      } else {
        campaignMetadata[k] = v;
      }
    }
  }

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
    campaignBudget,
    campaignUncoveredModels: uncovered,
    campaignMetadata,
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
  const tier = oneOf(draft.tier, ['scaffold', 'lean', 'standard', 'serious', 'ultra'], 'standard');
  const outputFormat = oneOf(draft.outputFormat, ['markdown', 'latex'], 'markdown');
  const template = oneOf(
    draft.template,
    ['target_research', 'consortium_scaffold', 'consortium_budget', 'literature_only', 'experiment_design', 'blank'],
    'target_research',
  );
  const createArgs = [
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
  ];
  // Persona-council overrides land in campaign metadata so each campaign
  // can have its own consensus / safety-cap behavior independent of the
  // global runner defaults.
  if (draft.personaDebateRounds) {
    createArgs.push('--persona-debate-rounds', String(parseInt(draft.personaDebateRounds, 10) || 3));
  }
  if (draft.personaMaxSynthesisAttempts) {
    createArgs.push('--persona-max-synthesis-attempts',
                    String(parseInt(draft.personaMaxSynthesisAttempts, 10) || 5));
  }
  if (draft.personaDeadlockPolicy && draft.personaDeadlockPolicy !== 'pause') {
    // 'pause' is the default; only forward non-default
    createArgs.push('--persona-deadlock-policy', String(draft.personaDeadlockPolicy));
  }
  createArgs.push('--json');
  const result = await runJson(session.root, createArgs, { timeout: 60000 });
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Campaign creation failed.');
    return;
  }
  const created = result.data && result.data.campaign ? result.data.campaign : result.data;
  await refresh(session);
  await selectCampaign(session, created.campaign_id || created.path || created.name);
  if (draft.autoStart !== false) {
    const started = await startCampaignExecution(session, {
      task: objective,
      dryRun: draft.dryRun !== false,
      tier,
      outputFormat,
      budget,
      model: draft.model,
      maxRunSeconds: draft.maxRunSeconds,
      counsel: Boolean(draft.counsel),
      math: Boolean(draft.math),
      treeSearch: Boolean(draft.treeSearch),
      allowSpend: Boolean(draft.allowSpend),
      confirmation: String(draft.confirmation || '').trim()
    });
    if (!started && !session.state.actionError) {
      setActionError(session, 'Campaign was created, but automatic execution did not start.');
    }
  }
}

async function updateBudgetCap(session, newCap) {
  if (!session.state.selectedCampaign) {
    setActionError(session, 'Select a campaign before adjusting its budget cap.');
    return;
  }
  if (!Number.isFinite(newCap) || newCap <= 0) {
    setActionError(session, 'New cap must be a positive number.');
    return;
  }
  const result = await runJson(session.root, [
    'campaigns', '--root', session.root,
    'update-budget-cap', String(session.state.selectedCampaign), String(newCap),
    '--reason', 'updated via VSCode budget tab', '--json',
  ], { timeout: 30000 });
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Could not update budget cap.');
    return;
  }
  await refresh(session);
}

async function setPricingDisposition(session, message) {
  if (!session.state.selectedCampaign) {
    setActionError(session, 'Select a campaign before resolving pricing.');
    return;
  }
  const modelId = String(message.modelId || '').trim();
  const action = String(message.action || '').trim();
  if (!modelId || !action) {
    setActionError(session, 'Pricing disposition requires modelId and action.');
    return;
  }
  const args = [
    'hpc', '--root', session.root, 'set-pricing-disposition',
    String(session.state.selectedCampaign),
    '--model', modelId,
    '--action', action,
  ];
  if (action === 'set_rate') {
    args.push('--input-per-1k', String(message.inputPer1k));
    args.push('--output-per-1k', String(message.outputPer1k));
  }
  args.push('--json');
  const result = await runJson(session.root, args, { timeout: 30000 });
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Pricing disposition failed.');
    return;
  }
  await refresh(session);
}

async function pricingList(session) {
  const result = await runJson(session.root, [
    'config', 'pricing', 'list', '--json'
  ], { timeout: 20000 });
  session.panel.webview.postMessage({
    type: 'pricingListResult',
    pricing: (result.ok && result.data && result.data.pricing) || {},
  });
}

async function pricingSet(session, message) {
  const model = String(message.model || '').trim();
  const inputPer1k = Number(message.inputPer1k);
  const outputPer1k = Number(message.outputPer1k);
  if (!model || !Number.isFinite(inputPer1k) || !Number.isFinite(outputPer1k)) {
    setActionError(session, 'pricingSet requires model + numeric rates.');
    return;
  }
  const result = await runJson(session.root, [
    'config', 'pricing', 'set', model,
    '--input-per-1k', String(inputPer1k),
    '--output-per-1k', String(outputPer1k),
    '--json',
  ], { timeout: 20000 });
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Could not set pricing.');
    return;
  }
  await pricingList(session);
}

async function pricingUnset(session, message) {
  const model = String(message.model || '').trim();
  if (!model) return;
  const result = await runJson(session.root, [
    'config', 'pricing', 'unset', model, '--json',
  ], { timeout: 20000 });
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Could not remove pricing.');
    return;
  }
  await pricingList(session);
}

async function restartNode(session, message) {
  if (!session.state.selectedCampaign) {
    setActionError(session, 'Select a campaign before restarting a node.');
    return;
  }
  const nodeId = String(message.nodeId || '').trim();
  if (!nodeId) {
    setActionError(session, 'restartNode requires a nodeId.');
    return;
  }
  const reason = String(message.reason || 'user-initiated restart').slice(0, 500);
  appendRunLog(session, 'system', `Restart node requested: ${nodeId} (${reason})`);
  // hpc restart-node does: scancel + rerun_stage audit + sbatch with
  // --start-from-stage. It can take up to ~90s on a loaded login node
  // (two sbatch calls + DB writes + Python startup).
  const result = await runJson(session.root, [
    'hpc', '--root', session.root, 'restart-node',
    String(session.state.selectedCampaign), nodeId,
    '--reason', reason, '--json',
  ], { timeout: 120000 });
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Restart node failed.');
    return;
  }
  appendRunLog(session, 'system',
    `Node ${nodeId} restart submitted. New run: ${result.data?.new_run_id || '?'} ` +
    `(orchestrator job ${result.data?.new_orchestrator_job_id || '?'})`);
  await refresh(session);
}

async function restartCampaign(session, message) {
  if (!session.state.selectedCampaign) {
    setActionError(session, 'Select a campaign before restarting it.');
    return;
  }
  const reason = String(message.reason || 'user-initiated restart').slice(0, 500);
  const archive = message.archive !== false;
  appendRunLog(session, 'system',
    `Restart campaign requested (${reason}; archive=${archive ? 'yes' : 'no'})`);
  const result = await runJson(session.root, [
    'hpc', '--root', session.root, 'restart-campaign',
    String(session.state.selectedCampaign),
    '--reason', reason,
    archive ? '--archive' : '--no-archive',
    '--json',
  ], { timeout: 120000 });
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Restart campaign failed.');
    return;
  }
  appendRunLog(session, 'system',
    `Campaign restart complete. Archived ${result.data?.archived_paths?.length || 0} prior runs. ` +
    `New run: ${result.data?.new_run_id || '?'}`);
  await refresh(session);
}

async function resumeFromCouncilDeadlock(session, message) {
  if (!session.state.selectedCampaign) {
    setActionError(session, 'Select a campaign before resolving a deadlock.');
    return;
  }
  const mode = String(message.mode || '').trim();
  if (mode !== 'accept_as_is' && mode !== 'edited') {
    setActionError(session, "mode must be 'accept_as_is' or 'edited'.");
    return;
  }
  const reason = String(message.reason || '').slice(0, 500);
  appendRunLog(session, 'system',
    `Resolving persona_council deadlock (mode=${mode}${reason ? `, reason="${reason}"` : ''})`);
  const result = await runJson(session.root, [
    'hpc', '--root', session.root, 'resume-from-deadlock',
    String(session.state.selectedCampaign),
    '--mode', mode,
    ...(reason ? ['--reason', reason] : []),
    '--json',
  ], { timeout: 120000 });
  if (!result.ok) {
    setActionError(session, result.error || result.stderr || 'Resume from deadlock failed.');
    return;
  }
  appendRunLog(session, 'system',
    `Deadlock resolved (mode=${mode}). New run: ${result.data?.new_run_id || '?'} ` +
    `orchestrator job ${result.data?.new_orchestrator_job_id || '?'}`);
  await refresh(session);
}

async function deleteCampaignForSession(session, message) {
  const campaignRef = String(message.campaign || session.state.selectedCampaign || '').trim();
  if (!campaignRef) {
    setActionError(session, 'Select a campaign before deleting it.');
    postDeleteCampaignResult(session, { ok: false, campaign: campaignRef, error: session.state.actionError });
    return;
  }
  if (message.confirm !== 'DELETE') {
    setActionError(session, 'Campaign deletion requires confirmation text DELETE.');
    postDeleteCampaignResult(session, { ok: false, campaign: campaignRef, error: session.state.actionError });
    return;
  }
  if (session.activeProcess) {
    setActionError(session, 'Stop the active campaign process before deleting this campaign.');
    postDeleteCampaignResult(session, { ok: false, campaign: campaignRef, error: session.state.actionError });
    return;
  }
  postDeleteCampaignResult(session, { ok: true, status: 'started', campaign: campaignRef });
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
    postDeleteCampaignResult(session, { ok: false, campaign: campaignRef, error: session.state.actionError });
    return;
  }
  if (session.openClaudeProcess) {
    session.openClaudeProcess.kill('SIGTERM');
    session.openClaudeProcess = null;
  }
  const deleted = result.data || {};
  session.state = {
    ...session.state,
    loading: false,
    view: 'home',
    selectedCampaign: null,
    campaignDetails: null,
    campaignWorkspace: null,
    campaignGraph: null,
    campaignArtifacts: [],
    campaignMetadata: {},
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
    campaigns: filterDeletedCampaign(session.state.campaigns || [], campaignRef, deleted.campaign_id),
    actionError: null,
    openClaude: defaultOpenClaudeState()
  };
  postDeleteCampaignResult(session, { ok: true, status: 'completed', campaign: campaignRef, result: deleted });
  postState(session);
}

function postDeleteCampaignResult(session, payload) {
  session.panel.webview.postMessage({ type: 'deleteCampaignResult', ...payload });
}

function filterDeletedCampaign(campaigns, campaignRef, campaignId) {
  const targets = new Set([campaignRef, campaignId].filter(Boolean).map(String));
  return campaigns.filter((campaign) => {
    const refs = [campaign.path, campaign.name, campaign.campaign_id, campaign.id, campaign.title].filter(Boolean).map(String);
    return !refs.some((ref) => targets.has(ref));
  });
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
  const content = buffer.slice(0, TEXT_PREVIEW_BYTES).toString('utf8');
  const kind = previewKind(ext);
  return {
    ...base,
    kind,
    html: renderArtifactHtml(content, ext),
    content: formatTextPreview(content, ext),
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

function resolveStageWorkspaceDir(session, stageId) {
  const trimmed = String(stageId || '').trim();
  if (!trimmed) return null;
  const details = session.state.campaignDetails || {};
  const stages = Array.isArray(details.stages) ? details.stages : [];
  const stage = stages.find((candidate) =>
    candidate && (candidate.stage_id === trimmed || candidate.id === trimmed || candidate.name === trimmed)
  );
  const baseRoots = artifactBaseRoots(session.root, details);
  const stageWorkspace = (stage && stage.workspace) || (details && details.workspace_root);
  const candidates = [];
  const pushAbs = (p) => {
    if (!p) return;
    const r = path.resolve(p);
    if (!candidates.includes(r)) candidates.push(r);
  };
  if (stageWorkspace) {
    for (const base of baseRoots) {
      const ws = path.isAbsolute(stageWorkspace) ? path.resolve(stageWorkspace) : path.resolve(base, stageWorkspace);
      pushAbs(ws);
      // StageRunContext layout: <ws>/runs/<run_id>/<stage_id>
      const runsParent = path.join(ws, 'runs');
      try {
        if (fs.existsSync(runsParent) && fs.statSync(runsParent).isDirectory()) {
          const newestRun = fs.readdirSync(runsParent)
            .map((name) => ({ name, mtime: safeMtime(path.join(runsParent, name)) }))
            .filter((entry) => entry.mtime > 0)
            .sort((a, b) => b.mtime - a.mtime)[0];
          if (newestRun) pushAbs(path.join(runsParent, newestRun.name, trimmed));
        }
      } catch (_) { /* fall through */ }
    }
  }
  // Pick the newest existing dir, preferring StageRunContext run dirs.
  const existing = candidates
    .map((p) => ({ p, mtime: safeMtime(p) }))
    .filter((entry) => entry.mtime > 0)
    .sort((a, b) => b.mtime - a.mtime);
  return existing.length ? existing[0].p : (candidates[0] || null);
}

function findNewestFile(dir) {
  let best = null;
  const walk = (current) => {
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch (_) { return; }
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue;
      const full = path.join(current, entry.name);
      try {
        if (entry.isDirectory()) {
          walk(full);
        } else if (entry.isFile()) {
          const m = fs.statSync(full).mtimeMs;
          if (!best || m > best.mtime) best = { path: full, mtime: m };
        }
      } catch (_) { /* skip unreadable */ }
    }
  };
  walk(dir);
  return best ? best.path : null;
}

async function openStageNewestFile(session, stageId) {
  const dir = resolveStageWorkspaceDir(session, stageId);
  if (!dir || !fs.existsSync(dir)) {
    setActionError(session, `Stage workspace not found for "${stageId}". The stage may not have started writing yet.`);
    return;
  }
  const newest = findNewestFile(dir);
  if (!newest) {
    setActionError(session, `No files written yet under ${dir}.`);
    return;
  }
  try {
    await vscode.commands.executeCommand('vscode.open', vscode.Uri.file(newest));
  } catch (error) {
    setActionError(session, error.message || String(error));
  }
}

async function revealStageWorkspace(session, stageId) {
  const dir = resolveStageWorkspaceDir(session, stageId);
  if (!dir) {
    setActionError(session, `Could not resolve a workspace directory for stage "${stageId}".`);
    return;
  }
  if (!fs.existsSync(dir)) {
    try { fs.mkdirSync(dir, { recursive: true }); } catch (_) { /* best effort */ }
  }
  try {
    await vscode.commands.executeCommand('revealFileInOS', vscode.Uri.file(dir));
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

  let sawAllowedButMissing = false;
  let firstAllowedCandidate = null;
  for (const candidate of candidates) {
    const resolved = path.resolve(candidate);
    if (!allowedRoots.some((allowedRoot) => isWithin(resolved, allowedRoot))) {
      continue;
    }
    if (!firstAllowedCandidate) firstAllowedCandidate = resolved;
    if (fs.existsSync(resolved) && fs.statSync(resolved).isFile()) {
      return resolved;
    }
    sawAllowedButMissing = true;
  }
  if (sawAllowedButMissing) {
    const stage = artifact.stage_id ? ` (stage: ${artifact.stage_id})` : '';
    throw new Error(
      `${path.basename(artifactPath)} has not been written yet${stage}. ` +
      `Expected at: ${firstAllowedCandidate}`
    );
  }
  throw new Error(
    `Artifact preview path is outside allowed roots. Path: ${artifactPath}. ` +
    'This usually means the campaign workspace metadata is incomplete; relaunch the campaign with --campaign-id.'
  );
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

  // StageRunContext (msc_sdk/stage_runtime.py) writes to
  // results/<campaign>/runs/<run_id>/<stage_id>/, but artifacts.json only declares the
  // stage workspace one level above. Probe the per-run subdirs so previews work
  // even for runs the artifact metadata doesn't yet know about.
  const stageId = String(artifact.stage_id || (stage && (stage.stage_id || stage.id || stage.name)) || '').trim();
  const stageWorkspace = (stage && stage.workspace) || (campaignDetails && campaignDetails.workspace_root);
  if (stageId && stageWorkspace) {
    for (const baseRoot of baseRoots) {
      const runsParent = path.isAbsolute(stageWorkspace)
        ? path.resolve(stageWorkspace, 'runs')
        : path.resolve(baseRoot, stageWorkspace, 'runs');
      let runIds = [];
      try {
        if (fs.existsSync(runsParent) && fs.statSync(runsParent).isDirectory()) {
          runIds = fs.readdirSync(runsParent)
            .map((name) => ({ name, mtime: safeMtime(path.join(runsParent, name)) }))
            .filter((entry) => entry.mtime > 0)
            .sort((a, b) => b.mtime - a.mtime)
            .map((entry) => entry.name);
        }
      } catch (_) {
        runIds = [];
      }
      for (const runId of runIds) {
        push(path.join(runsParent, runId, stageId));
      }
    }
  }

  for (const baseRoot of baseRoots) {
    push(baseRoot);
  }
  return roots.length ? roots : [path.resolve(root)];
}

function safeMtime(filePath) {
  try {
    return fs.statSync(filePath).mtimeMs;
  } catch (_) {
    return 0;
  }
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

function renderArtifactHtml(content, ext) {
  if (ext === '.md' || ext === '.markdown') {
    return renderMarkdownHtml(content);
  }
  if (ext === '.tex') {
    return renderLatexHtml(content);
  }
  return null;
}

function renderMarkdownHtml(content) {
  const lines = String(content || '').replace(/\r\n/g, '\n').split('\n');
  const html = [];
  let paragraph = [];
  let listType = null;
  let inCode = false;
  let codeLines = [];
  let tableLines = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${renderMarkdownInline(paragraph.join(' '))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = null;
  };
  const flushCode = () => {
    html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`);
    codeLines = [];
  };
  const flushTable = () => {
    if (!tableLines.length) return;
    html.push(renderMarkdownTable(tableLines));
    tableLines = [];
  };
  const flushBlocks = () => {
    flushParagraph();
    flushList();
    flushTable();
  };

  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      if (inCode) {
        flushCode();
        inCode = false;
      } else {
        flushBlocks();
        inCode = true;
        codeLines = [];
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }

    if (!line.trim()) {
      flushBlocks();
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flushParagraph();
      flushList();
      tableLines.push(line);
      continue;
    }
    flushTable();

    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(6, heading[1].length);
      html.push(`<h${level}>${renderMarkdownInline(heading[2])}</h${level}>`);
      continue;
    }

    const ordered = /^\s*\d+\.\s+(.+)$/.exec(line);
    const unordered = /^\s*[-*+]\s+(.+)$/.exec(line);
    if (ordered || unordered) {
      flushParagraph();
      const nextListType = ordered ? 'ol' : 'ul';
      if (listType && listType !== nextListType) {
        flushList();
      }
      if (!listType) {
        listType = nextListType;
        html.push(`<${listType}>`);
      }
      html.push(`<li>${renderMarkdownInline((ordered || unordered)[1])}</li>`);
      continue;
    }

    const quote = /^\s*>\s?(.+)$/.exec(line);
    if (quote) {
      flushParagraph();
      flushList();
      html.push(`<blockquote>${renderMarkdownInline(quote[1])}</blockquote>`);
      continue;
    }

    paragraph.push(line.trim());
  }

  if (inCode) {
    flushCode();
  }
  flushBlocks();
  return html.join('\n');
}

function renderMarkdownTable(lines) {
  const rows = lines
    .map((line) => line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim()))
    .filter((row) => row.length > 1);
  if (!rows.length) return '';
  const hasSeparator = rows.length > 1 && rows[1].every((cell) => /^:?-{3,}:?$/.test(cell));
  const header = rows[0];
  const body = hasSeparator ? rows.slice(2) : rows.slice(1);
  const headHtml = `<thead><tr>${header.map((cell) => `<th>${renderMarkdownInline(cell)}</th>`).join('')}</tr></thead>`;
  const bodyHtml = body.length
    ? `<tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${renderMarkdownInline(cell)}</td>`).join('')}</tr>`).join('')}</tbody>`
    : '';
  return `<table>${headHtml}${bodyHtml}</table>`;
}

function renderMarkdownInline(text) {
  let value = escapeHtml(text);
  value = value.replace(/`([^`]+)`/g, '<code>$1</code>');
  value = value.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  value = value.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  value = value.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
    const safeHref = sanitizeHref(href);
    return safeHref ? `<a href="${safeHref}">${label}</a>` : label;
  });
  return value;
}

function renderLatexHtml(content) {
  let source = String(content || '')
    .replace(/\r\n/g, '\n')
    .replace(/%.*$/gm, '')
    .replace(/\\begin\{abstract\}/g, '\n\\section*{Abstract}\n')
    .replace(/\\end\{abstract\}/g, '\n')
    .replace(/\\begin\{itemize\}/g, '\n__BEGIN_UL__\n')
    .replace(/\\end\{itemize\}/g, '\n__END_UL__\n')
    .replace(/\\begin\{enumerate\}/g, '\n__BEGIN_OL__\n')
    .replace(/\\end\{enumerate\}/g, '\n__END_OL__\n')
    .replace(/\\item\s+/g, '\n__ITEM__ ')
    .replace(/\$\$([\s\S]*?)\$\$/g, (_, math) => `\n__MATH__ ${math.trim()}\n`)
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, math) => `\n__MATH__ ${math.trim()}\n`);

  const lines = source.split('\n');
  const html = [];
  let paragraph = [];
  let listType = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${renderLatexInline(paragraph.join(' '))}</p>`);
    paragraph = [];
  };
  const closeList = () => {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      continue;
    }
    if (line === '__BEGIN_UL__' || line === '__BEGIN_OL__') {
      flushParagraph();
      closeList();
      listType = line === '__BEGIN_UL__' ? 'ul' : 'ol';
      html.push(`<${listType}>`);
      continue;
    }
    if (line === '__END_UL__' || line === '__END_OL__') {
      flushParagraph();
      closeList();
      continue;
    }
    if (line.startsWith('__ITEM__')) {
      flushParagraph();
      if (!listType) {
        listType = 'ul';
        html.push('<ul>');
      }
      html.push(`<li>${renderLatexInline(line.replace('__ITEM__', '').trim())}</li>`);
      continue;
    }
    if (line.startsWith('__MATH__')) {
      flushParagraph();
      closeList();
      html.push(`<div class="math-block">${escapeHtml(line.replace('__MATH__', '').trim())}</div>`);
      continue;
    }
    const title = /^\\title\{(.+)\}$/.exec(line);
    const section = /^\\section\*?\{(.+)\}$/.exec(line);
    const subsection = /^\\subsection\*?\{(.+)\}$/.exec(line);
    const subsubsection = /^\\subsubsection\*?\{(.+)\}$/.exec(line);
    if (title || section || subsection || subsubsection) {
      flushParagraph();
      closeList();
      const tag = title ? 'h1' : section ? 'h2' : subsection ? 'h3' : 'h4';
      html.push(`<${tag}>${renderLatexInline((title || section || subsection || subsubsection)[1])}</${tag}>`);
      continue;
    }
    if (/^\\(documentclass|usepackage|begin\{document\}|end\{document\}|maketitle|bibliography|bibliographystyle)/.test(line)) {
      continue;
    }
    paragraph.push(line);
  }
  flushParagraph();
  closeList();
  return html.join('\n');
}

function renderLatexInline(text) {
  let value = escapeHtml(text)
    .replace(/\\textbf\{([^{}]+)\}/g, '<strong>$1</strong>')
    .replace(/\\emph\{([^{}]+)\}/g, '<em>$1</em>')
    .replace(/\\textit\{([^{}]+)\}/g, '<em>$1</em>')
    .replace(/\\texttt\{([^{}]+)\}/g, '<code>$1</code>')
    .replace(/\\cite\{([^{}]+)\}/g, '<span class="citation">[$1]</span>')
    .replace(/\\ref\{([^{}]+)\}/g, '<span class="citation">$1</span>')
    .replace(/\$([^$]+)\$/g, '<span class="math-inline">$1</span>');
  value = value.replace(/\\[a-zA-Z]+\*?(?:\[[^\]]*\])?/g, '');
  return value.replace(/[{}]/g, '');
}

function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function sanitizeHref(value) {
  const href = String(value || '').trim();
  if (/^(https?:|mailto:)/i.test(href)) {
    return escapeHtml(href);
  }
  if (/^[./#]/.test(href)) {
    return escapeHtml(href);
  }
  return '';
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

async function runJson(root, args, opts = {}) {
  const result = await runMsc(root, args, opts);
  if (!result.ok) {
    return { ...result, errors: [commandError(args, result)] };
  }
  try {
    return { ok: true, data: JSON.parse(result.stdout), errors: [] };
  } catch (error) {
    return { ok: false, stdout: result.stdout, stderr: result.stderr, error: String(error), errors: [commandError(args, { ...result, error: String(error) })] };
  }
}

async function runText(root, args, opts = {}) {
  const result = await runMsc(root, args, opts);
  if (!result.ok) {
    return { ...result, errors: [commandError(args, result)] };
  }
  return { ...result, errors: [] };
}

function commandError(args, result) {
  return {
    command: [result.label || 'msc', ...args].join(' '),
    message: result.error || result.stderr || 'Command failed',
    code: result.code || null
  };
}

const RUN_MSC_DEFAULT_MAX_BUFFER = 4 * 1024 * 1024;
const RUN_MSC_DEFAULT_TIMEOUT_MS = 15000;
const SPAWN_TRANSIENT_CODES = new Set(['EAGAIN', 'ENOMEM', 'EMFILE', 'ENFILE']);
// Backoff schedule for transient spawn failures (EAGAIN under HPC node load).
// Total worst-case added latency on an unrecoverable transient: ~1.9 s.
const SPAWN_RETRY_DELAYS_MS = [200, 500, 1200];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isTransientSpawnFailure(result) {
  // Spawn-time failures don't produce stdout/stderr; detect them by the
  // shape of the error rather than parsing the message.
  if (!result || result.ok) return false;
  if (result.stdout || result.stderr) return false;
  const code = result.code;
  if (typeof code === 'string' && SPAWN_TRANSIENT_CODES.has(code)) return true;
  const message = String(result.error || '');
  for (const transient of SPAWN_TRANSIENT_CODES) {
    if (message.includes(transient)) return true;
  }
  return false;
}

function execFileOnce(command, args, opts) {
  return new Promise((resolve) => {
    childProcess.execFile(
      command.bin,
      [...command.prefixArgs, ...args],
      opts,
      (error, stdout, stderr) => {
        if (error) {
          resolve({ ok: false, code: error.code, error: error.message, stdout: stdout || '', stderr: stderr || '', label: command.label });
          return;
        }
        resolve({ ok: true, stdout: stdout || '', stderr: stderr || '', label: command.label });
      }
    );
  });
}

async function runMsc(root, args, opts = {}) {
  const command = resolveMscCommand(root);
  const maxBuffer = opts.maxBuffer || RUN_MSC_DEFAULT_MAX_BUFFER;
  const timeout = opts.timeout || RUN_MSC_DEFAULT_TIMEOUT_MS;
  const execOpts = { cwd: root, timeout, maxBuffer, env: runtimeEnv(root) };
  let result = await execFileOnce(command, args, execOpts);
  // On a shared HPC node the extension host can hit EAGAIN when forking
  // under thread/process pressure; the window often lasts seconds. Step
  // through SPAWN_RETRY_DELAYS_MS and stop early on success or on a real
  // CLI failure (which surfaces immediately).
  for (const delay of SPAWN_RETRY_DELAYS_MS) {
    if (!isTransientSpawnFailure(result)) break;
    await sleep(delay);
    result = await execFileOnce(command, args, execOpts);
  }
  return result;
}

function spawnWithStdinOnce(command, args, stdinValue, opts) {
  const timeout = opts.timeout || RUN_MSC_DEFAULT_TIMEOUT_MS;
  return new Promise((resolve) => {
    let proc;
    try {
      proc = childProcess.spawn(command.bin, [...command.prefixArgs, ...args], {
        cwd: opts.cwd,
        env: opts.env,
        shell: false
      });
    } catch (error) {
      resolve({ ok: false, code: error.code, error: error.message || String(error), stdout: '', stderr: '', label: command.label });
      return;
    }
    let stdout = '';
    let stderr = '';
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    const killTimer = setTimeout(() => {
      try { proc.kill('SIGTERM'); } catch (_) {}
      finish({ ok: false, error: `command timed out after ${timeout}ms`, stdout, stderr, label: command.label });
    }, timeout);
    proc.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    proc.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    proc.on('error', (error) => {
      clearTimeout(killTimer);
      finish({ ok: false, code: error.code, error: error.message || String(error), stdout, stderr, label: command.label });
    });
    proc.on('exit', (code) => {
      clearTimeout(killTimer);
      if (code === 0) {
        finish({ ok: true, stdout, stderr, label: command.label });
      } else {
        finish({ ok: false, code, error: stderr || `exit code ${code}`, stdout, stderr, label: command.label });
      }
    });
    try {
      proc.stdin.write(String(stdinValue == null ? '' : stdinValue));
      proc.stdin.end();
    } catch (error) {
      clearTimeout(killTimer);
      finish({ ok: false, error: error.message || String(error), stdout, stderr, label: command.label });
    }
  });
}

async function runMscWithStdin(root, args, stdinValue, opts = {}) {
  // Used when we need to hand a secret to the CLI without putting it on argv
  // (which would appear in /proc/<pid>/cmdline on a shared multi-user host).
  const command = resolveMscCommand(root);
  const spawnOpts = { cwd: root, env: runtimeEnv(root), timeout: opts.timeout };
  let result = await spawnWithStdinOnce(command, args, stdinValue, spawnOpts);
  // Same exponential backoff as runMsc. Transient spawn failures never
  // reached stdin, so the secret has not been emitted yet — safe to retry
  // the full spawn.
  for (const delay of SPAWN_RETRY_DELAYS_MS) {
    if (!isTransientSpawnFailure(result)) break;
    await sleep(delay);
    result = await spawnWithStdinOnce(command, args, stdinValue, spawnOpts);
  }
  return result;
}

async function refreshKeyStatus(session) {
  const result = await runJson(session.root, ['config', 'keys', 'list', '--json']);
  if (!result.ok) {
    session.state.keyStatus = { ok: false, error: result.error || result.stderr || 'Could not load key status.', keys: [] };
  } else {
    session.state.keyStatus = {
      ok: true,
      config_path: result.data && result.data.config_path,
      keys: (result.data && result.data.keys) || []
    };
  }
  postState(session);
}

async function setApiKey(session, message) {
  const envVar = String(message.env_var || '').trim().toUpperCase();
  const rawValue = typeof message.value === 'string' ? message.value : '';
  // Trim trailing whitespace but preserve internal characters of the secret.
  const value = rawValue.replace(/[\r\n]+$/g, '').trim();
  if (!envVar) {
    session.panel.webview.postMessage({ type: 'setApiKeyResult', ok: false, env_var: envVar, error: 'env_var is required' });
    return;
  }
  if (!value) {
    session.panel.webview.postMessage({ type: 'setApiKeyResult', ok: false, env_var: envVar, error: 'value is required' });
    return;
  }
  const result = await runMscWithStdin(
    session.root,
    ['config', 'keys', 'set', envVar, '--stdin', '--json'],
    `${value}\n`
  );
  if (!result.ok) {
    session.panel.webview.postMessage({
      type: 'setApiKeyResult',
      ok: false,
      env_var: envVar,
      error: result.error || result.stderr || 'Could not save key.'
    });
    return;
  }
  let parsed = {};
  try { parsed = JSON.parse(result.stdout || '{}'); } catch (_) {}
  session.panel.webview.postMessage({
    type: 'setApiKeyResult',
    ok: Boolean(parsed.ok),
    env_var: envVar,
    config_path: parsed.config_path || null,
    preview: parsed.preview || null,
    error: parsed.ok ? null : (parsed.error || 'Save failed.')
  });
  await refreshKeyStatus(session);
}

async function installOpenClaude(session) {
  const setupOps = session.state.setupOps || { openclaudeInstall: null };
  if (setupOps.openclaudeInstall && setupOps.openclaudeInstall.status === 'running') {
    return;
  }
  setupOps.openclaudeInstall = {
    status: 'running',
    startedAt: new Date().toISOString(),
    log: [],
    error: null,
    path: null
  };
  session.state.setupOps = setupOps;
  postState(session);

  const command = resolveMscCommand(session.root);
  const args = [...command.prefixArgs, 'openclaude', 'install', '--json'];
  const proc = childProcess.spawn(command.bin, args, {
    cwd: session.root,
    env: runtimeEnv(session.root),
    shell: false
  });

  let stdout = '';
  let stderr = '';
  const append = (chunk, channel) => {
    const text = chunk.toString();
    if (channel === 'stdout') stdout += text;
    else stderr += text;
    setupOps.openclaudeInstall.log.push({ channel, text, timestamp: new Date().toISOString() });
    postState(session);
  };
  proc.stdout.on('data', (chunk) => append(chunk, 'stdout'));
  proc.stderr.on('data', (chunk) => append(chunk, 'stderr'));

  await new Promise((resolve) => {
    proc.on('error', (error) => {
      setupOps.openclaudeInstall.status = 'failed';
      setupOps.openclaudeInstall.error = error.message || String(error);
      resolve();
    });
    proc.on('exit', (code) => {
      let parsed = null;
      try { parsed = JSON.parse(stdout); } catch (_) { parsed = null; }
      if (code === 0 && parsed && parsed.ok) {
        setupOps.openclaudeInstall.status = 'completed';
        setupOps.openclaudeInstall.path = parsed.path || null;
        setupOps.openclaudeInstall.alreadyInstalled = Boolean(parsed.already_installed);
      } else {
        setupOps.openclaudeInstall.status = 'failed';
        setupOps.openclaudeInstall.error =
          (parsed && (parsed.error || parsed.error_code)) ||
          stderr.trim().split('\n').pop() ||
          `npm exited with code ${code}`;
      }
      resolve();
    });
  });

  session.state.setupOps = setupOps;
  postState(session);
  await refresh(session);
}

async function unsetApiKey(session, message) {
  const envVar = String(message.env_var || '').trim().toUpperCase();
  if (!envVar) {
    session.panel.webview.postMessage({ type: 'unsetApiKeyResult', ok: false, env_var: envVar, error: 'env_var is required' });
    return;
  }
  const result = await runJson(session.root, ['config', 'keys', 'unset', envVar, '--json']);
  if (!result.ok) {
    session.panel.webview.postMessage({
      type: 'unsetApiKeyResult',
      ok: false,
      env_var: envVar,
      error: result.error || result.stderr || 'Could not remove key.'
    });
    return;
  }
  session.panel.webview.postMessage({
    type: 'unsetApiKeyResult',
    ok: Boolean(result.data && result.data.ok),
    env_var: envVar,
    config_path: (result.data && result.data.config_path) || null
  });
  await refreshKeyStatus(session);
}

async function startCampaignExecution(session, message) {
  if (session.activeProcess) {
    setActionError(session, 'Campaign execution is already active in this dashboard.');
    return false;
  }
  if (session.state.activeRun && session.state.activeRun.slurmRunId && session.state.activeRun.status === 'running') {
    setActionError(session, 'A detached SLURM run is already attached to this campaign.');
    return false;
  }

  const options = normalizeRunOptions(message);
  if (session.state.selectedCampaign) {
    options.campaignId = String(session.state.selectedCampaign);
    options.campaignGraphVersion = session.state.campaignGraph && session.state.campaignGraph.version;
  }
  const validationError = validateRunOptions(options);
  if (validationError) {
    setActionError(session, validationError);
    return false;
  }

  // If sbatch is on PATH and we have a campaign id, submit as a detached
  // SLURM run so the campaign survives the IDE being closed.
  const sbatchAvailable = await detectSbatch();
  if (sbatchAvailable && options.campaignId && !options.dryRun) {
    return startSlurmCampaignExecution(session, options);
  }

  const commandSpec = resolveMscCommand(session.root);
  const args = buildRunArgs(options, session.root);
  const command = [commandSpec.label, ...args].join(' ');
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

  const proc = childProcess.spawn(commandSpec.bin, [...commandSpec.prefixArgs, ...args], { cwd: session.root, env: runEnv(session.root, options), shell: false });
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
    if (code !== 0) {
      session.state.actionError = `Campaign execution command failed with code ${code == null ? 'null' : code}${signal ? ` (${signal})` : ''}. Check the process log for details.`;
    }
    await refresh(session);
  });
  return true;
}

let _sbatchAvailableCache = null;
async function detectSbatch() {
  if (_sbatchAvailableCache !== null) return _sbatchAvailableCache;
  const result = await new Promise((resolve) => {
    const proc = childProcess.spawn('which', ['sbatch'], { shell: false });
    proc.on('exit', (code) => resolve(code === 0));
    proc.on('error', () => resolve(false));
  });
  _sbatchAvailableCache = result;
  return result;
}

async function startSlurmCampaignExecution(session, options) {
  const command = resolveMscCommand(session.root);
  const args = buildHpcSubmitArgs(options, session.root);
  session.state.activeRun = {
    status: 'submitting',
    startedAt: new Date().toISOString(),
    exitedAt: null,
    exitCode: null,
    command: [command.label, ...args].join(' '),
    pid: null,
    dryRun: false,
    campaign: session.state.selectedCampaign || null,
    slurmRunId: null,
    orchestratorJobId: null,
    heartbeatJobId: null
  };
  session.state.runLog = [];
  session.state.actionError = null;
  appendRunLog(session, 'system', '$ ' + session.state.activeRun.command);
  postState(session);

  // `hpc submit` does: Python import (~1.5s) + DB writes + two sbatch calls.
  // On a loaded login node that comfortably exceeds the 15s default timeout
  // used for most CLI calls. Give it a real budget so legitimate submissions
  // don't get killed mid-sbatch — at which point we end up with a half-state
  // (jobs queued, extension thinks it failed). 90s is generous.
  const result = await runJson(session.root, args, { timeout: 90000 });
  if (!result.ok) {
    const reason = result.error || result.stderr || 'sbatch submission failed.';
    const hint = /timed out|ETIMEDOUT/i.test(reason)
      ? ' (timeout while talking to SLURM; check `squeue -u $USER` — your jobs may have been submitted successfully even though we did not see the response)'
      : '';
    session.state.activeRun = {
      ...session.state.activeRun,
      status: 'failed',
      exitedAt: new Date().toISOString(),
      exitCode: result.code != null ? result.code : null
    };
    appendRunLog(session, 'stderr', reason);
    setActionError(session, `SLURM submission failed: ${reason}${hint}`);
    postState(session);
    return false;
  }
  const data = result.data || {};
  session.state.activeRun = {
    ...session.state.activeRun,
    status: 'running',
    slurmRunId: String(data.run_id || ''),
    orchestratorJobId: String(data.orchestrator_job_id || ''),
    heartbeatJobId: String(data.heartbeat_job_id || ''),
    steeringInbox: data.steering && data.steering.inbox || null,
    workspaceRoot: data.campaign_id || null
  };
  appendRunLog(session, 'system',
    `Submitted SLURM run ${session.state.activeRun.slurmRunId} ` +
    `(orchestrator job ${session.state.activeRun.orchestratorJobId}, ` +
    `heartbeat job ${session.state.activeRun.heartbeatJobId}).`);
  postState(session);
  startSlurmRunPolling(session);
  await refresh(session);
  return true;
}

function buildHpcSubmitArgs(options, root) {
  const args = ['hpc', '--root', root, 'submit', String(options.campaignId), '--json'];
  if (options.task) args.push('--task', options.task);
  if (options.tier) args.push('--tier', String(options.tier));
  if (options.budget != null) args.push('--budget', String(options.budget));
  if (options.outputFormat) args.push('--output-format', String(options.outputFormat));
  if (options.maxRunSeconds) args.push('--max-run-seconds', String(options.maxRunSeconds));
  // Forward feature toggles using the canonical runner flag names from
  // consortium/args.py — `--counsel` / `--math` / `--tree-search` are *not*
  // valid runner flags; argparse abbreviation-matching silently maps them to
  // `--counsel-max-debate-rounds` and the orchestrator dies at startup.
  if (options.counsel) args.push('--extra-runner-arg', '--enable-counsel');
  else args.push('--extra-runner-arg', '--no-counsel');
  if (options.math) args.push('--extra-runner-arg', '--enable-math-agents');
  if (options.treeSearch) args.push('--extra-runner-arg', '--enable-tree-search');
  return args;
}

const SLURM_POLL_INTERVAL_MS = 10000;
function startSlurmRunPolling(session) {
  if (session.slurmPollTimer) return;
  const tick = async () => {
    if (!session || !session.state || !session.state.activeRun || !session.state.activeRun.slurmRunId) {
      session.slurmPollTimer = null;
      return;
    }
    if (session.state.activeRun.status !== 'running' && session.state.activeRun.status !== 'stopping') {
      session.slurmPollTimer = null;
      return;
    }
    const campaign = session.state.selectedCampaign;
    if (!campaign) {
      session.slurmPollTimer = null;
      return;
    }
    try {
      const statusResult = await runJson(session.root, [
        'hpc', '--root', session.root, 'status', String(campaign),
        '--run-id', String(session.state.activeRun.slurmRunId), '--json'
      ], { timeout: 30000 });
      if (statusResult.ok && statusResult.data) {
        const data = statusResult.data;
        const orchState = data.orchestrator_state;
        const lastEventType = data.last_event && data.last_event.type;
        // Run is over once squeue forgets the orch job AND a terminal event exists.
        const terminalTypes = ['RunFinished', 'RunFailed', 'RunCancelled'];
        if (orchState == null && terminalTypes.includes(lastEventType)) {
          session.state.activeRun = {
            ...session.state.activeRun,
            status: lastEventType === 'RunFinished' ? 'exited' : 'failed',
            exitedAt: new Date().toISOString()
          };
          appendRunLog(session, 'system', `SLURM run ${session.state.activeRun.slurmRunId} ${lastEventType}.`);
          postState(session);
          await refresh(session);
          session.slurmPollTimer = null;
          return;
        }
      }
      // Trigger reconciliation so any stale rows get a RunFailed event.
      await runJson(session.root, ['hpc', '--root', session.root, 'reconcile', String(campaign), '--json'], { timeout: 30000 })
        .catch(() => null);
    } catch (_) {
      // Polling is best-effort; ignore transient errors.
    }
    session.slurmPollTimer = setTimeout(tick, SLURM_POLL_INTERVAL_MS);
  };
  session.slurmPollTimer = setTimeout(tick, SLURM_POLL_INTERVAL_MS);
}

function stopSlurmRunPolling(session) {
  if (session.slurmPollTimer) {
    clearTimeout(session.slurmPollTimer);
    session.slurmPollTimer = null;
  }
}

function normalizeRunOptions(message) {
  return {
    task: String(message.task || '').trim(),
    dryRun: message.dryRun !== false,
    tier: oneOf(message.tier, ['scaffold', 'lean', 'standard', 'serious', 'ultra'], 'standard'),
    outputFormat: oneOf(message.outputFormat, ['markdown', 'latex'], 'markdown'),
    budget: normalizeBudget(message.budget),
    model: String(message.model || '').trim(),
    maxRunSeconds: normalizeOptionalPositiveInt(message.maxRunSeconds),
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
  if (options.model) {
    args.push('--model', options.model);
  }
  if (options.maxRunSeconds) {
    args.push('--max-run-seconds', String(options.maxRunSeconds));
  }
  args.push(options.task);
  return args;
}

async function stopCampaignExecution(session) {
  // Detached SLURM run: scancel both jobs via `msc hpc cancel`.
  const activeRun = session.state.activeRun;
  if (activeRun && activeRun.slurmRunId && !session.activeProcess) {
    appendRunLog(session, 'system', `Stop requested: scancel for run ${activeRun.slurmRunId}.`);
    session.state.activeRun = { ...activeRun, status: 'stopping' };
    postState(session);
    const result = await runJson(session.root, [
      'hpc', '--root', session.root, 'cancel', String(session.state.selectedCampaign || ''),
      '--run-id', String(activeRun.slurmRunId), '--json'
    ], { timeout: 30000 });
    if (!result.ok) {
      setActionError(session, result.error || result.stderr || 'scancel failed.');
    } else {
      session.state.activeRun = {
        ...session.state.activeRun,
        status: 'exited',
        exitedAt: new Date().toISOString()
      };
      appendRunLog(session, 'system', 'SLURM run cancelled.');
    }
    stopSlurmRunPolling(session);
    postState(session);
    await refresh(session);
    return;
  }
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
  const activeRun = session.state.activeRun;
  if (activeRun && activeRun.slurmRunId && !session.activeProcess) {
    // For detached runs the file-based steering queue is the channel.
    // An interrupt is just a steer call with `--interrupt --message ''`,
    // but `--message` requires non-empty text. Use a no-op instruction.
    const result = await runJson(session.root, [
      'hpc', '--root', session.root, 'steer', String(session.state.selectedCampaign || ''),
      '--run-id', String(activeRun.slurmRunId),
      '--interrupt', '--message', 'pause requested by researcher',
      '--type', 'm', '--json'
    ], { timeout: 30000 });
    if (!result.ok) {
      setActionError(session, result.error || result.stderr || 'steering call failed.');
      return;
    }
    appendRunLog(session, 'system', 'Steering interrupt enqueued (file channel).');
    return;
  }
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
  const activeRun = session.state.activeRun;
  const text = String(message.text || '').trim();
  if (!text) {
    setActionError(session, 'Enter a steering instruction first.');
    return;
  }
  const instructionType = oneOf(message.instructionType, ['m', 'n'], 'm');
  if (activeRun && activeRun.slurmRunId && !session.activeProcess) {
    const result = await runJson(session.root, [
      'hpc', '--root', session.root, 'steer', String(session.state.selectedCampaign || ''),
      '--run-id', String(activeRun.slurmRunId),
      '--message', text,
      '--type', instructionType,
      '--no-interrupt', '--json'
    ], { timeout: 30000 });
    if (!result.ok) {
      setActionError(session, result.error || result.stderr || 'steering call failed.');
      return;
    }
    appendRunLog(session, 'system', 'Steering instruction enqueued (file channel).');
    return;
  }
  if (!session.activeProcess) {
    setActionError(session, 'Start campaign execution before sending steering instructions.');
    return;
  }
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
  reconcileZombieResponding(session);
}

// If a prior turn was mid-stream when the extension host was killed/reloaded,
// the on-disk transcript has `streaming: true` and `status === 'responding'`
// but the producer subprocess is gone (it lived only in session memory). Clear
// the spinner so the panel is usable again.
function reconcileZombieResponding(session) {
  const oc = session.state.openClaude;
  if (!oc) return;
  const hasLiveProcess = Boolean(session.openClaudeProcess);
  const transcript = oc.transcript || [];
  const lastStreaming = transcript.length && transcript[transcript.length - 1].streaming;
  if (hasLiveProcess) return;
  if (oc.status !== 'responding' && !lastStreaming) return;
  finalizeStreamingAssistantMessage(session);
  const interruptedNote = appendChatMessage(session.state.openClaude.transcript || [], {
    role: 'system',
    text: 'Previous response was interrupted (extension reloaded or session lost). Send a new message to retry.',
    timestamp: new Date().toISOString()
  });
  session.state.openClaude = {
    ...session.state.openClaude,
    status: 'ready',
    error: 'Previous OpenClaude turn did not finish before the extension was reloaded.',
    transcript: interruptedNote
  };
  saveOpenClaudeHistory(session);
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
    'openclaude',
    '--model',
    model,
    'launch',
    '--execute',
    '--',
    '-p',
    '--permission-mode',
    'bypassPermissions',
    '--allowedTools',
    'Bash,Read,Grep,Glob',
    '--verbose',
    '--output-format',
    'stream-json',
    '--include-partial-messages',
    prompt
  ];
  const commandSpec = resolveMscCommand(session.root);
  appendOpenClaudeAction(session, { kind: 'command', status: 'started', text: `${commandSpec.label} ${args.join(' ')}`, timestamp: new Date().toISOString() });
  const proc = childProcess.spawn(commandSpec.bin, [...commandSpec.prefixArgs, ...args], { cwd: session.root, env: runtimeEnv(session.root), shell: false });
  session.openClaudeProcess = proc;
  let stdoutBuffer = '';
  let stderrBuffer = '';
  let assistantText = '';
  let lastChunkAt = Date.now();
  let stallWarned = false;
  let killedForStall = false;
  const watchdog = setInterval(() => {
    if (!session.openClaudeProcess || session.openClaudeProcess !== proc) {
      clearInterval(watchdog);
      return;
    }
    const idleMs = Date.now() - lastChunkAt;
    if (!stallWarned && idleMs >= OPENCLAUDE_STALL_WARN_MS) {
      stallWarned = true;
      appendOpenClaudeAction(session, {
        kind: 'stall',
        status: 'warning',
        text: `No output from OpenClaude for ${Math.round(idleMs / 1000)}s. It may be stuck on a tool call.`,
        timestamp: new Date().toISOString()
      });
    }
    if (!killedForStall && idleMs >= OPENCLAUDE_STALL_KILL_MS) {
      killedForStall = true;
      appendOpenClaudeAction(session, {
        kind: 'stall',
        status: 'failed',
        text: `Terminating OpenClaude after ${Math.round(idleMs / 1000)}s of silence.`,
        timestamp: new Date().toISOString()
      });
      try { proc.kill('SIGTERM'); } catch (_) {}
    }
  }, OPENCLAUDE_WATCHDOG_INTERVAL_MS);
  proc.stdout.on('data', (chunk) => {
    lastChunkAt = Date.now();
    stallWarned = false;
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
    lastChunkAt = Date.now();
    const textChunk = chunk.toString();
    stderrBuffer += textChunk;
    appendOpenClaudeAction(session, { kind: 'stderr', status: 'running', text: textChunk, timestamp: new Date().toISOString() });
  });
  proc.on('error', (error) => {
    clearInterval(watchdog);
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
    clearInterval(watchdog);
    session.openClaudeProcess = null;
    if (!assistantText && stdoutBuffer.trim()) {
      assistantText = stdoutBuffer.trim();
      updateStreamingAssistantMessage(session, assistantText);
    }
    finalizeStreamingAssistantMessage(session);
    const stalledOut = killedForStall;
    session.state.openClaude = {
      ...(session.state.openClaude || defaultOpenClaudeState()),
      status: code === 0 && !stalledOut ? 'ready' : 'error',
      error: code === 0 && !stalledOut
        ? null
        : stalledOut
          ? 'OpenClaude was terminated after going silent for too long. Send a new message to retry.'
          : `OpenClaude exited with code ${code == null ? 'null' : code}${signal ? ` (${signal})` : ''}.`
    };
    if ((code !== 0 || stalledOut) && !assistantText) {
      session.state.openClaude.transcript = appendChatMessage(session.state.openClaude?.transcript || [], {
        role: 'system',
        text: [
          session.state.openClaude.error || 'OpenClaude exited before returning a response.',
          stderrBuffer.trim() ? `stderr:\n${stderrBuffer.trim().slice(0, 1800)}` : ''
        ].filter(Boolean).join('\n\n'),
        timestamp: new Date().toISOString()
      });
    }
    appendOpenClaudeAction(session, { kind: 'command', status: code === 0 && !stalledOut ? 'completed' : 'failed', text: `OpenClaude exited with code ${code == null ? 'null' : code}${stalledOut ? ' (stall watchdog)' : ''}.`, timestamp: new Date().toISOString() });
    Object.assign(session.state, await loadCampaignWorkspace(session.root, session.state.selectedCampaign));
    saveOpenClaudeHistory(session);
    postState(session);
  });
}

function cancelOpenClaudeSession(session) {
  const proc = session.openClaudeProcess;
  if (!proc) {
    finalizeStreamingAssistantMessage(session);
    session.state.openClaude = {
      ...(session.state.openClaude || defaultOpenClaudeState()),
      status: 'ready'
    };
    saveOpenClaudeHistory(session);
    postState(session);
    return;
  }
  appendOpenClaudeAction(session, { kind: 'command', status: 'cancelled', text: 'Cancel requested by user.', timestamp: new Date().toISOString() });
  session.state.openClaude = {
    ...(session.state.openClaude || defaultOpenClaudeState()),
    transcript: appendChatMessage(session.state.openClaude?.transcript || [], {
      role: 'system',
      text: 'Cancelled by user.',
      timestamp: new Date().toISOString()
    })
  };
  try { proc.kill('SIGTERM'); } catch (_) {}
  // The exit handler finishes the cleanup (finalize, status, saveHistory, postState).
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
  const selectedNodeId = session.state.selectedGraphNode || '';
  const contextJson = JSON.stringify(compactOpenClaudeContext(contextPack, selectedNodeId), null, 2);
  const history = chatHistoryForPrompt(session.state.openClaude?.transcript || [], userText);
  const root = session.root || process.cwd();
  const commandSpec = resolveMscCommand(root);
  const cliPrefix = commandSpec.shellPrefix || commandSpec.label;
  return [
    `Campaign: ${session.state.selectedCampaign}`,
    selectedNodeId ? `Researcher's currently selected graph node: ${selectedNodeId} (treat "this stage" / "this node" as referring to it unless told otherwise).` : '',
    `Researcher message: ${userText}`,
    '',
    'Recent chat history:',
    history || 'No prior chat history for this campaign.',
    '',
    'You are the Overseer for this campaign. This chat is the researcher\'s primary steering interface — the graph and node-detail panel they see on the left expose the same campaign state you can inspect through the SDK. When the researcher asks for action, prefer to actually call the SDK rather than only describing what would happen.',
    `Use the MSc SDK/CLI as the campaign authority. Run commands from ${root} with this repo-local prefix: ${cliPrefix}. Do not assume bare msc is on PATH.`,
    '',
    'Steering verbs you have direct CLI access to via "<prefix> campaigns ..." (see "<prefix> campaigns --help" for the full surface):',
    '  - rerun-stage <campaign> <node> --reason "..."   (re-execute a stage; produces a new artifact iteration)',
    '  - rewrite-stage <campaign> <node> --instruction "..."   (apply an explicit edit instruction)',
    '  - rewind <campaign> <node> --reason "..."   (roll the graph back to before a node)',
    '  - reroute --from <node> --to <node> --reason "..."   (change the active path)',
    '  - propose-repair <campaign> --node <node> --reason "..."   (open a structured repair proposal)',
    '  - summarize-artifacts <campaign>   (digest the most recent deliverables)',
    '  - request-evidence <campaign> --question "..." [--node <node>] [--artifact <path>]',
    '  - approve <approval-id> / reject <approval-id>   (decide a pending approval)',
    '',
    'Detached SLURM runs (auto-used on HPC hosts) are managed via "<prefix> hpc ...":',
    '  - hpc submit <campaign> --task "..."   (start a new detached orchestrator + heartbeat)',
    '  - hpc status <campaign> [--run-id ID] --json   (squeue state + last event for a run)',
    '  - hpc steer <campaign> --message "..."  (file-based steering; reaches compute nodes)',
    '  - hpc cancel <campaign> [--run-id ID]   (scancel both jobs; records RunCancelled)',
    '  - hpc reconcile <campaign>   (reconcile any vanished jobs against squeue)',
    '  - hpc tail <campaign> [--run-id ID]   (tail -F the orchestrator stdout log)',
    'For ad-hoc analysis you may also Read / Grep artifact files directly from the campaign workspace. To "spawn a debug agent" you typically use propose-repair or request-evidence with a focused question, then iterate via rerun-stage once the diagnosis lands. Never edit repo code, delete campaigns/artifacts, scrape SQLite directly, or bypass budget limits.',
    '',
    'Current compact campaign context:',
    contextJson
  ].filter(Boolean).join('\n');
}

function compactOpenClaudeContext(contextPack, selectedNodeId = '') {
  if (!contextPack) {
    return selectedNodeId ? { selected_node_id: selectedNodeId } : {};
  }
  const compact = {
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
  if (selectedNodeId) compact.selected_node_id = selectedNodeId;
  return compact;
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

function runEnv(root, options = {}) {
  const env = runtimeEnv(root);
  if (options.model) {
    env.DEEP_RESEARCH_MODEL = openRouterModelForEnv(options.model);
    env.LITELLM_MODEL_ID = openRouterModelForEnv(options.model);
  }
  return env;
}

function openRouterModelForEnv(model) {
  const value = String(model || '').trim();
  if (!value) return '';
  return value.startsWith('openrouter/') ? value : `openrouter/${value}`;
}

function resolveMscBin(root) {
  return resolveMscCommand(root).bin;
}

function resolveMscCommand(root) {
  const local = process.platform === 'win32'
    ? path.join(root, '.venv', 'Scripts', 'msc.exe')
    : path.join(root, '.venv', 'bin', 'msc');
  if (fs.existsSync(local)) {
    return { bin: local, prefixArgs: ['--no-banner'], label: local, shellPrefix: `${local} --no-banner` };
  }

  const localPython = process.platform === 'win32'
    ? path.join(root, '.venv', 'Scripts', 'python.exe')
    : path.join(root, '.venv', 'bin', 'python');
  if (fs.existsSync(localPython)) {
    return {
      bin: localPython,
      prefixArgs: ['-m', 'consortium.cli.main', '--no-banner'],
      label: `${localPython} -m consortium.cli.main`,
      shellPrefix: `${localPython} -m consortium.cli.main --no-banner`
    };
  }

  if (isMscProjectRoot(root)) {
    const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
    return {
      bin: python,
      prefixArgs: ['-m', 'consortium.cli.main', '--no-banner'],
      label: `${python} -m consortium.cli.main`,
      shellPrefix: `${python} -m consortium.cli.main --no-banner`
    };
  }

  return { bin: 'msc', prefixArgs: ['--no-banner'], label: 'msc', shellPrefix: 'msc --no-banner' };
}

function openRouterConfigured(openclaude) {
  const readiness = unwrap(openclaude, 'openclaude');
  return Boolean(process.env.OPENROUTER_API_KEY || readiness.openrouter_configured);
}

function oneOf(value, allowed, fallback) {
  return allowed.includes(value) ? value : fallback;
}

function normalizeBudget(value) {
  const parsed = Number.parseInt(String(value == null ? '20' : value), 10);
  return Number.isFinite(parsed) ? parsed : 20;
}

function normalizeOptionalPositiveInt(value) {
  if (value == null || value === '') {
    return null;
  }
  const parsed = Number.parseInt(String(value), 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
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
  renderArtifactHtml,
  textFromOpenClaudeEvent,
  normalizeRunOptions,
  safeResolveArtifactPath,
  validateRunOptions,
  resolveMscBin,
  resolveMscCommand
};
