const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const extension = fs.readFileSync(path.join(root, 'extension.js'), 'utf8');

assert.strictEqual(pkg.main, './extension.js');
assert(pkg.activationEvents.includes('onCommand:mscDashboard.open'));
assert(pkg.contributes.commands.some((command) => command.command === 'mscDashboard.open'));

for (const forbidden of [
  ' refresh-manifest',
  ' request-action',
  ' execute-action',
  ' campaign start',
  ' campaign repair',
  ' resume '
]) {
  assert(!extension.includes(forbidden), `read-only extension must not call ${forbidden}`);
}

assert(extension.includes("['project', 'readiness', '--json']"));
assert(extension.includes("['runs', 'list', '--json']"));
assert(extension.includes("['selftest', 'commands', '--json']"));
assert(extension.includes("['openclaude', 'readiness', '--json']"));
assert(extension.includes("['openclaude', 'env', '--json']"));
