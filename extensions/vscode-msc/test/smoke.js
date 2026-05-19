const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const Module = require('module');

const root = path.resolve(__dirname, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const extensionSource = fs.readFileSync(path.join(root, 'extension.js'), 'utf8');
const uiSource = fs.readFileSync(path.join(root, 'webview-ui', 'src', 'main.jsx'), 'utf8');

const originalLoad = Module._load;
Module._load = function load(request, parent, isMain) {
  if (request === 'vscode') {
    return {
      commands: { registerCommand: () => ({ dispose() {} }), executeCommand: async () => undefined },
      env: {},
      Uri: { file: (filePath) => ({ fsPath: filePath }) },
      ViewColumn: { One: 1 },
      window: { createWebviewPanel: () => ({}) },
      workspace: { workspaceFolders: null }
    };
  }
  return originalLoad(request, parent, isMain);
};
const extension = require(path.join(root, 'extension.js'));
Module._load = originalLoad;

assert.strictEqual(pkg.main, './extension.js');
// VSCode auto-generates onCommand:* activation events from the commands
// contribution, so we no longer list them explicitly. The setup-on-first-run
// nudge needs onStartupFinished.
assert(pkg.activationEvents.includes('onStartupFinished'));
assert(pkg.contributes.commands.some((command) => command.command === 'mscDashboard.open'));
assert(pkg.contributes.commands.some((command) => command.command === 'mscDashboard.setKeys'));
assert(pkg.scripts.build.includes('vite build'));

for (const forbidden of [
  'openclaw start',
  'openclaw repair',
  'openclaude start',
  'openclaude launch',
  ' campaign start',
  ' campaign repair',
  ' resume '
]) {
  assert(!extensionSource.toLowerCase().includes(forbidden), `extension must not call ${forbidden}`);
}

for (const expected of [
  "message.type === 'selectCampaign'",
  "message.type === 'backToCampaigns'",
  "message.type === 'createCampaign'",
  "message.type === 'deleteCampaign'",
  "message.type === 'openSettings'",
  "message.type === 'refreshCampaign'",
  "message.type === 'selectGraphNode'",
  "message.type === 'previewArtifact'",
  "message.type === 'openArtifact'",
  "message.type === 'startCampaign'",
  "message.type === 'stopCampaign'",
  "message.type === 'interruptRun'",
  "message.type === 'sendInstruction'",
  "message.type === 'submitFeedback'",
  "message.type === 'openClaudeStart'",
  "message.type === 'openClaudeSend'",
  "message.type === 'openClaudeRestartModel'",
  "message.type === 'openClaudeClearHistory'",
  "message.type === 'linkArtifactContext'",
  "message.type === 'updateContextLink'",
  "message.type === 'openOnboarding'",
  "message.type === 'refreshKeyStatus'",
  "message.type === 'setApiKey'",
  "message.type === 'unsetApiKey'",
  "['campaigns', '--root', root, 'workspace'",
  "['project', 'readiness', '--json']",
  "['openclaude', 'readiness', '--json']",
  "['openclaude', 'models', '--json']",
  "['config', 'keys', 'list', '--json']",
  "'context',",
  "'link',"
]) {
  assert(extensionSource.includes(expected), `missing expected protocol or read surface: ${expected}`);
}
assert(extensionSource.includes('Message received by extension backend.'));
assert(extensionSource.includes('findProjectRoot'));

assert(uiSource.includes('Campaign Workspace'));
assert(uiSource.includes('Research Graph'));
assert(uiSource.includes('AI Helper'));
assert(uiSource.includes('FloatingOpenClaude'));
assert(uiSource.includes('Ask OpenClaude to drive the campaign'));
assert(uiSource.includes('Link to Chat'));
assert(uiSource.includes('Clear History'));
assert(uiSource.includes('saved chat message'));
assert(uiSource.includes('chatEndRef'));
assert(uiSource.includes('Autonomous Action Log'));
assert(uiSource.includes('New Campaign'));
assert(uiSource.includes('DeleteCampaignModal'));
assert(uiSource.includes('deleteTargetForCampaign'));
assert(uiSource.includes('deleteCampaignResult'));
assert(uiSource.includes('campaignMatchesAny'));
assert(uiSource.includes('Start automatically after creation'));
assert(uiSource.includes('tier default'));
assert(uiSource.includes('Create and Start Campaign'));
assert(uiSource.includes('Dry validation (no spend)'));
assert(uiSource.includes('dryRun: true'));
assert(uiSource.includes('Type DELETE to confirm'));
assert(!uiSource.includes('window.prompt'));
assert(uiSource.includes('Start Campaign'));
assert(uiSource.includes('Diagnostics'));
assert(uiSource.includes('OnboardingPanel'));
assert(uiSource.includes('Configure API Keys'));
assert(uiSource.includes("vscode.postMessage({ type: 'setApiKey'"));
assert(uiSource.includes("vscode.postMessage({ type: 'unsetApiKey'"));
assert(uiSource.includes('ErrorSummary'));
assert(uiSource.includes('LoadingNotice'));
assert(uiSource.includes('DashboardLoadingState'));
assert(uiSource.includes('state.loading && !state.loaded'));
assert(uiSource.includes('CampaignExecutionPanel'));
assert(uiSource.includes('Continue Campaign'));
assert(uiSource.includes('diagnostic log lines'));
assert(uiSource.includes('Process Log'));
assert(uiSource.includes('RUN LOCAL'));
assert(uiSource.includes("['decisions', 'deliverables', 'feedback', 'diagnostics']"));
assert(uiSource.includes('DecisionsTab'));
assert(uiSource.includes('DeliverablesTab'));
assert(uiSource.includes('HumanFeedbackForm'));
assert(uiSource.includes('Record Feedback'));
assert(uiSource.includes('No local process is attached to this dashboard.'));
assert(uiSource.includes('Assistant readiness'));
assert(uiSource.includes('Dashboard Checks'));
assert(!uiSource.includes('No budget summary available.'));
assert(!uiSource.includes('No config output available.'));
assert(uiSource.includes("useState('existing')"));
assert(uiSource.includes("useState('deliverables')"));
assert(uiSource.includes('No produced deliverables found for the current filters.'));
assert(uiSource.includes('produced'));
assert(uiSource.includes('planned'));
assert(uiSource.includes('InteractiveGraph'));
assert(uiSource.includes('graph-canvas'));
assert(uiSource.includes('layoutGraph'));
assert(uiSource.includes('graphEdgesFromState'));
assert(uiSource.includes("setTab('deliverables')"));
assert(uiSource.includes("document.getElementById('deliverables-preview')"));
assert(uiSource.includes('onPreviewArtifact'));
assert(uiSource.includes('id="deliverables-preview"'));
assert(uiSource.includes('Purpose'));
assert(uiSource.includes('Validators'));
assert(uiSource.includes('Preview'));
assert(uiSource.includes('target research'));
assert(uiSource.includes('consortium scaffold'));
assert(uiSource.includes('standard'));
assert(uiSource.includes('serious'));
assert(uiSource.includes('consortium_budget'));
assert(uiSource.includes('Bundle exports'));
assert(!uiSource.includes('YAML exports'));
assert(!uiSource.includes('Local mode'));
assert(fs.readFileSync(path.join(root, 'webview-ui', 'src', 'styles.css'), 'utf8').includes('artifact-library-panel .artifact-list'));
assert(fs.readFileSync(path.join(root, 'webview-ui', 'src', 'styles.css'), 'utf8').includes('height: calc(100vh - 220px)'));
assert(extensionSource.includes('bundleExportRoot'));
assert(extensionSource.includes("'delete'"));
assert(extensionSource.includes("'--confirm'"));
assert(extensionSource.includes('postDeleteCampaignResult'));
assert(extensionSource.includes('filterDeletedCampaign'));
assert(extensionSource.includes('Closing a dashboard panel should not be a destructive run-control action'));
assert(extensionSource.includes('refreshPromise'));
assert(extensionSource.includes('campaignRunSummary'));
assert(extensionSource.includes("'feedback'"));
assert(extensionSource.includes("'create'"));
assert(extensionSource.includes('openClaude'));
assert(extensionSource.includes('stream-json'));
assert(extensionSource.includes('--verbose'));
assert(extensionSource.includes('bypassPermissions'));
assert(extensionSource.includes('Bash,Read,Grep,Glob'));
assert(extensionSource.includes('context-pack'));
assert(extensionSource.includes('.msc'));
assert(extensionSource.includes('openclaude_chats'));
assert(extensionSource.includes('chatHistoryForPrompt'));
assert(!extensionSource.includes('fs.writeFileSync(campaignPath'));

const parsed = extension.consumeJsonLines('{"type":"content_block_delta","delta":{"text":"Hello"}}\n{"type":"tool","name":"Bash"}\npartial');
assert.strictEqual(parsed.items.length, 2);
assert.strictEqual(parsed.remainder, 'partial');
assert.strictEqual(extension.textFromOpenClaudeEvent(parsed.items[0]), 'Hello');
assert.strictEqual(
  extension.textFromOpenClaudeEvent({ type: 'stream_event', event: { type: 'content_block_delta', delta: { text: ' streamed' } } }),
  ' streamed'
);
assert.strictEqual(
  extension.textFromOpenClaudeEvent({ type: 'result', result: 'done' }, true),
  ''
);
const prompt = extension.buildOpenClaudePrompt(
  { state: { selectedCampaign: 'demo-campaign', openClaude: { transcript: [
    { role: 'user', text: 'Earlier question', timestamp: '2026-05-14T00:00:00Z' },
    { role: 'assistant', text: 'Earlier answer', timestamp: '2026-05-14T00:00:01Z' },
    { role: 'user', text: 'Review the matrix.', timestamp: '2026-05-14T00:00:02Z' }
  ] } } },
  'Review the matrix.',
  {
    schema: 'msc.openclaude.context_pack.v1',
    campaign: { id: 'demo-campaign' },
    selected_artifacts: [{ path: 'artifacts/literature_matrix.md' }]
  }
);
assert(prompt.includes('Review the matrix.'));
assert(prompt.includes('artifacts/literature_matrix.md'));
assert(prompt.includes('Earlier question'));
assert(prompt.includes('Earlier answer'));
assert(prompt.includes('repo-local prefix'));
assert(prompt.includes('Do not assume bare msc is on PATH.'));
assert(!extension.chatHistoryForPrompt([{ role: 'user', text: 'Current' }], 'Current').includes('Current'));
assert(extension.openClaudeChatPath('/tmp/project', 'campaign/name').includes('openclaude_chats'));
const repoRoot = path.resolve(root, '..', '..');
assert.strictEqual(extension.findProjectRoot(path.dirname(repoRoot)), repoRoot);
assert.strictEqual(extension.findProjectRoot(path.dirname(path.dirname(repoRoot))), repoRoot);
assert.strictEqual(extension.findProjectRoot(path.join(repoRoot, 'extensions', 'vscode-msc'), path.join(repoRoot, 'extensions', 'vscode-msc')), repoRoot);

const mscCommand = extension.resolveMscCommand(repoRoot);
assert(mscCommand.bin.endsWith('/msc') || mscCommand.prefixArgs.includes('consortium.cli.main'));
assert(mscCommand.prefixArgs.includes('--no-banner'));

const args = extension.buildRunArgs({
  task: 'Smoke task',
  dryRun: true,
  tier: 'lean',
  outputFormat: 'markdown',
  budget: 20,
  counsel: false,
  math: false,
  treeSearch: false
});
assert.strictEqual(args[0], 'run');
assert(args.includes('--dry-run'));
assert(args.includes('--tier') && args.includes('lean'));
assert(args.includes('--output-format') && args.includes('markdown'));
assert(args.includes('--no-counsel'));
assert(args.includes('--no-math'));
assert(args.includes('--no-tree-search'));
assert.strictEqual(args.includes('--model'), false);
const attachedArgs = extension.buildRunArgs({
  task: 'Attached smoke',
  dryRun: true,
  tier: 'lean',
  outputFormat: 'markdown',
  budget: 20,
  model: 'gpt-5-mini',
  maxRunSeconds: 3600,
  counsel: false,
  math: false,
  treeSearch: false,
  campaignId: 'demo-campaign',
  campaignGraphVersion: 1
}, root);
assert(attachedArgs.includes('--campaign-id') && attachedArgs.includes('demo-campaign'));
assert(attachedArgs.includes('--campaign-root') && attachedArgs.includes(root));
assert(attachedArgs.includes('--campaign-graph-version') && attachedArgs.includes('1'));
assert(attachedArgs.includes('--model') && attachedArgs.includes('gpt-5-mini'));
assert(attachedArgs.includes('--max-run-seconds') && attachedArgs.includes('3600'));
const scaffoldOptions = extension.normalizeRunOptions({
  task: 'Scaffold task',
  dryRun: false,
  tier: 'scaffold',
  outputFormat: 'markdown',
  budget: 1,
  model: 'gpt-5-mini',
  maxRunSeconds: 3600,
  allowSpend: true,
  confirmation: 'RUN LOCAL'
});
assert.strictEqual(scaffoldOptions.tier, 'scaffold');
assert.strictEqual(scaffoldOptions.model, 'gpt-5-mini');
assert.strictEqual(scaffoldOptions.maxRunSeconds, 3600);
assert.strictEqual(extension.validateRunOptions(scaffoldOptions), null);
assert.strictEqual(
  extension.validateRunOptions({ task: 'Spend', dryRun: false, budget: 20, allowSpend: false, confirmation: '' }),
  'Real local execution requires allow spend plus confirmation text RUN LOCAL.'
);

const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'msc-extension-'));
try {
  const inside = path.join(tempRoot, 'artifact.md');
  fs.writeFileSync(inside, '# artifact\n', 'utf8');
  assert.strictEqual(
    extension.safeResolveArtifactPath(tempRoot, { stages: [], workspace_root: 'results/low-rank-fine-tuning' }, { path: 'artifact.md' }),
    inside
  );
  const stageFile = path.join(tempRoot, 'results', 'demo', 'brainstorm_agent', 'artifacts', 'brainstorm.md');
  fs.mkdirSync(path.dirname(stageFile), { recursive: true });
  fs.writeFileSync(stageFile, '# brainstorm\n', 'utf8');
  assert.strictEqual(
    extension.safeResolveArtifactPath(
      tempRoot,
      { stages: [], workspace_root: 'results/demo' },
      { path: 'artifacts/brainstorm.md', workspace: 'results/demo/brainstorm_agent', stage_id: 'brainstorm_agent' }
    ),
    stageFile
  );
  const stageOnlyFile = path.join(tempRoot, 'results', 'demo', 'literature_review_agent', 'artifacts', 'literature.md');
  fs.mkdirSync(path.dirname(stageOnlyFile), { recursive: true });
  fs.writeFileSync(stageOnlyFile, '# literature\n', 'utf8');
  assert.strictEqual(
    extension.safeResolveArtifactPath(
      tempRoot,
      { stages: [{ stage_id: 'literature_review_agent', workspace: 'results/demo/literature_review_agent' }], workspace_root: 'results/demo' },
      { path: 'artifacts/literature.md', stage_id: 'literature_review_agent' }
    ),
    stageOnlyFile
  );
  const parentRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'msc-parent-'));
  const repoRoot = path.join(parentRoot, 'PoggioAI_MSc');
  const runtimeFile = path.join(repoRoot, 'results', 'consortium_20260511_101645', 'paper_workspace', 'research_proposal.md');
  fs.mkdirSync(path.dirname(runtimeFile), { recursive: true });
  fs.mkdirSync(path.join(repoRoot, 'campaigns', 'test'), { recursive: true });
  fs.writeFileSync(runtimeFile, '# proposal\n', 'utf8');
  assert.strictEqual(
    extension.safeResolveArtifactPath(
      parentRoot,
      {
        path: path.join(repoRoot, 'campaigns', 'test'),
        provenance: { source: path.join(repoRoot, '.msc', 'campaigns.db') },
        stages: []
      },
      {
        path: 'paper_workspace/research_proposal.md',
        workspace: 'results/consortium_20260511_101645',
        stage_id: 'persona_council'
      }
    ),
    runtimeFile
  );
  const renderedMarkdown = extension.renderArtifactHtml('# Finding\n\n- **Strong** evidence\n\n| Metric | Value |\n| --- | --- |\n| Cost | Low |\n', '.md');
  assert(renderedMarkdown.includes('<h1>Finding</h1>'));
  assert(renderedMarkdown.includes('<strong>Strong</strong>'));
  assert(renderedMarkdown.includes('<table>'));
  const renderedLatex = extension.renderArtifactHtml('\\section{Result}\nThis is \\textbf{important} and $x^2$.\n\\begin{itemize}\n\\item First\n\\end{itemize}', '.tex');
  assert(renderedLatex.includes('<h2>Result</h2>'));
  assert(renderedLatex.includes('<strong>important</strong>'));
  assert(renderedLatex.includes('class="math-inline"'));
  fs.rmSync(parentRoot, { recursive: true, force: true });
  const outside = path.join(os.tmpdir(), 'msc-extension-outside.txt');
  fs.writeFileSync(outside, 'outside', 'utf8');
  assert.throws(
    () => extension.safeResolveArtifactPath(tempRoot, { stages: [] }, { path: outside }),
    /outside allowed roots/
  );
} finally {
  fs.rmSync(tempRoot, { recursive: true, force: true });
}
