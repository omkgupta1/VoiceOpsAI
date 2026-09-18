/**
 * VoiceOps platform API.
 *
 * The only public entry point. Owns authentication and RBAC, reads calls, jobs
 * and analytics from Postgres, talks to the queue, and proxies voice turns to
 * the AI service — which stays internal.
 */
// First import in the process, before anything binds a library handle that
// OpenTelemetry is about to replace. See the note at the top of telemetry.ts.
import { httpDuration, httpRequests, registry } from './telemetry.js';

import cors from '@fastify/cors';
import Fastify, { type FastifyError, type FastifyReply, type FastifyRequest } from 'fastify';
import { trace } from '@opentelemetry/api';
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
    // Stamped on every line, so a log here and a span in Jaeger can be joined
    // by one id. Without it, "the API logged an error at 16:04" and "this call
    // was slow" stay two separate investigations of the same incident.
    mixin() {
      const context = trace.getActiveSpan()?.spanContext();
      return context ? { trace_id: context.traceId, span_id: context.spanId } : {};
    },
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

/**
 * Count and time every request, labelled by route template rather than by URL.
 *
 * `/api/calls/:id` as a label keeps the metric to one series; the raw URL would
 * mint a new time series per call id and eventually take Prometheus down. That
 * failure mode has a name — cardinality explosion — and it is the usual way a
 * first metrics rollout goes wrong.
 */
app.addHook('onResponse', async (request, reply) => {
  const route = request.routeOptions?.url ?? 'unmatched';
  const labels = {
    method: request.method,
    route,
    status: String(reply.statusCode),
  };
  httpRequests.inc(labels);
  httpDuration.observe(labels, reply.elapsedTime / 1000);
});

/**
 * Hand the trace id back to the caller.
 *
 * The dashboard can then show "this request was slow — here is its trace", and
 * a bug report can carry one id that finds the exact request across all five
 * services.
 */
app.addHook('onSend', async (request, reply, payload) => {
  const context = trace.getActiveSpan()?.spanContext();
  if (context) reply.header('x-trace-id', context.traceId);
  return payload;
});

app.get('/metrics', async (_request, reply) => {
  reply.header('content-type', registry.contentType);
  return registry.metrics();
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
