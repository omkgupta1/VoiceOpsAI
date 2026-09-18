'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, type Attempt, type Job, type LiveJob, type QueueStats } from '@/lib/api';
import { Ago, CallLink, Duration, Empty, ErrorNote, Mono, Panel, Pill, ROW, Skeleton, Stat, toneStyle, toneVar } from '@/lib/ui';

export default function QueuePage() {
  const [live, setLive] = useState<QueueStats | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [dead, setDead] = useState<LiveJob[]>([]);
  const [selected, setSelected] = useState<{ job: Job; attempts: Attempt[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [beat, setBeat] = useState(false);

  const load = useCallback(async () => {
    try {
      const [list, dlq] = await Promise.all([api.jobs({ limit: '40' }), api.deadLetters()]);
      setLive(list.live);
      setJobs(list.jobs);
      setDead(dlq.jobs);
      setError(null);
      // A short blink on the poll indicator: proof the page is still talking to
      // the API, which matters when every number happens to be zero.
      setBeat(true);
      setTimeout(() => setBeat(false), 400);
    } catch (caught) {
      setError((caught as Error).message);
    }
  }, []);

  // The queue changes on its own — jobs promote, retry and dead-letter with
  // nobody touching the page. A static snapshot would be misleading here in a
  // way it is not on the calls list.
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [load]);

  async function requeue(id: string) {
    setBusy(id);
    try {
      await api.retryJob(id);
      await load();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function inspect(id: string) {
    try {
      setSelected(await api.job(id));
    } catch (caught) {
      setError((caught as Error).message);
    }
  }

  return (
    <div className="space-y-5">
      <div className="rise flex items-baseline justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight">
          <span className="grad-text">Queue</span>
        </h1>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-muted)]">
          <span
            className={`h-1.5 w-1.5 rounded-full transition-opacity duration-300 ${beat ? 'opacity-100' : 'opacity-30'}`}
            style={{ background: toneVar('ok'), boxShadow: `0 0 8px ${toneVar('ok')}` }}
          />
          live · every 3s
        </span>
      </div>

      {error && <ErrorNote message={error} />}

      {live && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Stat live index={0} icon="▲" label="High" value={live.ready.HIGH ?? 0} tone="bad" />
            <Stat live index={1} icon="■" label="Medium" value={live.ready.MEDIUM ?? 0} tone="warn" />
            <Stat live index={2} icon="▬" label="Low" value={live.ready.LOW ?? 0} tone="muted" />
            <Stat live index={3} icon="◷" label="Retry wait" value={live.scheduled} tone="warn" hint="backing off" />
            <Stat live index={4} icon="◍" label="In flight" value={live.processing} tone="accent2" />
            <Stat
              live index={5} icon="☠" label="Dead letter" value={live.dead_letter}
              tone={live.dead_letter > 0 ? 'bad' : 'muted'}
              hint={live.dead_letter > 0 ? 'needs a human' : 'none'}
            />
          </div>

          <Panel index={6} accent="accent" title="Lifetime counters">
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 lg:grid-cols-6">
              {Object.entries(live.counters).map(([name, value]) => (
                <div key={name} className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-[var(--color-muted)]">{name.replace(/_/g, ' ')}</span>
                  <span
                    className="tnum font-medium"
                    style={{
                      color: name === 'dead_lettered' && value > 0 ? toneVar('bad')
                        : name === 'succeeded' ? toneVar('ok')
                        : name === 'retried' ? toneVar('warn')
                        : undefined,
                    }}
                  >
                    {value}
                  </span>
                </div>
              ))}
            </div>
          </Panel>
        </>
      )}

      {dead.length > 0 && (
        <Panel index={7} accent="bad" title={`Dead letters (${dead.length})`}>
          <p className="mb-3 text-[var(--color-muted)]">
            Automation gave up on these. Requeueing resets the attempt count, so fix the
            cause first — otherwise they will simply exhaust again.
          </p>
          <ul className="space-y-2">
            {dead.map((job, index) => (
              <li
                key={job.id}
                className="rise flex flex-wrap items-center gap-2 rounded-xl border px-3 py-2 transition-colors"
                style={{ ...toneStyle('bad', 0.06, 0.28), color: 'var(--color-ink)', '--i': index } as React.CSSProperties}
              >
                <Mono onClick={() => void inspect(job.id)}>{job.id.slice(0, 8)}</Mono>
                <span className="font-[family-name:var(--font-mono)] text-[12px]">{job.job_type}</span>
                <Pill value={job.priority} />
                <span className="tnum text-[var(--color-muted)]">{job.attempt} attempts</span>
                <span className="text-[11px]" style={{ color: toneVar('bad') }}>{job.last_error_code}</span>
                <button
                  onClick={() => void requeue(job.id)} disabled={busy === job.id}
                  className="ml-auto rounded-lg border border-[var(--color-line)] px-3 py-1 transition hover:border-[var(--color-accent)] hover:bg-[var(--color-raised)] active:scale-[0.97] disabled:opacity-60"
                >
                  {busy === job.id ? 'Requeueing…' : '↻ Requeue'}
                </button>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {selected && (
        <Panel
          index={8}
          accent="warn"
          title={`Attempt history · ${selected.job.id.slice(0, 8)}`}
          action={
            <button
              onClick={() => setSelected(null)}
              className="rounded-lg px-2 py-1 text-[var(--color-muted)] transition hover:bg-[var(--color-raised)] hover:text-[var(--color-ink)]"
            >
              Close
            </button>
          }
        >
          {/* The backoff column is the point of this view: it shows the curve
              actually growing, rather than asking you to trust that it did. */}
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead className="border-b border-[var(--color-line)] text-[10px] uppercase tracking-[0.08em] text-[var(--color-muted)]">
                <tr>
                  {['#', 'Status', 'Backoff before', 'Took', 'Class', 'Error', 'Worker'].map((h) => (
                    <th key={h} className="py-2.5 pr-4 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {selected.attempts.map((attempt, index) => {
                  const worst = Math.max(...selected.attempts.map((a) => a.backoff_ms ?? 0), 1);
                  return (
                    <tr
                      key={attempt.attempt_number}
                      className={`rise ${ROW}`}
                      style={{ '--i': index } as React.CSSProperties}
                    >
                      <td className="tnum py-2.5 pr-4">{attempt.attempt_number}</td>
                      <td className="py-2.5 pr-4"><Pill value={attempt.status} /></td>
                      <td className="py-2.5 pr-4">
                        <div className="flex items-center gap-2">
                          {/* The bar is the argument. A column of numbers asks
                              you to compare them; a growing bar just shows it. */}
                          <span className="h-1.5 w-16 overflow-hidden rounded-full bg-[var(--color-raised)]">
                            <span
                              className="bar-fill block h-full rounded-full"
                              style={{
                                width: `${((attempt.backoff_ms ?? 0) / worst) * 100}%`,
                                background: toneVar('warn'),
                              }}
                            />
                          </span>
                          <Duration ms={attempt.backoff_ms} />
                        </div>
                      </td>
                      <td className="py-2.5 pr-4"><Duration ms={attempt.duration_ms} /></td>
                      <td className="py-2.5 pr-4"><Pill value={attempt.error_class} /></td>
                      <td className="py-2.5 pr-4 font-[family-name:var(--font-mono)] text-[11px]">
                        {attempt.error_type ?? '—'}
                      </td>
                      <td className="py-2.5 pr-4 font-[family-name:var(--font-mono)] text-[11px] text-[var(--color-muted)]">
                        {attempt.worker_id?.split('-').slice(-2).join('-') ?? '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <Panel index={9} title="Recent jobs">
        {!live ? <Skeleton rows={5} /> : jobs.length === 0 ? <Empty>No jobs yet.</Empty> : (
          <div className="-m-4 overflow-x-auto">
            <table className="w-full text-left">
              <thead className="border-b border-[var(--color-line)] text-[10px] uppercase tracking-[0.08em] text-[var(--color-muted)]">
                <tr>
                  {['Job', 'Type', 'Priority', 'Status', 'Attempts', 'Call', 'Created'].map((h) => (
                    <th key={h} className="px-4 py-2.5 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const exhausted = job.attempt_count >= job.max_attempts;
                  return (
                    <tr key={job.id} className={ROW}>
                      <td className="px-4 py-2.5">
                        <Mono onClick={() => void inspect(job.id)}>{job.id.slice(0, 8)}</Mono>
                      </td>
                      <td className="px-4 py-2.5 font-[family-name:var(--font-mono)] text-[12px]">{job.job_type}</td>
                      <td className="px-4 py-2.5"><Pill value={job.priority} /></td>
                      <td className="px-4 py-2.5"><Pill value={job.status} /></td>
                      <td className="tnum px-4 py-2.5" style={exhausted ? { color: toneVar('bad') } : undefined}>
                        {job.attempt_count}/{job.max_attempts}
                      </td>
                      <td className="px-4 py-2.5">
                        {job.call_id ? <CallLink id={job.call_id} /> : <span className="text-[var(--color-muted)]">—</span>}
                      </td>
                      <td className="px-4 py-2.5"><Ago iso={job.created_at} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
