/**
 * Call listing, detail and escalation.
 */
import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { query, queryOne } from '../lib/db.js';
import { requires, type TokenPayload } from '../auth/plugin.js';

const ListQuery = z.object({
  status: z.string().optional(),
  intent: z.string().optional(),
  escalated: z.coerce.boolean().optional(),
  limit: z.coerce.number().min(1).max(200).default(50),
  offset: z.coerce.number().min(0).default(0),
});

export async function callRoutes(app: FastifyInstance): Promise<void> {
  app.get('/api/calls', { preHandler: requires('calls:read') }, async (request, reply) => {
    const parsed = ListQuery.safeParse(request.query);
    if (!parsed.success) {
      return reply.code(400).send({
        error: { code: 'INVALID_QUERY', message: parsed.error.message, retryable: false },
      });
    }
    const { status, intent, escalated, limit, offset } = parsed.data;

    // Filters are built as numbered placeholders rather than interpolated, so a
    // status of "'; DROP TABLE calls; --" is simply a status that matches nothing.
    const where: string[] = [];
    const params: unknown[] = [];
    if (status) { params.push(status); where.push(`c.status = $${params.length}::call_status`); }
    if (intent) { params.push(intent); where.push(`c.primary_intent = $${params.length}`); }
    if (escalated !== undefined) {
      where.push(escalated ? `c.escalation_status <> 'NONE'` : `c.escalation_status = 'NONE'`);
    }
    const clause = where.length ? `WHERE ${where.join(' AND ')}` : '';

    params.push(limit, offset);
    const rows = await query(
      `SELECT c.id, c.status, c.primary_intent, c.flow_id, c.escalation_status,
              c.started_at, c.ended_at, c.duration_ms, c.channel,
              cu.full_name AS customer_name, cu.phone AS customer_phone,
              (SELECT count(*) FROM conversations v WHERE v.call_id = c.id) AS turns
         FROM calls c
         LEFT JOIN customers cu ON cu.id = c.customer_id
         ${clause}
        ORDER BY c.started_at DESC
        LIMIT $${params.length - 1} OFFSET $${params.length}`,
      params,
    );

    const total = await queryOne<{ count: number }>(
      `SELECT count(*) AS count FROM calls c ${clause}`,
      params.slice(0, params.length - 2),
    );

    return { calls: rows, total: total?.count ?? 0, limit, offset };
  });

  app.get('/api/calls/:id', { preHandler: requires('calls:read') }, async (request, reply) => {
    const { id } = request.params as { id: string };

    const call = await queryOne(
      `SELECT c.*, cu.full_name AS customer_name, cu.phone AS customer_phone, cu.email AS customer_email
         FROM calls c LEFT JOIN customers cu ON cu.id = c.customer_id
        WHERE c.id = $1`,
      [id],
    );
    if (!call) {
      return reply.code(404).send({
        error: { code: 'CALL_NOT_FOUND', message: `No call ${id}`, retryable: false },
      });
    }

    // Transcripts are a separate permission from call metadata: a supervisor
    // reviewing queue health has no need to read what customers said.
    const user = request.user as TokenPayload;
    const conversation = (await import('../auth/rbac.js')).can(user.role, 'conversations:read')
      ? await query(
          `SELECT turn_index, speaker, message, intent, confidence, tool_calls,
                  stt_ms, llm_ms, tts_ms, providers, created_at
             FROM conversations WHERE call_id = $1 ORDER BY turn_index`,
          [id],
        )
      : null;

    const jobs = await query(
      `SELECT j.id, j.job_type, j.priority, j.status, j.attempt_count, j.max_attempts,
              j.last_error, j.created_at
         FROM jobs j WHERE j.call_id = $1 ORDER BY j.created_at DESC`,
      [id],
    );

    return { call, conversation, jobs };
  });

  app.post('/api/calls/:id/escalate', { preHandler: requires('calls:escalate') }, async (request, reply) => {
    const { id } = request.params as { id: string };
    const { reason } = (request.body ?? {}) as { reason?: string };
    const user = request.user as TokenPayload;

    const updated = await queryOne(
      `UPDATE calls
          SET status = 'ESCALATED', escalation_status = 'ESCALATED',
              escalation_reason = coalesce(escalation_reason, $2)
        WHERE id = $1
        RETURNING id, status, escalation_status, escalation_reason`,
      [id, reason ?? `Escalated by ${user.name}`],
    );
    if (!updated) {
      return reply.code(404).send({
        error: { code: 'CALL_NOT_FOUND', message: `No call ${id}`, retryable: false },
      });
    }
    return updated;
  });
}
