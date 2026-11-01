const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const front = path.join(__dirname, '../frontend');
const katex = require(path.join(front, 'vendor/katex/katex.min.js'));
const sandbox = {console, katex, API_BASE:'/api', document:{addEventListener(){}}, escapeHtml:s=>s, window:{}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(front, 'vendor/katex/mhchem.min.js'),'utf8'), sandbox);
vm.runInContext(fs.readFileSync(path.join(front, 'note-view.js'),'utf8'), sandbox);
vm.runInContext('escapeHtml = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")', sandbox);
const run = code => vm.runInContext(code,sandbox);
sandbox.content = 'Inline $x_1^2$; code ' + String.fromCharCode(96) + '$skip$' + String.fromCharCode(96) + String.raw`.
~~~tex
$also_skip$
~~~
\(v=at\) and \[E=mc^2\]; escaped \$5.
$$\ce{2H2 + O2 -> 2H2O}$$`;
const extracted = run('extractMathSegments(content)');
assert.equal(extracted.segments.length,4);
assert.ok(extracted.markdown.includes('$also_skip$'));
assert.ok(extracted.markdown.includes('$skip$'));
const rendered = run('restoreMathSegments(extractMathSegments(content).markdown, extractMathSegments(content).segments)');
assert.ok(rendered.includes('katex'));
assert.ok(!rendered.includes('katex-error'));
const plain = run('stripMarkdown(content)');
assert.ok(plain.includes('$x_1^2$'));
assert.ok(plain.includes('$$\\ce{2H2 + O2 -> 2H2O}$$'));
console.log('Math/physics/chemistry, alternate delimiters, code protection and text source preservation passed');
