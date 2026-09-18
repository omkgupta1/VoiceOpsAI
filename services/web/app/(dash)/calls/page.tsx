'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, type Call, type CallAnalytics, tokens } from '@/lib/api';
import { Ago, Bar, CallLink, Duration, Empty, ErrorNote, Panel, Pill, ROW, Skeleton, Stat, toneStyle, toneVar, type Tone } from '@/lib/ui';

/** Each intent keeps one hue everywhere it appears. */
const INTENT_TONE: Tone[] = ['accent', 'accent2', 'ok', 'warn', 'bad', 'muted'];

export default function CallsPage() {
  const [calls, setCalls] = useState<Call[]>([]);
  const [total, setTotal] = useState(0);
  const [analytics, setAnalytics] = useState<CallAnalytics | null>(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const canSeeAnalytics = tokens.getUser()?.permissions.includes('analytics:read') ?? false;

  const load = useCallback(async () => {
    try {
      const [list, stats] = await Promise.all([
        api.calls({ status, limit: 50 }),
        // An agent has calls:read but not analytics:read. Asking anyway would
        // produce a 403 in the console on every load for a whole role.
        canSeeAnalytics ? api.callAnalytics(30) : Promise.resolve(null),
      ]);
      setCalls(list.calls);
      setTotal(list.total);
      if (stats) setAnalytics(stats);
      setError(null);
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setLoading(false);
    }
  }, [status, canSeeAnalytics]);

  useEffect(() => { void load(); }, [load]);

  const intentMax = Math.max(...(analytics?.by_intent ?? []).map((row) => row.calls), 1);

  return (
    <div className="space-y-5">
      <div className="rise flex items-baseline justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight">
          <span className="grad-text">Calls</span>
        </h1>
        <span className="tnum text-[var(--color-muted)]">{total} total</span>
      </div>

      {error && <ErrorNote message={error} />}

      {analytics && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <Stat index={0} icon="◉" label="Total" value={analytics.totals.total} tone="accent"
              hint={`last ${analytics.window_days} days`} />
            <Stat index={1} icon="✓" label="Successful" value={analytics.totals.successful} tone="ok" />
            <Stat index={2} icon="↗" label="Escalated" value={analytics.totals.escalated} tone="warn" />
            <Stat index={3} icon="✕" label="Failed" value={analytics.totals.failed} tone="bad" />
            <Stat index={4} icon="◷" label="Avg duration"
              value={analytics.totals.avg_duration_ms
                ? `${Math.round(analytics.totals.avg_duration_ms / 1000)}s` : '—'} tone="accent2" />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel index={5} accent="accent" title="Voice latency by stage">
              {/* The LLM dominates by an order of magnitude. This panel exists so
                  that stays visible rather than being re-derived each time. */}
              {(() => {
                const stages = [
                  ['Speech to text', analytics.latency.avg_stt_ms, 'accent2'],
                  ['LLM', analytics.latency.avg_llm_ms, 'accent'],
                  ['Text to speech', analytics.latency.avg_tts_ms, 'ok'],
                ] as const;
                const max = Math.max(...stages.map(([, value]) => value ?? 0), 1);
                return (
                  <div className="space-y-2.5">
                    {stages.map(([label, value, tone]) => (
                      <Bar key={label} label={label} value={value ?? 0} max={max} tone={tone}
                        display={value ? `${value}ms` : '—'} />
                    ))}
                    <div className="mt-3 border-t border-[var(--color-line)] pt-2.5 text-[11px] text-[var(--color-muted)]">
                      p95 LLM <span className="tnum text-[var(--color-ink)]">
                        {analytics.latency.p95_llm_ms ?? '—'}ms
                      </span> · the model is the whole cost, so speed work belongs there
                    </div>
                  </div>
                );
              })()}
            </Panel>

            <Panel index={6} accent="accent2" title="Intent distribution">
              <div className="space-y-2">
                {analytics.by_intent.slice(0, 6).map((row, index) => {
                  const tone = INTENT_TONE[index % INTENT_TONE.length];
                  return (
                    <div key={row.intent} className="space-y-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-[family-name:var(--font-mono)] text-[12px]">{row.intent}</span>
                        <span className="tnum text-[var(--color-muted)]">
                          {row.calls}
                          {row.escalated > 0 && (
                            <span style={{ color: toneVar('warn') }}> · {row.escalated} escalated</span>
                          )}
                        </span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-raised)]">
                        <div
                          className="bar-fill h-full rounded-full"
                          style={{
                            width: `${(row.calls / intentMax) * 100}%`,
                            background: toneVar(tone),
                            boxShadow: `0 0 10px -2px ${toneVar(tone)}`,
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </Panel>
          </div>
        </>
      )}

      <Panel
        index={7}
        title="Recent calls"
        action={
          <select
            value={status} onChange={(e) => setStatus(e.target.value)}
            className="rounded-lg border border-[var(--color-line)] bg-[var(--color-raised)] px-2.5 py-1.5 text-[12px] outline-none transition focus:border-[var(--color-accent)]"
          >
            <option value="">All statuses</option>
            {['COMPLETED', 'IN_PROGRESS', 'ESCALATED', 'FAILED', 'ABANDONED'].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        }
      >
        {loading ? <Skeleton rows={5} /> : calls.length === 0 ? (
          <Empty>No calls match this filter.</Empty>
        ) : (
          <div className="-m-4 overflow-x-auto">
            <table className="w-full text-left">
              <thead className="border-b border-[var(--color-line)] text-[10px] uppercase tracking-[0.08em] text-[var(--color-muted)]">
                <tr>
                  {['Call', 'Customer', 'Intent', 'Status', 'Turns', 'Duration', 'Started'].map((h) => (
                    <th key={h} className="px-4 py-2.5 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {calls.map((call, index) => (
                  <tr
                    key={call.id}
                    className={`rise ${ROW}`}
                    // Only the first rows are staggered; past that the cascade
                    // outlasts anyone's patience for a 50-row table.
                    style={{ '--i': Math.min(index, 12) } as React.CSSProperties}
                  >
                    <td className="px-4 py-2.5"><CallLink id={call.id} /></td>
                    <td className="px-4 py-2.5">
                      {call.customer_name ?? <span className="text-[var(--color-muted)]">unknown</span>}
                    </td>
                    <td className="px-4 py-2.5">
                      {call.primary_intent ? (
                        <span
                          className="rounded-md border px-1.5 py-[1px] font-[family-name:var(--font-mono)] text-[11px]"
                          style={toneStyle('accent2', 0.08, 0.2)}
                        >
                          {call.primary_intent}
                        </span>
                      ) : <span className="text-[var(--color-muted)]">—</span>}
                    </td>
                    <td className="px-4 py-2.5"><Pill value={call.status} /></td>
                    <td className="tnum px-4 py-2.5">{call.turns}</td>
                    <td className="px-4 py-2.5"><Duration ms={call.duration_ms} /></td>
                    <td className="px-4 py-2.5"><Ago iso={call.started_at} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
