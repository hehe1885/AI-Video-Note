// One entry point for local checks and CI; newly added smoke tests run automatically.
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
function files(directory, predicate) {
    return fs.readdirSync(path.join(root, directory), { withFileTypes: true })
        .filter(entry => entry.isFile() && predicate(entry.name))
        .map(entry => path.join(directory, entry.name)).sort();
}
function run(args) {
    const result = spawnSync(process.execPath, args, { cwd: root, stdio: 'inherit' });
    if (result.error) throw result.error;
    if (result.status !== 0) {
        console.error(`Frontend check failed: ${args.join(' ')}`);
        process.exit(result.status || 1);
    }
}
const sources = [...files('frontend', name => name.endsWith('.js')),
    ...files('frontend/locales', name => name.endsWith('.js'))];
const smokeTests = files('tests', name => /^frontend_.*_smoke\.cjs$/.test(name));
if (!smokeTests.length) throw new Error('No frontend smoke tests found');
for (const source of sources) run(['--check', source]);
for (const test of smokeTests) run([test]);
console.log(`Frontend checks passed: ${sources.length} scripts, ${smokeTests.length} smoke tests`);
