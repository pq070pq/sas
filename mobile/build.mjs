import { mkdir, readFile, writeFile, cp } from 'node:fs/promises';
import { resolve } from 'node:path';
import { build } from 'esbuild';

const root = process.cwd();
const out = resolve(root, 'www');
await mkdir(out, { recursive: true });
const template = await readFile(resolve(root, 'src/index.html'), 'utf8');
const apiBaseUrl = (process.env.SAS_API_BASE || process.env.SAS_APP_URL || 'https://vps-b67ef07b.vps.ovh.ca').replace(/\\/$/, '');
await writeFile(resolve(out, 'index.html'), template.replace('__SAS_MOBILE_CONFIG__', JSON.stringify({ apiBaseUrl })));
await cp(resolve(root, 'src/styles.css'), resolve(out, 'styles.css'));
await build({ entryPoints: [resolve(root, 'src/app.js')], outfile: resolve(out, 'app.js'), bundle: true, platform: 'browser', format: 'esm', target: ['es2020'], sourcemap: false, minify: true });
