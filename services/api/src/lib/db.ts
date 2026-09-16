/**
 * PostgreSQL access.
 *
 * Raw SQL over `pg`, not an ORM: the schema is owned by db/migrations and neither
 * language's ORM gets to generate it (ADR 0002). Parameterised queries throughout —
 * every value reaches Postgres as a bound parameter, never as string concatenation.
 */
import pg from 'pg';
import { config } from '../config.js';

// Postgres returns NUMERIC as a string to preserve precision. Fares and refund
// amounts are money, and JSON.stringify of a string is not what a UI expects,
// so they are parsed here rather than in every route.
pg.types.setTypeParser(1700, (value) => Number.parseFloat(value));
// BIGINT (count(*) results) likewise arrives as a string.
pg.types.setTypeParser(20, (value) => Number.parseInt(value, 10));

export const pool = new pg.Pool({
  connectionString: config.databaseUrl,
  max: 10,
  idleTimeoutMillis: 30_000,
});

export async function query<T extends pg.QueryResultRow = pg.QueryResultRow>(
  text: string,
  params: unknown[] = [],
): Promise<T[]> {
  const result = await pool.query<T>(text, params);
  return result.rows;
}

export async function queryOne<T extends pg.QueryResultRow = pg.QueryResultRow>(
  text: string,
  params: unknown[] = [],
): Promise<T | null> {
  const rows = await query<T>(text, params);
  return rows[0] ?? null;
}

export async function ping(): Promise<boolean> {
  await query('SELECT 1');
  return true;
}
