import { mkdir, readFile, writeFile, cp } from 'node:fs/promises';
import { resolve } from 'node:path';

const root = process.cwd();
const out = resolve(root, 'www');
await mkdir(out, { recursive: true });
const template = await readFile(resolve(root, 'src/index.html'), 'utf8');
const config = {
  // Public app-routing values; environment variables may override these defaults.
  botUsername: process.env.SAS_TELEGRAM_BOT_USERNAME || 'SAS_07_bot',
  webAppShortName: process.env.SAS_TELEGRAM_WEBAPP_SHORT_NAME || 'saspro',
  webUrl: process.env.SAS_APP_URL || 'https://vps-b67ef07b.vps.ovh.ca/',
};
await writeFile(resolve(out, 'index.html'), template.replace('__SAS_MOBILE_CONFIG__', JSON.stringify(config)));
await cp(resolve(root, 'src/styles.css'), resolve(out, 'styles.css'));
