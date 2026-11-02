import { mkdir, readFile, writeFile, cp } from 'node:fs/promises';
import { resolve } from 'node:path';

const root = resolve('mobile');
const out = resolve(root, 'www');
await mkdir(out, { recursive: true });
const template = await readFile(resolve(root, 'src/index.html'), 'utf8');
const config = {
  botUsername: process.env.SAS_TELEGRAM_BOT_USERNAME || '',
  webAppShortName: process.env.SAS_TELEGRAM_WEBAPP_SHORT_NAME || '',
  webUrl: process.env.SAS_APP_URL || '',
};
await writeFile(resolve(out, 'index.html'), template.replace('__SAS_MOBILE_CONFIG__', JSON.stringify(config)));
await cp(resolve(root, 'src/styles.css'), resolve(out, 'styles.css'));
