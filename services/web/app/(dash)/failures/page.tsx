'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, type FailureAnalytics } from '@/lib/api';
import { Ago, Empty, ErrorNote, Panel, Pill, Stat } from '@/lib/ui';

export default function FailuresPage() {
  const [data, setData] = useState<FailureAnalytics | null>(null);
  const [days, setDays] = useState(7);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.failureAnalytics(days));
      setError(null);
    } catch (caught) {
      setError((caught as Error).message);
    }
  }, [days]);

  useEffect(() => { void load(); }, [load]);

  if (error) return <ErrorNote message={error} />;
  if (!data) return <Empty>Loading…</Empty>;

  const worst = Math.max(...data.by_service.map((row) => row.occurrences), 1);
  const retryRate = data.retries.total_attempts
    ? data.retries.retries / data.retries.total_attempts : 0;

  return (
    <div className="space-y-5">
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Failures</h1>
        <select
          value={days} onChange={(e) => setDays(Number(e.target.value))}
          className="rounded-lg border border-[--color-line] bg-[--color-panel] px-2 py-1 text-[12px]"
        >
          {[1, 7, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Retry rate" value={`${Math.round(retryRate * 100)}%`}
          tone={retryRate > 0.3 ? 'text-[--color-warn]' : ''}
          hint={`${data.retries.retries} of ${data.retries.total_attempts} attempts`}
        />
        <Stat
          label="Avg backoff"
          value={data.retries.avg_backoff_ms ? `${(data.retries.avg_backoff_ms / 1000).toFixed(1)}s` : '—'}
          hint="before a retry"
        />
        <Stat label="Retrying now" value={data.open.retrying} tone="text-[--color-warn]" />
        <Stat
          label="Dead lettered" value={data.open.dead_letter}
          tone={data.open.dead_letter > 0 ? 'text-[--color-bad]' : ''}
          hint={data.open.dead_letter > 0 ? 'automation gave up' : undefined}
        />
      </div>

      <Panel title="Where failures come from">
        {data.by_service.length === 0 ? <Empty>No failures in this window.</Empty> : (
          <div className="space-y-2.5">
            {data.by_service.map((row) => (
              <div key={`${row.service}-${row.error_type}`} className="space-y-1">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-medium">{row.service}</span>
                  <span className="font-[family-name:--font-mono] text-[12px]">{row.error_type}</span>
                  <Pill value={row.error_class} />
                  <span className="tnum ml-auto">{row.occurrences}</span>
                  <span className="w-20 text-right"><Ago iso={row.last_seen} /></span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-[--color-ground]">
                  <div
                    className={`h-full rounded-full ${
                      row.error_class === 'PERMANENT' ? 'bg-[--color-bad]' : 'bg-[--color-warn]'
                    }`}
                    style={{ width: `${(row.occurrences / worst) * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
        <p className="mt-4 border-t border-[--color-line] pt-3 text-[11px] leading-relaxed text-[--color-muted]">
          <strong>Retryable</strong> failures were worth another attempt — a timeout, a 503,
          a reset connection. <strong>Permanent</strong> ones were not: the request was
          wrong and would stay wrong, so the system failed fast rather than spending four
          more attempts and a customer&apos;s patience on it.
        </p>
      </Panel>
    </div>
  );
}
