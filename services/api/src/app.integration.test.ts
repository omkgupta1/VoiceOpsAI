/**
 * The API, driven through its real request lifecycle.
 *
 * `app.inject()` runs a request through auth, RBAC, routing and the error
 * handler in-process — no port, no server to race against. Testing an HTTP API
 * by starting a server and hoping to connect in time is the usual source of
 * flaky CI.
 *
 * Needs Postgres and Redis (seeded). CI provides both as service containers;
 * locally, `make up && make migrate && make seed`.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { buildApp } from './app.js';
import { pool } from './lib/db.js';
import { redis } from './lib/queue.js';

let app: FastifyInstance;

/** Signing in for real, rather than minting a token, tests the thing users do. */
async function tokenFor(email: string): Promise<string> {
  const response = await app.inject({
    method: 'POST',
    url: '/api/auth/login',
    payload: { email, password: 'voiceops123' },
  });
  expect(response.statusCode, `login failed for ${email}: ${response.body}`).toBe(200);
  return response.json().token;
}

beforeAll(async () => {
  app = await buildApp();
});

afterAll(async () => {
  await app.close();
  await pool.end();
  redis.disconnect();
});

describe('health', () => {
  it('reports both datastores', async () => {
    const body = (await app.inject({ method: 'GET', url: '/health' })).json();

    expect(body.status).toBe('ok');
    expect(body.database).toBe('up');
    expect(body.queue).toBeTypeOf('object');
  });
});

describe('authentication', () => {
  it('issues a token with the role and its permissions', async () => {
    const response = await app.inject({
      method: 'POST',
      url: '/api/auth/login',
      payload: { email: 'supervisor@voiceops.ai', password: 'voiceops123' },
    });

    expect(response.statusCode).toBe(200);
    const body = response.json();
    expect(body.token).toBeTruthy();
    expect(body.user.role).toBe('SUPERVISOR');
    expect(body.user.permissions).toContain('jobs:retry');
  });

  it('refuses a wrong password without saying which half was wrong', async () => {
    const response = await app.inject({
      method: 'POST',
      url: '/api/auth/login',
      payload: { email: 'supervisor@voiceops.ai', password: 'not-the-password' },
    });

    expect(response.statusCode).toBe(401);
    // Naming the wrong half turns a login form into an account enumerator.
    // The generic message ("email or password is incorrect") is what we want,
    // so the assertion has to name the leaks rather than match a substring of
    // the good message — which the first version of this test did, and failed.
    const message = response.json().error.message.toLowerCase();
    expect(message).not.toContain('no such user');
    expect(message).not.toContain('unknown email');
    expect(message).not.toContain('wrong password');
    expect(message).toContain('email or password');
  });

  it('refuses an unknown account with the same status as a wrong password', async () => {
    const response = await app.inject({
      method: 'POST',
      url: '/api/auth/login',
      payload: { email: 'nobody@voiceops.ai', password: 'voiceops123' },
    });

    expect(response.statusCode).toBe(401);
  });

  it('rejects a request with no token', async () => {
    expect((await app.inject({ method: 'GET', url: '/api/calls' })).statusCode).toBe(401);
  });

  it('rejects a forged token', async () => {
    const response = await app.inject({
      method: 'GET',
      url: '/api/calls',
      headers: { authorization: 'Bearer not.a.real.token' },
    });

    expect(response.statusCode).toBe(401);
  });
});

describe('RBAC is enforced at the HTTP layer, not just in the table', () => {
  it('lets a supervisor read the queue', async () => {
    const response = await app.inject({
      method: 'GET',
      url: '/api/jobs',
      headers: { authorization: `Bearer ${await tokenFor('supervisor@voiceops.ai')}` },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json().jobs).toBeInstanceOf(Array);
  });

  it('refuses an agent the queue', async () => {
    // The dashboard hides the tab; this asserts the API would refuse anyway.
    // A UI-only restriction is a suggestion.
    const response = await app.inject({
      method: 'GET',
      url: '/api/jobs',
      headers: { authorization: `Bearer ${await tokenFor('agent1@voiceops.ai')}` },
    });

    expect(response.statusCode).toBe(403);
  });

  it('refuses an agent analytics', async () => {
    const response = await app.inject({
      method: 'GET',
      url: '/api/analytics/calls',
      headers: { authorization: `Bearer ${await tokenFor('agent1@voiceops.ai')}` },
    });

    expect(response.statusCode).toBe(403);
  });

  it('lets an agent read calls', async () => {
    const response = await app.inject({
      method: 'GET',
      url: '/api/calls',
      headers: { authorization: `Bearer ${await tokenFor('agent1@voiceops.ai')}` },
    });

    expect(response.statusCode).toBe(200);
  });
});

describe('the error envelope', () => {
  it('is the same shape for a 404 as for anything else', async () => {
    const body = (await app.inject({ method: 'GET', url: '/nope' })).json();

    expect(body.error).toMatchObject({
      code: 'NOT_FOUND',
      retryable: false,
    });
    expect(body.error.message).toBeTypeOf('string');
  });

  it('marks a client error as not retryable', async () => {
    const response = await app.inject({
      method: 'GET',
      url: '/api/calls/not-a-uuid',
      headers: { authorization: `Bearer ${await tokenFor('supervisor@voiceops.ai')}` },
    });

    // Found by this test: the raw param went straight into `WHERE id = $1`
    // against a uuid column, so Postgres threw and it left as a 500 marked
    // RETRYABLE — a permanently malformed request that the retry engine would
    // then retry with backoff until the attempt cap ran out.
    expect(response.statusCode).toBe(400);
    expect(response.json().error.code).toBe('INVALID_IDENTIFIER');
    expect(response.json().error.retryable).toBe(false);
  });

  it('rejects a malformed job id the same way', async () => {
    const response = await app.inject({
      method: 'GET',
      url: '/api/jobs/not-a-uuid',
      headers: { authorization: `Bearer ${await tokenFor('supervisor@voiceops.ai')}` },
    });

    expect(response.statusCode).toBe(400);
    expect(response.json().error.retryable).toBe(false);
  });
});

describe('observability', () => {
  it('serves Prometheus metrics', async () => {
    const response = await app.inject({ method: 'GET', url: '/metrics' });

    expect(response.statusCode).toBe(200);
    expect(response.body).toContain('voiceops_api_requests_total');
  });

  it('does not fall over when there is no active span', async () => {
    // `inject` never opens a socket, so the HTTP instrumentation creates no
    // server span and there is no trace id to attach. That is a property of
    // in-process injection, not a bug — the header is asserted against a real
    // socket by `make test-trace`, which fails if it is missing.
    const response = await app.inject({ method: 'GET', url: '/health' });

    expect(response.statusCode).toBe(200);
    expect(response.headers['x-trace-id']).toBeUndefined();
  });
});
