/**
 * The producer half of the queue protocol, in TypeScript.
 *
 * Deliberately a *subset*: enqueue, inspect, and requeue a dead letter. The hard
 * parts — atomic reservation, leases, the reaper, backoff — live in the Python
 * worker and are not reimplemented here. Two implementations of a distributed
 * algorithm is two chances to get it subtly different, and the differences would
 * only show up under the failures it exists to survive.
 *
 * The key layout is fixed by docs/queue-protocol.md, which is the contract these
 * two languages meet at.
 */
import { Redis } from 'ioredis';
import { randomUUID } from 'node:crypto';
import { config } from '../config.js';

export const PRIORITIES = ['HIGH', 'MEDIUM', 'LOW'] as const;
export type Priority = (typeof PRIORITIES)[number];

export interface JobInput {
  jobType: string;
  payload?: Record<string, unknown>;
  priority?: Priority;
  runAt?: number;
  maxAttempts?: number;
  idempotencyKey?: string;
  callId?: string | null;
  customerId?: string | null;
}

export interface Job {
  id: string;
  job_type: string;
  payload: Record<string, unknown>;
  priority: Priority;
  status: string;
  attempt: number;
  max_attempts: number;
  last_error: string | null;
  last_error_code: string | null;
  call_id: string | null;
  run_at: number;
  created_at: number;
}

const ns = config.queueNamespace;
const keys = {
  job: (id: string) => `${ns}:job:${id}`,
  ready: (priority: Priority) => `${ns}:ready:${priority}`,
  scheduled: `${ns}:scheduled`,
  processing: `${ns}:processing`,
  dlq: `${ns}:dlq`,
  idempotency: (key: string) => `${ns}:idem:${key}`,
  stat: (name: string) => `${ns}:stats:${name}`,
};

export const redis = new Redis(config.redisUrl, { maxRetriesPerRequest: 3 });

function decode(raw: Record<string, string>): Job {
  return {
    id: raw.id ?? '',
    job_type: raw.job_type ?? '',
    payload: JSON.parse(raw.payload || '{}'),
    priority: (raw.priority as Priority) ?? 'MEDIUM',
    status: raw.status ?? 'QUEUED',
    attempt: Number(raw.attempt ?? 0),
    max_attempts: Number(raw.max_attempts ?? 5),
    last_error: raw.last_error || null,
    last_error_code: raw.last_error_code || null,
    call_id: raw.call_id || null,
    run_at: Number(raw.run_at ?? 0),
    created_at: Number(raw.created_at ?? 0),
  };
}

export async function getJob(id: string): Promise<Job | null> {
  const raw = await redis.hgetall(keys.job(id));
  return Object.keys(raw).length > 0 ? decode(raw) : null;
}

export async function enqueue(input: JobInput): Promise<{ job: Job; created: boolean }> {
  const id = randomUUID();
  const now = Date.now() / 1000;
  const runAt = input.runAt ?? now;
  const priority = input.priority ?? 'MEDIUM';

  if (input.idempotencyKey) {
    // SET NX is the whole mechanism: whoever sets it first owns the job, so a
    // caller that retried after a timeout gets the original back rather than
    // cancelling the same booking twice.
    const claimed = await redis.set(keys.idempotency(input.idempotencyKey), id, 'EX', 86_400, 'NX');
    if (!claimed) {
      const existingId = await redis.get(keys.idempotency(input.idempotencyKey));
      const existing = existingId ? await getJob(existingId) : null;
      if (existing) return { job: existing, created: false };
    }
  }

  const due = runAt <= now;
  const fields: Record<string, string> = {
    id,
    job_type: input.jobType,
    payload: JSON.stringify(input.payload ?? {}),
    priority,
    status: due ? 'QUEUED' : 'SCHEDULED',
    run_at: String(runAt),
    attempt: '0',
    max_attempts: String(input.maxAttempts ?? 5),
    idempotency_key: input.idempotencyKey ?? '',
    call_id: input.callId ?? '',
    customer_id: input.customerId ?? '',
    last_error: '',
    last_error_code: '',
    created_at: String(now),
  };

  const pipeline = redis.pipeline();
  pipeline.hset(keys.job(id), fields);
  if (due) pipeline.rpush(keys.ready(priority), id);
  else pipeline.zadd(keys.scheduled, runAt, id);
  pipeline.incr(keys.stat('enqueued'));
  await pipeline.exec();

  return { job: decode(fields), created: true };
}

export async function stats() {
  const pipeline = redis.pipeline();
  for (const priority of PRIORITIES) pipeline.llen(keys.ready(priority));
  pipeline.zcard(keys.scheduled);
  pipeline.zcard(keys.processing);
  pipeline.llen(keys.dlq);
  const counterNames = ['enqueued', 'succeeded', 'failed', 'retried', 'dead_lettered', 'reaped'];
  for (const name of counterNames) pipeline.get(keys.stat(name));

  const results = (await pipeline.exec()) ?? [];
  const value = (index: number) => Number(results[index]?.[1] ?? 0);

  const ready = Object.fromEntries(PRIORITIES.map((p, i) => [p, value(i)])) as Record<
    Priority,
    number
  >;

  return {
    ready,
    ready_total: PRIORITIES.reduce((total, p) => total + ready[p], 0),
    scheduled: value(3),
    processing: value(4),
    dead_letter: value(5),
    counters: Object.fromEntries(counterNames.map((name, i) => [name, value(6 + i)])),
  };
}

export async function deadLetters(limit = 50): Promise<Job[]> {
  const ids = await redis.lrange(keys.dlq, 0, limit - 1);
  const jobs = await Promise.all(ids.map(getJob));
  return jobs.filter((job): job is Job => job !== null);
}

export async function requeueDeadLetter(id: string): Promise<Job | null> {
  const job = await getJob(id);
  if (!job) return null;

  const pipeline = redis.pipeline();
  pipeline.lrem(keys.dlq, 1, id);
  pipeline.hset(keys.job(id), {
    status: 'QUEUED',
    attempt: '0',
    run_at: String(Date.now() / 1000),
    last_error: '',
    last_error_code: '',
  });
  pipeline.rpush(keys.ready(job.priority), id);
  await pipeline.exec();

  return { ...job, status: 'QUEUED', attempt: 0 };
}
