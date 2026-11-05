// Compile the pinned SDK classes with TypeScript and compare synthetic proofs.
// Test dependencies live in the isolated workspace, outside the desktop runtime.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const root = path.resolve(process.argv[2]);
const require = createRequire(path.join(root, 'package.json'));
const ts = require('typescript');
fs.mkdirSync(path.join(root, 'compiled'), { recursive: true });
for (const name of ['Address', 'Account']) {
  const source = fs.readFileSync(path.join(root, 'upstream', name + '.ts'), 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  }).outputText.replace('"./Address"', '"./Address.mjs"');
  fs.writeFileSync(path.join(root, 'compiled', name + '.mjs'), output);
}
const { Account } = await import(pathToFileURL(path.join(root, 'compiled/Account.mjs')));
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const account = Account.fromPrivateKeyHex(input.privateKeyHex);
const results = input.cases.map(c => {
  const bytes = Buffer.from(c.messageBase64, 'base64');
  const message = c.asString ? bytes.toString('utf8') : bytes;
  return {
    name: c.name,
    signature: account.signBytes(message).signature,
    recovered: Account.recoverAddress(message, c.signature).hex,
  };
});
process.stdout.write(JSON.stringify({ address: account.address.hex, results }));
