/**
 * Identifier validation at the edge.
 *
 * Every `:id` in this API addresses a `uuid` column. Passing an arbitrary string
 * to Postgres for one of those raises `invalid input syntax for type uuid`,
 * which arrives here as an unhandled exception and leaves as a **500 marked
 * retryable** — so a permanently malformed request gets retried, with backoff,
 * for as long as the attempt cap allows.
 *
 * That is the exact failure this project's error taxonomy exists to prevent, and
 * it is the same bug that was fixed in the flight service in Phase 6: a bad
 * identifier is a permanent client error, and the service must say so itself
 * rather than letting the database say it badly.
 */
import type { FastifyReply } from 'fastify';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: string): boolean {
  return UUID.test(value);
}

/**
 * Returns true when the reply has been sent, so callers can `if (...) return;`.
 */
export function rejectIfNotUuid(value: string, field: string, reply: FastifyReply): boolean {
  if (isUuid(value)) return false;

  reply.code(400).send({
    error: {
      code: 'INVALID_IDENTIFIER',
      message: `'${field}' must be a UUID`,
      // The whole point: asking again with the same value cannot succeed.
      retryable: false,
    },
  });
  return true;
}
