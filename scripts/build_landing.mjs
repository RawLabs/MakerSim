import { build } from '../frontend/node_modules/vite/dist/node/index.js';
import { copyFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
execFileSync(resolve(root,'.venv/bin/python'),['-m','scripts.build_landing_example'],{cwd:root,stdio:'inherit'});
await build({configFile:false,publicDir:false,build:{outDir:resolve(root,'deploy/landing/assets'),emptyOutDir:false,
  rollupOptions:{input:resolve(root,'deploy/hero/hero.js'),output:{entryFileNames:'hero.js',chunkFileNames:'hero-[hash].js',assetFileNames:'hero[extname]'}}}});
await copyFile(resolve(root,'frontend/src/vendor/LICENSE'),resolve(root,'deploy/landing/assets/THREE_LICENSE.txt'));
