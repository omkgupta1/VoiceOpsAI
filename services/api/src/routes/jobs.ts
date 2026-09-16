/**
 * Queue visibility and operator actions.
 */
import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { query, queryOne } from '../lib/db.js';
import { requires } from '../auth/plugin.js';
import { deadLetters, enqueue, getJob, requeueDeadLetter, stats, PRIORITIES } from '../lib/queue.js';

const ListQuery = z.object({
  status: z.string().optional(),
  priority: z.enum(PRIORITIES).optional(),
  limit: z.coerce.number().min(1).max(200).default(50),
});

const EnqueueBody = z.object({
  job_type: z.string().min(1),
  payload: z.record(z.unknown()).default({}),
  priority: z.enum(PRIORITIES).default('MEDIUM'),
  run_in_seconds: z.number().min(0).max(86_400).optional(),
  idempotency_key: z.string().optional(),
});

export async function jobRoutes(app: FastifyInstance): Promise<void> {
  app.get('/api/jobs', { preHandler: requires('jobs:read') }, async (request, reply) => {
    const parsed = ListQuery.safeParse(request.query);
    if (!parsed.success) {
      return reply.code(400).send({
        error: { code: 'INVALID_QUERY', message: parsed.error.message, retryable: false },
      });
    }
    const { status, priority, limit } = parsed.data;

    const where: string[] = [];
    const params: unknown[] = [];
    if (status) { params.push(status); where.push(`status = $${params.length}::job_status`); }
    if (priority) { params.push(priority); where.push(`priority = $${params.length}::job_priority`); }
    params.push(limit);

    // Read from Postgres, not Redis: Redis holds only live work, and a job that
    // succeeded an hour ago is exactly what someone asking this question wants.
    const jobs = await query(
      `SELECT id, call_id, job_type, priority, status, attempt_count, max_attempts,
              last_error, scheduled_at, created_at, completed_at
         FROM jobs ${where.length ? `WHERE ${where.join(' AND ')}` : ''}
        ORDER BY created_at DESC LIMIT $${params.length}`,
      params,
    );
    return { jobs, live: await stats() };
  });

  app.get('/api/jobs/dead-letters', { preHandler: requires('jobs:read') }, async () => {
    return { jobs: await deadLetters(50) };
  });

  app.get('/api/jobs/:id', { preHandler: requires('jobs:read') }, async (request, reply) => {
    const { id } = request.params as { id: string };

    const job = await queryOne(`SELECT * FROM jobs WHERE id = $1`, [id]);
    if (!job) {
      return reply.code(404).send({
        error: { code: 'JOB_NOT_FOUND', message: `No job ${id}`, retryable: false },
      });
    }

    // The attempt history is the interesting part: which attempt, after how much
    // backoff, failing how. `jobs` alone only shows where it ended up.
    const attempts = await query(
      `SELECT attempt_number, status, worker_id, started_at, finished_at,
              duration_ms, error_class, error_type, error_message, backoff_ms
         FROM job_attempts WHERE job_id = $1 ORDER BY attempt_number`,
      [id],
    );

    return { job, attempts, live: await getJob(id) };
  });

  app.post('/api/jobs/:id/retry', { preHandler: requires('jobs:retry') }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const requeued = await requeueDeadLetter(id);
    if (!requeued) {
      return reply.code(404).send({
        error: {
          code: 'JOB_NOT_FOUND',
          message: `No job ${id} in the dead-letter queue`,
          retryable: false,
        },
      });
    }
    return { job: requeued, message: 'Requeued with attempts reset' };
  });

  app.post('/api/jobs', { preHandler: requires('jobs:retry') }, async (request, reply) => {
    const parsed = EnqueueBody.safeParse(request.body);
    if (!parsed.success) {
      return reply.code(400).send({
        error: { code: 'INVALID_BODY', message: parsed.error.message, retryable: false },
      });
    }
    const body = parsed.data;
    const { job, created } = await enqueue({
      jobType: body.job_type,
      payload: body.payload,
      priority: body.priority,
      runAt: body.run_in_seconds ? Date.now() / 1000 + body.run_in_seconds : undefined,
      idempotencyKey: body.idempotency_key,
    });
    return reply.code(created ? 201 : 200).send({ job, created });
  });
}
