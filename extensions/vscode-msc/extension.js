const vscode = require('vscode');
const childProcess = require('child_process');
const fs = require('fs');
const path = require('path');

function activate(context) {
  const disposable = vscode.commands.registerCommand('mscDashboard.open', () => {
    const root = getWorkspaceRoot();
    const panel = vscode.window.createWebviewPanel(
      'mscDashboard',
      'MSc Dashboard',
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true
      }
    );

    panel.webview.html = renderHtml(panel.webview, initialState(root));
    refresh(panel, root);

    panel.webview.onDidReceiveMessage(async (message) => {
      if (!message || message.type !== 'refresh') {
        return;
      }
      await refresh(panel, root);
    }, undefined, context.subscriptions);
  });

  context.subscriptions.push(disposable);
}

function deactivate() {}

function getWorkspaceRoot() {
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
  return folder ? folder.uri.fsPath : process.cwd();
}

function initialState(root) {
  return {
    root,
    loading: true,
    generatedAt: new Date().toISOString(),
    errors: [],
    readiness: null,
    project: null,
    runs: [],
    campaigns: [],
    campaignGraph: null,
    events: [],
    commands: [],
    capabilities: null,
    openclaude: null,
    openclaudeEnv: null,
    openclaw: null,
    setupState: null,
    tutorialPlan: null
  };
}

async function refresh(panel, root) {
  panel.webview.postMessage({ type: 'state', state: { ...initialState(root), loading: true } });
  const state = await collectDashboardData(root);
  panel.webview.postMessage({ type: 'state', state });
}

async function collectDashboardData(root) {
  const state = initialState(root);
  state.loading = false;

  const readiness = await runJson(root, ['project', 'readiness', '--json']);
  state.readiness = unwrap(readiness, 'readiness');

  const project = await runJson(root, ['project', 'inspect', '--json']);
  state.project = unwrap(project, 'project');

  const runs = await runJson(root, ['runs', 'list', '--json']);
  state.runs = unwrap(runs, 'runs').runs || [];

  const campaigns = await runJson(root, ['campaigns', '--root', root, 'list', '--json']);
  state.campaigns = unwrap(campaigns, 'campaigns').campaigns || [];

  if (state.campaigns.length > 0) {
    const firstCampaign = state.campaigns[0].path || state.campaigns[0].name;
    const graph = await runJson(root, ['campaigns', '--root', root, 'graph', firstCampaign, '--json']);
    state.campaignGraph = unwrap(graph, 'campaignGraph');
  }

  const events = await runJson(root, ['events', '--state-dir', path.join(root, '.msc'), 'list', '--limit', '25', '--json']);
  state.events = unwrap(events, 'events').events || [];

  const commands = await runJson(root, ['selftest', 'commands', '--json']);
  state.commands = unwrap(commands, 'commands').commands || [];

  const capabilities = await runJson(root, ['capabilities', 'current', '--json']);
  state.capabilities = unwrap(capabilities, 'capabilities');

  const setupState = await runJson(root, ['project', 'setup-state', '--json']);
  state.setupState = unwrap(setupState, 'setupState');

  const tutorialPlan = await runJson(root, ['project', 'tutorial-plan', '--json']);
  state.tutorialPlan = unwrap(tutorialPlan, 'tutorialPlan');

  const openclaude = await runJson(root, ['openclaude', 'readiness', '--json']);
  state.openclaude = unwrap(openclaude, 'openclaude');

  const openclaudeEnv = await runJson(root, ['openclaude', 'env', '--json']);
  state.openclaudeEnv = unwrap(openclaudeEnv, 'openclaudeEnv');

  const openclaw = await runJson(root, ['openclaw', 'readiness', '--json']);
  state.openclaw = unwrap(openclaw, 'openclaw');

  state.errors = [
    ...readiness.errors,
    ...project.errors,
    ...runs.errors,
    ...campaigns.errors,
    ...events.errors,
    ...commands.errors,
    ...capabilities.errors,
    ...setupState.errors,
    ...tutorialPlan.errors,
    ...openclaude.errors,
    ...openclaudeEnv.errors,
    ...openclaw.errors
  ];
  state.generatedAt = new Date().toISOString();
  return state;
}

function unwrap(result, label) {
  if (result.ok) {
    return result.data || {};
  }
  return {
    ok: false,
    label,
    error: result.error,
    stdout: result.stdout,
    stderr: result.stderr
  };
}

