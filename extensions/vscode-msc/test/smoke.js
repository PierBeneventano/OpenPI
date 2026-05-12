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
assert(pkg.activationEvents.includes('onCommand:mscDashboard.open'));
assert(pkg.contributes.commands.some((command) => command.command === 'mscDashboard.open'));
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
  "message.type === 'openSettings'",
  "message.type === 'refreshCampaign'",
  "message.type === 'selectGraphNode'",
  "message.type === 'previewArtifact'",
  "message.type === 'openArtifact'",
  "message.type === 'startRun'",
  "message.type === 'stopRun'",
  "message.type === 'interruptRun'",
  "message.type === 'sendInstruction'",
  "message.type === 'submitFeedback'",
  "['campaigns', '--root', root, 'graph'",
  "['campaigns', '--root', root, 'inspect'",
  "['campaigns', '--root', root, 'artifacts'",
  "['campaigns', '--root', root, 'events'",
  "['project', 'readiness', '--json']",
  "['selftest', 'commands', '--json']",
  "['openclaude', 'readiness', '--json']",
  "['openclaude', 'env', '--json']"
]) {
  assert(extensionSource.includes(expected), `missing expected protocol or read surface: ${expected}`);
}

assert(uiSource.includes('Campaign Workspace'));
assert(uiSource.includes('New Campaign'));
assert(uiSource.includes('Start Local Run'));
assert(uiSource.includes('Diagnostics'));
assert(uiSource.includes('ErrorSummary'));
assert(uiSource.includes('LoadingNotice'));
assert(uiSource.includes('DashboardLoadingState'));
assert(uiSource.includes('state.loading && !state.loaded'));
assert(uiSource.includes('RunStatusPanel'));
assert(uiSource.includes('Start Campaign Run'));
assert(uiSource.includes('RUN LOCAL'));
assert(uiSource.includes("['graph', 'feedback', 'artifacts']"));
assert(uiSource.includes('HumanFeedbackForm'));
assert(uiSource.includes('Record Feedback'));
assert(uiSource.includes('Start First Local Run'));
assert(uiSource.includes('No local process is attached to this dashboard.'));
assert(uiSource.includes('Assistant readiness'));
assert(uiSource.includes('InteractiveGraph'));
assert(uiSource.includes('graph-canvas'));
assert(uiSource.includes('layoutGraph'));
assert(uiSource.includes('graphEdgesFromState'));
assert(uiSource.includes('Purpose'));
assert(uiSource.includes('Validators'));
assert(uiSource.includes('Preview'));
assert(uiSource.includes('consortium scaffold'));
assert(uiSource.includes('live-smoke'));
assert(uiSource.includes('consortium_budget'));
assert(uiSource.includes('Bundle exports'));
assert(!uiSource.includes('YAML exports'));
assert(!uiSource.includes('Local mode'));
assert(extensionSource.includes('bundleExportRoot'));
assert(extensionSource.includes('Closing a dashboard panel should not be a destructive run-control action'));
assert(extensionSource.includes('refreshPromise'));
assert(extensionSource.includes('campaignRunSummary'));
assert(extensionSource.includes("'feedback'"));
assert(extensionSource.includes("'create'"));
assert(!extensionSource.includes('fs.writeFileSync(campaignPath'));

const args = extension.buildRunArgs({
  task: 'Smoke task',
  dryRun: true,
  tier: 'budget',
  outputFormat: 'markdown',
  budget: 20,
  counsel: false,
  math: false,
  treeSearch: false
});
assert.deepStrictEqual(args.slice(0, 2), ['--no-banner', 'run']);
assert(args.includes('--dry-run'));
assert(args.includes('--tier') && args.includes('budget'));
assert(args.includes('--output-format') && args.includes('markdown'));
assert(args.includes('--no-counsel'));
assert(args.includes('--no-math'));
assert(args.includes('--no-tree-search'));
const attachedArgs = extension.buildRunArgs({
  task: 'Attached smoke',
  dryRun: true,
  tier: 'budget',
  outputFormat: 'markdown',
  budget: 20,
  counsel: false,
  math: false,
  treeSearch: false,
  campaignId: 'demo-campaign',
  campaignGraphVersion: 1
}, root);
assert(attachedArgs.includes('--campaign-id') && attachedArgs.includes('demo-campaign'));
assert(attachedArgs.includes('--campaign-root') && attachedArgs.includes(root));
assert(attachedArgs.includes('--campaign-graph-version') && attachedArgs.includes('1'));
const liveSmokeOptions = extension.normalizeRunOptions({
  task: 'Live smoke task',
  dryRun: false,
  tier: 'live-smoke',
  outputFormat: 'markdown',
  budget: 1,
  allowSpend: true,
  confirmation: 'RUN LOCAL'
});
assert.strictEqual(liveSmokeOptions.tier, 'live-smoke');
assert.strictEqual(extension.validateRunOptions(liveSmokeOptions), null);
assert.strictEqual(
  extension.validateRunOptions({ task: 'Spend', dryRun: false, budget: 20, allowSpend: false, confirmation: '' }),
  'Real local runs require allow spend plus confirmation text RUN LOCAL.'
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
