'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, type Call, type CallAnalytics, tokens } from '@/lib/api';
import { Ago, CallLink, Duration, Empty, ErrorNote, Panel, Pill, Stat } from '@/lib/ui';

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

  return (
    <div className="space-y-5">
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Calls</h1>
        <span className="tnum text-[--color-muted]">{total} total</span>
      </div>

      {error && <ErrorNote message={error} />}

      {analytics && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <Stat label="Total" value={analytics.totals.total} hint={`last ${analytics.window_days} days`} />
            <Stat label="Successful" value={analytics.totals.successful} tone="text-[--color-ok]" />
            <Stat label="Escalated" value={analytics.totals.escalated} tone="text-[--color-warn]" />
            <Stat label="Failed" value={analytics.totals.failed} tone="text-[--color-bad]" />
            <Stat
              label="Avg duration"
              value={analytics.totals.avg_duration_ms
                ? `${Math.round(analytics.totals.avg_duration_ms / 1000)}s` : '—'}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Voice latency by stage">
              {/* The LLM dominates by an order of magnitude. This panel exists so
                  that stays visible rather than being re-derived each time. */}
              <div className="space-y-2">
                {([
                  ['Speech to text', analytics.latency.avg_stt_ms],
                  ['LLM', analytics.latency.avg_llm_ms],
                  ['Text to speech', analytics.latency.avg_tts_ms],
                ] as const).map(([label, value]) => {
                  const max = Math.max(
                    analytics.latency.avg_stt_ms ?? 0,
                    analytics.latency.avg_llm_ms ?? 0,
                    analytics.latency.avg_tts_ms ?? 0, 1,
                  );
                  return (
                    <div key={label} className="flex items-center gap-3">
                      <span className="w-28 shrink-0 text-[--color-muted]">{label}</span>
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-[--color-ground]">
                        <div
                          className="h-full rounded-full bg-[--color-accent]"
                          style={{ width: `${((value ?? 0) / max) * 100}%` }}
                        />
                      </div>
                      <span className="tnum w-16 text-right">{value ? `${value}ms` : '—'}</span>
                    </div>
                  );
                })}
                <div className="pt-1 text-[11px] text-[--color-muted]">
                  p95 LLM {analytics.latency.p95_llm_ms ?? '—'}ms
                </div>
              </div>
            </Panel>

            <Panel title="Intent distribution">
              <div className="space-y-1.5">
                {analytics.by_intent.slice(0, 6).map((row) => (
                  <div key={row.intent} className="flex items-center justify-between">
                    <span className="font-[family-name:--font-mono] text-[12px]">{row.intent}</span>
                    <span className="tnum text-[--color-muted]">
                      {row.calls}
                      {row.escalated > 0 && (
                        <span className="text-[--color-warn]"> · {row.escalated} escalated</span>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            </Panel>
          </div>
        </>
      )}

      <Panel
        title="Recent calls"
        action={
          <select
            value={status} onChange={(e) => setStatus(e.target.value)}
            className="rounded-lg border border-[--color-line] bg-[--color-panel] px-2 py-1 text-[12px]"
          >
            <option value="">All statuses</option>
            {['COMPLETED', 'IN_PROGRESS', 'ESCALATED', 'FAILED', 'ABANDONED'].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        }
        className="overflow-hidden"
      >
        {loading ? <Empty>Loading…</Empty> : calls.length === 0 ? (
          <Empty>No calls match this filter.</Empty>
        ) : (
          <div className="-m-4 overflow-x-auto">
            <table className="w-full text-left">
              <thead className="border-b border-[--color-line] text-[11px] uppercase tracking-wide text-[--color-muted]">
                <tr>
                  {['Call', 'Customer', 'Intent', 'Status', 'Turns', 'Duration', 'Started'].map((h) => (
                    <th key={h} className="px-4 py-2 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {calls.map((call) => (
                  <tr key={call.id} className="border-b border-[--color-line] last:border-0 hover:bg-[--color-ground]">
                    <td className="px-4 py-2"><CallLink id={call.id} /></td>
                    <td className="px-4 py-2">{call.customer_name ?? <span className="text-[--color-muted]">unknown</span>}</td>
                    <td className="px-4 py-2 font-[family-name:--font-mono] text-[12px]">
                      {call.primary_intent ?? <span className="text-[--color-muted]">—</span>}
                    </td>
                    <td className="px-4 py-2"><Pill value={call.status} /></td>
                    <td className="tnum px-4 py-2">{call.turns}</td>
                    <td className="px-4 py-2"><Duration ms={call.duration_ms} /></td>
                    <td className="px-4 py-2"><Ago iso={call.started_at} /></td>
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
