import { gzipSync } from 'node:zlib';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const assetsDirectory = join(process.cwd(), 'dist', 'assets');
const javascriptAssets = readdirSync(assetsDirectory).filter((name) => name.endsWith('.js'));

if (javascriptAssets.length === 0) {
  throw new Error('No production JavaScript assets found; run npm run build first.');
}

const failures = [];
for (const name of javascriptAssets) {
  const gzipKiB = gzipSync(readFileSync(join(assetsDirectory, name))).byteLength / 1024;
  const isEntry = name.startsWith('index-');
  const limitKiB = isEntry ? 150 : 100;
  console.log(`${name}: ${gzipKiB.toFixed(2)} KiB gzip (limit ${limitKiB} KiB)`);
  if (gzipKiB > limitKiB) failures.push(`${name} is ${gzipKiB.toFixed(2)} KiB gzip`);
}

if (failures.length > 0) {
  throw new Error(`Bundle budget exceeded: ${failures.join('; ')}`);
}