async function runJson(root, args) {
  const result = await runMsc(root, args);
  if (!result.ok) {
    return { ...result, errors: [commandError(args, result)] };
  }
  try {
    return { ok: true, data: JSON.parse(result.stdout), errors: [] };
  } catch (error) {
    return {
      ok: false,
      stdout: result.stdout,
      stderr: result.stderr,
      error: String(error),
      errors: [commandError(args, { ...result, error: String(error) })]
    };
  }
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
      {
        cwd: root,
        timeout: 15000,
        maxBuffer: 1024 * 1024,
        env: { ...process.env, PYTHONPATH: root }
      },
      (error, stdout, stderr) => {
        if (error) {
          resolve({
            ok: false,
            code: error.code,
            error: error.message,
            stdout: stdout || '',
            stderr: stderr || ''
          });
          return;
        }
        resolve({ ok: true, stdout: stdout || '', stderr: stderr || '' });
      }
    );
  });
}

function resolveMscBin(root) {
  const local = process.platform === 'win32'
    ? path.join(root, '.venv', 'Scripts', 'msc.exe')
    : path.join(root, '.venv', 'bin', 'msc');
  return fs.existsSync(local) ? local : 'msc';
}

function renderHtml(webview, state) {
  const nonce = getNonce();
  const serialized = JSON.stringify(state).replace(/</g, '\\u003c');
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' ${webview.cspSource}; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MSc Dashboard</title>
  <style>
    :root {
      --bg: var(--vscode-editor-background);
      --fg: var(--vscode-editor-foreground);
      --muted: var(--vscode-descriptionForeground);
      --border: var(--vscode-panel-border);
      --accent: var(--vscode-button-background);
      --accent-fg: var(--vscode-button-foreground);
      --panel: var(--vscode-sideBar-background);
      --warn: var(--vscode-editorWarning-foreground);
      --bad: var(--vscode-errorForeground);
      --good: var(--vscode-terminal-ansiGreen);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font: 13px/1.45 var(--vscode-font-family);
    }
    button {
      border: 1px solid var(--border);
      background: transparent;
      color: var(--fg);
      min-height: 30px;
      padding: 5px 10px;
      cursor: pointer;
    }
    button.primary {
      background: var(--accent);
      color: var(--accent-fg);
      border-color: var(--accent);
    }
    .shell {
      display: grid;
      grid-template-rows: auto auto 1fr;
      min-height: 100vh;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
    }
    h1 {
      font-size: 16px;
      margin: 0;
      font-weight: 600;
    }
    .toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .pill {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--border);
      padding: 3px 8px;
      min-height: 24px;
      color: var(--muted);
      background: var(--bg);
    }
    .tabs {
      display: flex;
      gap: 0;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
      overflow-x: auto;
    }
    .tabs button {
      border-width: 0 1px 0 0;
      min-width: 96px;
    }
    .tabs button.active {
      background: var(--bg);
      color: var(--fg);
      border-bottom: 2px solid var(--accent);
    }
    main { padding: 14px 16px; }
    .summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(140px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .metric {
      border: 1px solid var(--border);
      background: var(--panel);
      padding: 10px;
      min-height: 68px;
    }
    .metric strong { display: block; font-size: 20px; margin-top: 4px; }
    .muted { color: var(--muted); }
    .grid {
      display: grid;
      grid-template-columns: minmax(280px, 1fr) minmax(320px, 1.3fr);
      gap: 14px;
      align-items: start;
    }
    .panel {
      border: 1px solid var(--border);
      background: var(--panel);
      padding: 12px;
      min-height: 120px;
    }
    .panel h2 {
      margin: 0 0 10px;
      font-size: 14px;
      font-weight: 600;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      padding: 7px 6px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th { color: var(--muted); font-weight: 500; }
    .graph {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: stretch;
    }
    .node {
      border: 1px solid var(--border);
      border-left: 4px solid var(--accent);
      padding: 9px;
      min-width: 150px;
      max-width: 220px;
      background: var(--bg);
    }
    .node.failed { border-left-color: var(--bad); }
    .node.completed { border-left-color: var(--good); }
    .node.pending, .node.unknown { border-left-color: var(--muted); }
    .node-title { font-weight: 600; overflow-wrap: anywhere; }
    .node-status { color: var(--muted); margin-top: 4px; }
    .event-list {
      display: grid;
      gap: 8px;
    }
    .event {
      border-left: 3px solid var(--accent);
      padding: 6px 8px;
      background: var(--bg);
    }
    .hidden { display: none; }
    .error { color: var(--bad); }
    @media (max-width: 860px) {
      .summary { grid-template-columns: repeat(2, minmax(140px, 1fr)); }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>MSc Dashboard</h1>
        <div class="muted" id="root"></div>
      </div>
      <div class="toolbar">
        <span class="pill">Read-only</span>
        <span class="pill" id="updated">Waiting</span>
        <button class="primary" id="refresh" title="Refresh dashboard data">Refresh</button>
      </div>
    </header>
    <nav class="tabs" aria-label="Dashboard tabs">
      <button data-tab="graph" class="active">Graph</button>
      <button data-tab="runs">Runs</button>
      <button data-tab="campaigns">Campaigns</button>
      <button data-tab="events">Events</button>
      <button data-tab="openclaude">OpenClaude</button>
      <button data-tab="readiness">Readiness</button>
    </nav>
    <main>
      <section class="summary" id="summary"></section>
      <section id="tab-graph" class="tab"></section>
      <section id="tab-runs" class="tab hidden"></section>
      <section id="tab-campaigns" class="tab hidden"></section>
      <section id="tab-events" class="tab hidden"></section>
      <section id="tab-openclaude" class="tab hidden"></section>
      <section id="tab-readiness" class="tab hidden"></section>
    </main>
  </div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    let state = ${serialized};

    document.getElementById('refresh').addEventListener('click', () => {
      vscode.postMessage({ type: 'refresh' });
    });

    document.querySelectorAll('.tabs button').forEach((button) => {
      button.addEventListener('click', () => selectTab(button.dataset.tab));
    });

    window.addEventListener('message', (event) => {
      if (event.data && event.data.type === 'state') {
        state = event.data.state;
        render();
      }
    });

    function selectTab(tab) {
      document.querySelectorAll('.tabs button').forEach((button) => {
        button.classList.toggle('active', button.dataset.tab === tab);
      });
      document.querySelectorAll('.tab').forEach((section) => {
        section.classList.toggle('hidden', section.id !== 'tab-' + tab);
      });
    }

    function render() {
      document.getElementById('root').textContent = state.root || '';
      document.getElementById('updated').textContent = state.loading ? 'Refreshing' : shortTime(state.generatedAt);
      renderSummary();
      renderGraph();
      renderRuns();
      renderCampaigns();
      renderEvents();
      renderOpenClaude();
      renderReadiness();
    }

    function renderSummary() {
      const readinessOk = state.readiness && state.readiness.ok;
      const commands = Array.isArray(state.commands) ? state.commands.length : 0;
      const html = [
        metric('Readiness', readinessOk ? 'Ready' : 'Check', readinessOk ? 'Project checks passed' : 'Review setup state'),
        metric('Runs', state.runs.length, 'recent workspaces'),
        metric('Campaigns', state.campaigns.length, 'local specs'),
        metric('Events', state.events.length, 'latest entries')
      ];
      if (commands) {
        html.push(metric('CLI Surface', commands, 'public operations'));
      }
      document.getElementById('summary').innerHTML = html.join('');
    }

    function renderGraph() {
      const graph = state.campaignGraph;
      const nodes = graph && Array.isArray(graph.nodes) ? graph.nodes : [];
      const errors = renderErrors();
      const nodeHtml = nodes.length
        ? nodes.map((node) => '<div class="node ' + escapeHtml(node.status || 'unknown') + '"><div class="node-title">' + escapeHtml(node.id || 'stage') + '</div><div class="node-status">' + escapeHtml(node.status || 'unknown') + '</div><div class="muted">' + escapeHtml(node.workspace || '') + '</div></div>').join('')
        : '<p class="muted">No campaign graph detected. Add or select a campaign spec to populate this view.</p>';
      document.getElementById('tab-graph').innerHTML = '<div class="grid"><div class="panel"><h2>Campaign Graph</h2><div class="graph">' + nodeHtml + '</div></div><div class="panel"><h2>Operational Notes</h2>' + errors + '<p class="muted">This dashboard reads public JSON commands only. Launch, repair, resume, and edit controls are intentionally absent in v1.</p></div></div>';
    }

    function renderRuns() {
      document.getElementById('tab-runs').innerHTML = '<div class="panel"><h2>Runs</h2>' + table(['Run', 'Status', 'Stage', 'Budget', 'Task'], state.runs.map((run) => [
        run.run_id,
        run.status,
        run.current_stage || '-',
        budget(run.budget),
        run.task || '-'
      ])) + '</div>';
    }

    function renderCampaigns() {
      document.getElementById('tab-campaigns').innerHTML = '<div class="panel"><h2>Campaigns</h2>' + table(['Name', 'Path', 'Size'], state.campaigns.map((campaign) => [
        campaign.name,
        campaign.path,
        String(campaign.size_bytes || 0)
      ])) + '</div>';
    }

    function renderEvents() {
      const rows = state.events.length
        ? state.events.map((event) => '<div class="event"><strong>' + escapeHtml(event.kind) + '</strong><div>' + escapeHtml(event.summary) + '</div><div class="muted">' + escapeHtml(event.timestamp || '') + '</div></div>').join('')
        : '<p class="muted">No product-shell events yet.</p>';
      document.getElementById('tab-events').innerHTML = '<div class="panel"><h2>Events</h2><div class="event-list">' + rows + '</div></div>';
    }

    function renderOpenClaude() {
      const readiness = state.openclaude || {};
      const env = state.openclaudeEnv && state.openclaudeEnv.env ? state.openclaudeEnv.env : {};
      const rows = [
        ['OpenClaude binary', readiness.openclaude_available ? 'available' : 'missing'],
        ['OpenRouter key', readiness.openrouter_configured ? 'configured' : 'missing'],
        ['Skill', readiness.skill_exists ? readiness.skill_path : 'missing'],
        ['Launch ready', readiness.launch_ready ? 'yes' : 'no'],
        ['Model', readiness.model || '-'],
        ['Base URL', readiness.base_url || env.OPENAI_BASE_URL || '-']
      ];
      const envRows = Object.entries(env).map(([key, value]) => [key, value == null ? 'unset' : value]);
      document.getElementById('tab-openclaude').innerHTML = '<div class="grid"><div class="panel"><h2>OpenClaude Handoff</h2>' + table(['Check', 'State'], rows) + '<p class="muted">Stage 6 uses a configuration-first handoff. Chat embedding remains future work; launch with integrations/openclaude/launch_openclaude_msc.sh or the OpenClaude CLI once ready.</p></div><div class="panel"><h2>Launch Environment</h2>' + table(['Variable', 'Value'], envRows) + '</div></div>';
    }

    function renderReadiness() {
      const checks = state.readiness && state.readiness.checks ? Object.entries(state.readiness.checks) : [];
      const capabilities = state.capabilities && state.capabilities.capabilities ? state.capabilities.capabilities : [];
      const setup = state.setupState && state.setupState.setup ? state.setupState.setup : {};
      const openclaw = state.openclaw || {};
      const setupRows = Object.entries(setup)
        .filter(([key, value]) => typeof value === 'boolean' || key === 'credential_source' || key === 'config_dir')
        .map(([key, value]) => [key, typeof value === 'boolean' ? (value ? 'ok' : 'missing') : value || '-']);
      setupRows.push(['openclaw_config', openclaw.configured ? openclaw.config_path : 'not configured']);
      setupRows.push(['openclaw_profile', openclaw.default_profile || 'read_only']);
      const planRows = state.tutorialPlan && Array.isArray(state.tutorialPlan.steps)
        ? state.tutorialPlan.steps.map((step) => [step.id, step.command])
        : [];
      document.getElementById('tab-readiness').innerHTML = '<div class="grid"><div class="panel"><h2>Readiness</h2>' + table(['Check', 'State'], checks.map(([key, value]) => [key, value ? 'ok' : 'missing'])) + '<h2>Setup</h2>' + table(['Item', 'State'], setupRows) + '</div><div class="panel"><h2>Capabilities</h2>' + table(['Capability'], capabilities.map((item) => [item])) + '<h2>Tutorial Plan</h2>' + table(['Step', 'Command'], planRows) + '</div></div>';
    }

    function renderErrors() {
      if (!state.errors || state.errors.length === 0) {
        return '<p class="muted">No command errors reported.</p>';
      }
      return '<div class="error">' + state.errors.map((error) => '<div>' + escapeHtml(error.command || 'command') + ': ' + escapeHtml(error.message || 'failed') + '</div>').join('') + '</div>';
    }

    function metric(label, value, detail) {
      return '<div class="metric"><span class="muted">' + escapeHtml(label) + '</span><strong>' + escapeHtml(String(value)) + '</strong><span class="muted">' + escapeHtml(detail) + '</span></div>';
    }

    function table(headers, rows) {
      if (!rows || rows.length === 0) {
        return '<p class="muted">No data.</p>';
      }
      return '<table><thead><tr>' + headers.map((header) => '<th>' + escapeHtml(header) + '</th>').join('') + '</tr></thead><tbody>' + rows.map((row) => '<tr>' + row.map((cell) => '<td>' + escapeHtml(String(cell == null ? '' : cell)) + '</td>').join('') + '</tr>').join('') + '</tbody></table>';
    }

    function budget(value) {
      if (!value || value.total_usd == null) {
        return '-';
      }
      return '$' + Number(value.total_usd).toFixed(2);
    }

    function shortTime(value) {
      try {
        return new Date(value).toLocaleTimeString();
      } catch (_) {
        return 'Updated';
      }
    }

    function escapeHtml(value) {
      return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    render();
  </script>
</body>
</html>`;
}

function getNonce() {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let text = '';
  for (let i = 0; i < 32; i += 1) {
    text += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return text;
}

module.exports = {
  activate,
  deactivate,
  collectDashboardData,
  resolveMscBin
};
