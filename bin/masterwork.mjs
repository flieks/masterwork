#!/usr/bin/env node
/**
 * One command instead of two terminals: prepares the database, starts the API
 * and the web server, and opens the browser. Ctrl-C stops both.
 */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createConnection } from 'node:net';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const BACKEND = join(ROOT, 'backend');
const FRONTEND = join(ROOT, 'frontend');

const API_PORT = Number(process.env.MASTERWORK_API_PORT ?? 8008);
const WEB_PORT = Number(process.env.MASTERWORK_WEB_PORT ?? 5192);

const c = {
  dim: (s) => `\x1b[2m${s}\x1b[0m`,
  bold: (s) => `\x1b[1m${s}\x1b[0m`,
  green: (s) => `\x1b[32m${s}\x1b[0m`,
  red: (s) => `\x1b[31m${s}\x1b[0m`,
  yellow: (s) => `\x1b[33m${s}\x1b[0m`,
};

function die(msg, hint) {
  console.error(`${c.red('✗')} ${msg}`);
  if (hint) console.error(`  ${c.dim(hint)}`);
  process.exit(1);
}

function has(cmd) {
  return spawnSync(cmd, ['--version'], { stdio: 'ignore' }).status === 0;
}

function portFree(port) {
  return new Promise((resolve) => {
    const sock = createConnection({ port, host: '127.0.0.1' })
      .on('connect', () => (sock.destroy(), resolve(false)))
      .on('error', () => resolve(true));
  });
}

async function waitForHttp(url, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

function run(cmd, args, cwd) {
  const res = spawnSync(cmd, args, { cwd, stdio: 'inherit' });
  if (res.status !== 0) die(`\`${cmd} ${args.join(' ')}\` failed in ${cwd}`);
}

// ── preflight ────────────────────────────────────────────────────────────────
if (!existsSync(BACKEND) || !existsSync(FRONTEND)) {
  die('Run this from a Masterwork checkout.', `looked in ${ROOT}`);
}
if (!has('uv')) die('uv is not installed.', 'https://docs.astral.sh/uv/getting-started/');
if (!has('node')) die('Node is not installed.', 'Node 20 or newer is required.');
if (!has('claude')) {
  console.warn(
    `${c.yellow('!')} The \`claude\` CLI was not found — browsing and editing work, ` +
      `but chat, simulations and audits will fail until it is installed and signed in.`,
  );
}

for (const [port, what] of [
  [API_PORT, 'API'],
  [WEB_PORT, 'web server'],
]) {
  if (!(await portFree(port))) {
    die(
      `Port ${port} is already in use (needed for the ${what}).`,
      `Stop whatever is on it, or set MASTERWORK_${what === 'API' ? 'API' : 'WEB'}_PORT.`,
    );
  }
}

// ── prepare ──────────────────────────────────────────────────────────────────
console.log(c.bold('\nMasterwork\n'));
console.log(`${c.dim('→')} installing backend dependencies`);
run('uv', ['sync', '--quiet'], BACKEND);

console.log(`${c.dim('→')} applying database migrations`);
run('uv', ['run', 'alembic', 'upgrade', 'head'], BACKEND);

if (!existsSync(join(FRONTEND, 'node_modules'))) {
  console.log(`${c.dim('→')} installing frontend dependencies (first run only)`);
  run('npm', ['install', '--silent'], FRONTEND);
}

// ── start ────────────────────────────────────────────────────────────────────
const children = [];
function start(name, cmd, args, cwd, env) {
  const child = spawn(cmd, args, { cwd, env: { ...process.env, ...env }, stdio: 'pipe' });
  child.stdout.on('data', (b) => process.stdout.write(c.dim(`[${name}] `) + b));
  child.stderr.on('data', (b) => process.stderr.write(c.dim(`[${name}] `) + b));
  child.on('exit', (code) => {
    if (!shuttingDown) {
      console.error(`${c.red('✗')} ${name} exited (${code}) — shutting down`);
      shutdown(1);
    }
  });
  children.push(child);
  return child;
}

let shuttingDown = false;
function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) child.kill('SIGTERM');
  setTimeout(() => process.exit(code), 300);
}
process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

console.log(`${c.dim('→')} starting the API on :${API_PORT}`);
start('api', 'uv', ['run', 'uvicorn', 'app.main:app', '--port', String(API_PORT)], BACKEND);

if (!(await waitForHttp(`http://127.0.0.1:${API_PORT}/openapi.json`))) {
  die('The API did not start in time.');
}

console.log(`${c.dim('→')} starting the web server on :${WEB_PORT}`);
start('web', 'npm', ['run', 'dev', '--', '--port', String(WEB_PORT)], FRONTEND, {
  VITE_API_URL: `http://localhost:${API_PORT}`,
});

const url = `http://localhost:${WEB_PORT}`;
if (await waitForHttp(url)) {
  console.log(`\n${c.green('✓')} Masterwork is running at ${c.bold(url)}`);
  console.log(c.dim('  Ctrl-C to stop.\n'));
  const opener =
    process.platform === 'darwin' ? 'open' : process.platform === 'win32' ? 'start' : 'xdg-open';
  spawn(opener, [url], { stdio: 'ignore', detached: true }).unref();
} else {
  die('The web server did not start in time.');
}
