import { mkdir, readFile, writeFile, cp } from 'node:fs/promises';
import { resolve } from 'node:path';
import { build } from 'esbuild';

const root = process.cwd();
const out = resolve(root, 'www');
await mkdir(out, { recursive: true });
const template = await readFile(resolve(root, 'src/index.html'), 'utf8');
const apiBaseUrl = (process.env.SAS_API_BASE || '').replace(/\\/+$/, '');
if (!apiBaseUrl) throw new Error('SAS_API_BASE must point to an isolated staging API before building.');
if (apiBaseUrl === 'https://vps-b67ef07b.vps.ovh.ca') throw new Error('Production API builds are blocked for the standalone app.');
await writeFile(resolve(out, 'index.html'), template.replace('__SAS_MOBILE_CONFIG__', JSON.stringify({ apiBaseUrl })));
await cp(resolve(root, 'src/styles.css'), resolve(out, 'styles.css'));
await build({ entryPoints: [resolve(root, 'src/app.js')], outfile: resolve(out, 'app.js'), bundle: true, platform: 'browser', format: 'esm', target: ['es2020'], sourcemap: false, minify: true });
