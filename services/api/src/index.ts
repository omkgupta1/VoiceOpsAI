/**
 * VoiceOps platform API — process entry point.
 *
 * The only public entry point to the platform. Owns authentication and RBAC,
 * reads calls, jobs and analytics from Postgres, talks to the queue, and proxies
 * voice turns to the AI service — which stays internal.
 *
 * The app itself lives in `app.ts`; this file only starts and stops it.
 */
// First import in the process, before anything binds a library handle that
// OpenTelemetry is about to replace. See the note at the top of telemetry.ts.
import './telemetry.js';

import { buildApp } from './app.js';
import { config } from './config.js';
import { pool } from './lib/db.js';
import { redis } from './lib/queue.js';

const app = await buildApp();

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, async () => {
    app.log.info('shutting down');
    await app.close();
    await pool.end();
    redis.disconnect();
    process.exit(0);
  });
}

await app.listen({ port: config.port, host: '0.0.0.0' });
