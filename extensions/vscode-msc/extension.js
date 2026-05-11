const vscode = require('vscode');
const childProcess = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

const MAX_LOG_LINES = 400;
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
    refresh(session);

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
    session.state.campaignGraph = null;
    session.state.campaignArtifacts = [];
    session.state.selectedGraphNode = null;
    session.state.artifactPreview = null;
    postState(session);
  } else if (message.type === 'createCampaign' || message.type === 'createCampaignDraft') {
    await createCampaignDraftForSession(session, message.draft || {});
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
  } else if (message.type === 'startRun') {
    await startRun(session, message);
  } else if (message.type === 'stopRun') {
    stopRun(session);
  } else if (message.type === 'interruptRun') {
    await interruptRun(session);
  } else if (message.type === 'sendInstruction') {
    await sendInstruction(session, message);
  }
}

function createDashboardSession(panel, root) {
  return {
    panel,
    root,
    activeProcess: null,
    stopTimer: null,
    steeringTimer: null,
    state: initialState(root)
  };
}

function cleanupSession(session) {
  stopSteeringPolling(session);
  if (session.stopTimer) {
    clearTimeout(session.stopTimer);
    session.stopTimer = null;
  }
  if (session.activeProcess && !session.activeProcess.killed) {
    session.activeProcess.kill('SIGTERM');
  }
}

function getWorkspaceRoot() {
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
  return folder ? folder.uri.fsPath : process.cwd();
}

function initialState(root) {
  return {
    root,
    view: 'home',
    loading: true,
    generatedAt: new Date().toISOString(),
    errors: [],
    campaigns: [],
    selectedCampaign: null,
    campaignDetails: null,
    campaignGraph: null,
    campaignArtifacts: [],
    selectedGraphNode: null,
    artifactPreview: null,
    settingsOpen: false,
    settings: defaultSettings(root),
    diagnostics: {},
    activeRun: null,
    runLog: [],
    steering: { available: false, paused: false, queueDepth: 0, lastError: null },
    actionError: null
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
  state.actionError = previous.actionError;

  if (state.selectedCampaign) {
    Object.assign(state, await loadCampaignWorkspace(session.root, state.selectedCampaign));
  }

  session.state = state;
  postState(session);
}

async function collectDashboardData(root) {
  const state = initialState(root);
  state.loading = false;

  const campaigns = await runJson(root, ['campaigns', '--root', root, 'list', '--json']);
  state.campaigns = await enrichCampaigns(root, unwrap(campaigns, 'campaigns').campaigns || []);

  const readiness = await runJson(root, ['project', 'readiness', '--json']);
  const commands = await runJson(root, ['selftest', 'commands', '--json']);
  const config = await runText(root, ['config', 'list']);
  const budget = await runText(root, ['budget']);
  const openclaude = await runJson(root, ['openclaude', 'readiness', '--json']);
  const openclaudeEnv = await runJson(root, ['openclaude', 'env', '--json']);

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
    openclaudeEnv: unwrap(openclaudeEnv, 'openclaudeEnv')
  };
  state.errors = [
    ...campaigns.errors,
    ...readiness.errors,
    ...commands.errors,
    ...config.errors,
    ...budget.errors,
    ...openclaude.errors,
    ...openclaudeEnv.errors
  ];
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
  session.state.errors = [...(session.state.errors || []), ...(workspace.errors || [])];
  postState(session);
}

async function loadCampaignWorkspace(root, campaignRef) {
  const inspect = await runJson(root, ['campaigns', '--root', root, 'inspect', campaignRef, '--json']);
  const graph = await runJson(root, ['campaigns', '--root', root, 'graph', campaignRef, '--json']);
  const artifacts = await runJson(root, ['campaigns', '--root', root, 'artifacts', campaignRef, '--json']);
  return {
    campaignDetails: unwrap(inspect, 'campaign'),
    campaignGraph: unwrap(graph, 'campaignGraph'),
    campaignArtifacts: normalizeArtifacts(unwrap(artifacts, 'artifacts')),
    errors: [...inspect.errors, ...graph.errors, ...artifacts.errors]
  };
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

async function startRun(session, message) {
  if (session.activeProcess) {
    setActionError(session, 'A run is already active in this dashboard.');
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
    return 'Enter a research task before starting a run.';
  }
  if (!Number.isInteger(options.budget) || options.budget < 1 || options.budget > 10000) {
    return 'Budget must be an integer between 1 and 10000.';
  }
  if (!options.dryRun && (!options.allowSpend || options.confirmation !== RUN_CONFIRMATION)) {
    return `Real local runs require allow spend plus confirmation text ${RUN_CONFIRMATION}.`;
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

function stopRun(session) {
  if (!session.activeProcess) {
    setActionError(session, 'No active run to stop.');
    return;
  }
  appendRunLog(session, 'system', 'Stop requested: sending SIGINT.');
  session.state.activeRun = { ...session.state.activeRun, status: 'stopping' };
  postState(session);
  session.activeProcess.kill('SIGINT');
  session.stopTimer = setTimeout(() => {
    if (session.activeProcess) {
      appendRunLog(session, 'system', 'Run still active after SIGINT; sending SIGTERM.');
      session.activeProcess.kill('SIGTERM');
    }
  }, 5000);
}

async function interruptRun(session) {
  if (!session.activeProcess) {
    setActionError(session, 'Start a local run before sending steering commands.');
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
    setActionError(session, 'Start a local run before sending steering instructions.');
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
  collectDashboardData,
  deactivate,
  normalizeRunOptions,
  safeResolveArtifactPath,
  validateRunOptions,
  resolveMscBin
};
