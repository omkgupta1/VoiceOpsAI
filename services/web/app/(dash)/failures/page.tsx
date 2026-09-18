'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, type FailureAnalytics } from '@/lib/api';
import { Ago, Empty, ErrorNote, Panel, Pill, Skeleton, Stat, toneStyle, toneVar } from '@/lib/ui';

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
  if (!data) return <Panel title="Failures"><Skeleton rows={6} /></Panel>;

  const worst = Math.max(...data.by_service.map((row) => row.occurrences), 1);
  const retryRate = data.retries.total_attempts
    ? data.retries.retries / data.retries.total_attempts : 0;

  return (
    <div className="space-y-5">
      <div className="rise flex items-baseline justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight">
          <span className="grad-text">Failures</span>
        </h1>
        <select
          value={days} onChange={(e) => setDays(Number(e.target.value))}
          className="rounded-lg border border-[var(--color-line)] bg-[var(--color-raised)] px-2.5 py-1.5 text-[12px] outline-none transition focus:border-[var(--color-accent)]"
        >
          {[1, 7, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          index={0} icon="↻" label="Retry rate" value={`${Math.round(retryRate * 100)}%`}
          tone={retryRate > 0.3 ? 'warn' : 'accent'}
          hint={`${data.retries.retries} of ${data.retries.total_attempts} attempts`}
        />
        <Stat
          index={1} icon="◷" label="Avg backoff"
          value={data.retries.avg_backoff_ms ? `${(data.retries.avg_backoff_ms / 1000).toFixed(1)}s` : '—'}
          tone="accent2" hint="before a retry"
        />
        <Stat index={2} icon="◍" label="Retrying now" value={data.open.retrying} tone="warn" />
        <Stat
          index={3} icon="☠" label="Dead lettered" value={data.open.dead_letter}
          tone={data.open.dead_letter > 0 ? 'bad' : 'muted'}
          hint={data.open.dead_letter > 0 ? 'automation gave up' : 'none'}
        />
      </div>

      <Panel index={4} accent="warn" title="Where failures come from">
        {data.by_service.length === 0 ? <Empty>No failures in this window.</Empty> : (
          <div className="space-y-3">
            {data.by_service.map((row, index) => {
              const tone = row.error_class === 'PERMANENT' ? 'bad' : 'warn';
              return (
                <div
                  key={`${row.service}-${row.error_type}`}
                  className="rise space-y-1.5"
                  style={{ '--i': index } as React.CSSProperties}
                >
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span
                      className="rounded-md border px-1.5 py-[1px] text-[11px] font-medium"
                      style={toneStyle('accent2', 0.08, 0.22)}
                    >
                      {row.service}
                    </span>
                    <span className="font-[family-name:var(--font-mono)] text-[12px]">{row.error_type}</span>
                    <Pill value={row.error_class} />
                    <span className="tnum ml-auto font-medium" style={{ color: toneVar(tone) }}>
                      {row.occurrences}
                    </span>
                    <span className="w-20 text-right"><Ago iso={row.last_seen} /></span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-[var(--color-raised)]">
                    <div
                      className="bar-fill h-full rounded-full"
                      style={{
                        width: `${(row.occurrences / worst) * 100}%`,
                        background: `linear-gradient(90deg, rgb(var(--${tone}-rgb) / 0.5), ${toneVar(tone)})`,
                        boxShadow: `0 0 12px -2px ${toneVar(tone)}`,
                      }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <div className="mt-5 grid gap-3 border-t border-[var(--color-line)] pt-4 sm:grid-cols-2">
          <div className="rounded-xl border px-3 py-2.5" style={toneStyle('warn', 0.07, 0.25)}>
            <div className="font-medium">Retryable</div>
            <p className="mt-0.5 text-[11px] leading-relaxed" style={{ color: 'var(--color-muted)' }}>
              Worth another attempt — a timeout, a 503, a reset connection. The backoff
              curve gives the other side room to recover.
            </p>
          </div>
          <div className="rounded-xl border px-3 py-2.5" style={toneStyle('bad', 0.07, 0.25)}>
            <div className="font-medium">Permanent</div>
            <p className="mt-0.5 text-[11px] leading-relaxed" style={{ color: 'var(--color-muted)' }}>
              Not worth another attempt: the request was wrong and would stay wrong, so the
              system fails fast rather than spending four more tries and a customer&apos;s
              patience on it.
            </p>
          </div>
        </div>
      </Panel>
    </div>
  );
}
