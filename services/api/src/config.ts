/**
 * Configuration, read from the environment with the repo-root .env as a fallback.
 *
 * The repo keeps one .env at its root rather than one per service, so the Python
 * and TypeScript sides cannot drift apart on things like the queue namespace —
 * which they must agree on exactly to see the same jobs.
 */
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

function findRootEnv(): Record<string, string> {
  let dir = dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 6; i += 1) {
    const candidate = join(dir, '.env');
    if (existsSync(candidate)) {
      return Object.fromEntries(
        readFileSync(candidate, 'utf8')
          .split('\n')
          .map((line) => line.trim())
          .filter((line) => line && !line.startsWith('#') && line.includes('='))
          .map((line) => {
            const index = line.indexOf('=');
            return [line.slice(0, index), line.slice(index + 1).replace(/^["']|["']$/g, '')];
          }),
      );
    }
    dir = resolve(dir, '..');
  }
  return {};
}

const fileEnv = findRootEnv();
const get = (key: string, fallback: string): string => process.env[key] ?? fileEnv[key] ?? fallback;

export const config = {
  env: get('ENV', 'local'),
  port: Number(get('API_PORT', '3000')),
  logLevel: get('LOG_LEVEL', 'info'),

  databaseUrl: get('DATABASE_URL', 'postgresql://voiceops:voiceops@localhost:5432/voiceops'),
  redisUrl: get('REDIS_URL', 'redis://localhost:6379/0'),
  queueNamespace: get('QUEUE_NAMESPACE', 'vo'),

  // The AI service is never exposed publicly; this API is the only way in.
  // It holds the confirmation gate, and an internet-reachable turn endpoint
  // would be a way around it.
  //
  // Addressed by IP, not by `localhost`: Node 18+ resolves localhost to ::1
  // first, and a server bound to 127.0.0.1 is IPv4-only — which surfaces as an
  // unhelpful "fetch failed" rather than a connection refused.
  aiServiceUrl: get('AI_SERVICE_URL', 'http://127.0.0.1:8000'),

  jwtSecret: get('JWT_SECRET', 'dev-only-change-me'),
  jwtExpiresIn: get('JWT_EXPIRES_IN', '8h'),
} as const;
