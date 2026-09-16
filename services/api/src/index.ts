/**
 * VoiceOps platform API.
 *
 * The only public entry point. Owns authentication and RBAC, reads calls, jobs
 * and analytics from Postgres, talks to the queue, and proxies voice turns to
 * the AI service — which stays internal.
 */
import cors from '@fastify/cors';
import Fastify, { type FastifyError, type FastifyReply, type FastifyRequest } from 'fastify';
import { config } from './config.js';
import { registerAuth } from './auth/plugin.js';
import { ping, pool } from './lib/db.js';
import { redis, stats } from './lib/queue.js';
import { analyticsRoutes } from './routes/analytics.js';
import { authRoutes } from './routes/auth.js';
import { callRoutes } from './routes/calls.js';
import { jobRoutes } from './routes/jobs.js';
import { voiceRoutes } from './routes/voice.js';

const app = Fastify({
  logger: {
    level: config.logLevel,
    transport: config.env === 'local' ? { target: 'pino-pretty' } : undefined,
  },
  // Audio turns carry a recording; the default 1MB limit rejects them.
  bodyLimit: 25 * 1024 * 1024,
});

await app.register(cors, { origin: true, credentials: true });
await registerAuth(app);

/**
 * One error envelope, matching the AI and flight services:
 *   {"error": {code, message, retryable}}
 * A caller should never have to branch on which service produced a failure.
 */
app.setErrorHandler((error: FastifyError, request: FastifyRequest, reply: FastifyReply) => {
  const status = error.statusCode ?? 500;
  if (status >= 500) request.log.error({ err: error }, 'request failed');
  reply.code(status).send({
    error: {
      code: error.code ?? `HTTP_${status}`,
      message: status >= 500 ? 'Internal server error' : error.message,
      retryable: status >= 500 || status === 429,
    },
  });
});

app.setNotFoundHandler((request, reply) => {
  reply.code(404).send({
    error: {
      code: 'NOT_FOUND',
      message: `${request.method} ${request.url} is not a route`,
      retryable: false,
    },
  });
});

app.get('/health', async () => {
  let database = 'up';
  try {
    await ping();
  } catch (error: unknown) {
    database = `down: ${(error as Error).message}`;
  }

  let queue: unknown = 'up';
  try {
    queue = await stats();
  } catch (error: unknown) {
    queue = `down: ${(error as Error).message}`;
  }

  return {
    service: 'api',
    status: database === 'up' ? 'ok' : 'degraded',
    database,
    queue,
    ai_service: config.aiServiceUrl,
  };
});

await app.register(authRoutes);
await app.register(callRoutes);
await app.register(jobRoutes);
await app.register(analyticsRoutes);
await app.register(voiceRoutes);

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
