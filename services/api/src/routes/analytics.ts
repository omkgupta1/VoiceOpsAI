/**
 * Aggregate reporting — the metrics in overview.md §13.
 *
 * Every figure is computed in SQL rather than by pulling rows into Node and
 * counting them. Postgres has the indexes; shipping ten thousand calls over the
 * wire to produce five numbers would be slower and would stop working at exactly
 * the volume the dashboard is for.
 */
import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { query, queryOne } from '../lib/db.js';
import { requires } from '../auth/plugin.js';
import { stats } from '../lib/queue.js';

const Window = z.object({ days: z.coerce.number().min(1).max(90).default(7) });

export async function analyticsRoutes(app: FastifyInstance): Promise<void> {
  app.get('/api/analytics/calls', { preHandler: requires('analytics:read') }, async (request) => {
    const { days } = Window.parse(request.query);

    const totals = await queryOne(
      `SELECT count(*)                                            AS total,
              count(*) FILTER (WHERE status = 'COMPLETED')        AS successful,
              count(*) FILTER (WHERE status = 'ESCALATED')        AS escalated,
              count(*) FILTER (WHERE status = 'FAILED')           AS failed,
              count(*) FILTER (WHERE status = 'ABANDONED')        AS abandoned,
              count(*) FILTER (WHERE status = 'IN_PROGRESS')      AS in_progress,
              round(avg(duration_ms) FILTER (WHERE duration_ms IS NOT NULL)) AS avg_duration_ms
         FROM calls WHERE started_at > now() - ($1 || ' days')::interval`,
      [days],
    );

    const byIntent = await query(
      `SELECT coalesce(primary_intent, 'UNKNOWN') AS intent, count(*) AS calls,
              count(*) FILTER (WHERE status = 'ESCALATED') AS escalated
         FROM calls WHERE started_at > now() - ($1 || ' days')::interval
        GROUP BY 1 ORDER BY calls DESC`,
      [days],
    );

    // Voice latency by stage. This is the number that decides where optimisation
    // effort goes, and it is consistently the LLM.
    const latency = await queryOne(
      `SELECT round(avg(stt_ms)) AS avg_stt_ms,
              round(avg(llm_ms)) AS avg_llm_ms,
              round(avg(tts_ms)) AS avg_tts_ms,
              round(percentile_cont(0.95) WITHIN GROUP (ORDER BY llm_ms)) AS p95_llm_ms
         FROM conversations WHERE created_at > now() - ($1 || ' days')::interval`,
      [days],
    );

    return { window_days: days, totals, by_intent: byIntent, latency };
  });

  app.get('/api/analytics/failures', { preHandler: requires('failures:read') }, async (request) => {
    const { days } = Window.parse(request.query);

    const byService = await query(
      `SELECT service, error_class, error_type, count(*) AS occurrences,
              max(occurred_at) AS last_seen
         FROM failures WHERE occurred_at > now() - ($1 || ' days')::interval
        GROUP BY 1,2,3 ORDER BY occurrences DESC LIMIT 25`,
      [days],
    );

    const retries = await queryOne(
      `SELECT count(*)                                     AS total_attempts,
              count(*) FILTER (WHERE attempt_number > 1)   AS retries,
              round(avg(backoff_ms) FILTER (WHERE backoff_ms > 0)) AS avg_backoff_ms,
              max(attempt_number)                          AS worst_attempt_count
         FROM job_attempts WHERE started_at > now() - ($1 || ' days')::interval`,
      [days],
    );

    const open = await queryOne(
      `SELECT count(*) FILTER (WHERE resolution_status = 'OPEN')        AS open,
              count(*) FILTER (WHERE resolution_status = 'RETRYING')    AS retrying,
              count(*) FILTER (WHERE resolution_status = 'DEAD_LETTER') AS dead_letter
         FROM failures WHERE occurred_at > now() - ($1 || ' days')::interval`,
      [days],
    );

    return { window_days: days, by_service: byService, retries, open };
  });

  app.get('/api/analytics/queue', { preHandler: requires('jobs:read') }, async () => {
    // Redis for what is happening now, Postgres for what has happened. Neither
    // answers both questions.
    const history = await query(
      `SELECT status, priority, count(*) AS jobs
         FROM jobs WHERE created_at > now() - interval '7 days'
        GROUP BY 1,2 ORDER BY 2,1`,
    );
    return { live: await stats(), history };
  });
}
