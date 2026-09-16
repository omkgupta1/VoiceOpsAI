'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, type Attempt, type Job, type LiveJob, type QueueStats } from '@/lib/api';
import { Ago, CallLink, Duration, Empty, ErrorNote, Panel, Pill, Stat } from '@/lib/ui';

export default function QueuePage() {
  const [live, setLive] = useState<QueueStats | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [dead, setDead] = useState<LiveJob[]>([]);
  const [selected, setSelected] = useState<{ job: Job; attempts: Attempt[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, dlq] = await Promise.all([api.jobs({ limit: '40' }), api.deadLetters()]);
      setLive(list.live);
      setJobs(list.jobs);
      setDead(dlq.jobs);
      setError(null);
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
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Queue</h1>
        <span className="text-[11px] text-[--color-muted]">refreshing every 3s</span>
      </div>

      {error && <ErrorNote message={error} />}

      {live && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="High" value={live.ready.HIGH ?? 0} tone="text-[--color-bad]" />
            <Stat label="Medium" value={live.ready.MEDIUM ?? 0} tone="text-[--color-warn]" />
            <Stat label="Low" value={live.ready.LOW ?? 0} />
            <Stat label="Retry wait" value={live.scheduled} hint="backing off" />
            <Stat label="In flight" value={live.processing} tone="text-[--color-accent]" />
            <Stat
              label="Dead letter" value={live.dead_letter}
              tone={live.dead_letter > 0 ? 'text-[--color-bad]' : ''}
              hint={live.dead_letter > 0 ? 'needs a human' : undefined}
            />
          </div>

          <Panel title="Lifetime counters">
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-3 lg:grid-cols-6">
              {Object.entries(live.counters).map(([name, value]) => (
                <div key={name} className="flex justify-between gap-2">
                  <span className="text-[--color-muted]">{name.replace(/_/g, ' ')}</span>
                  <span className="tnum">{value}</span>
                </div>
              ))}
            </div>
          </Panel>
        </>
      )}

      {dead.length > 0 && (
        <Panel title={`Dead letters (${dead.length})`}>
          <p className="mb-3 text-[--color-muted]">
            Automation gave up on these. Requeueing resets the attempt count, so fix the
            cause first — otherwise they will simply exhaust again.
          </p>
          <ul className="space-y-2">
            {dead.map((job) => (
              <li key={job.id} className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => void inspect(job.id)}
                  className="font-[family-name:--font-mono] text-[--color-accent] hover:underline"
                >
                  {job.id.slice(0, 8)}
                </button>
                <span className="font-[family-name:--font-mono] text-[12px]">{job.job_type}</span>
                <Pill value={job.priority} />
                <span className="tnum text-[--color-muted]">{job.attempt} attempts</span>
                <span className="text-[11px] text-[--color-bad]">{job.last_error_code}</span>
                <button
                  onClick={() => void requeue(job.id)} disabled={busy === job.id}
                  className="ml-auto rounded-lg border border-[--color-line] px-2.5 py-1 hover:border-[--color-accent] disabled:opacity-60"
                >
                  {busy === job.id ? 'Requeueing…' : 'Requeue'}
                </button>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {selected && (
        <Panel
          title={`Attempt history · ${selected.job.id.slice(0, 8)}`}
          action={
            <button onClick={() => setSelected(null)} className="text-[--color-muted] hover:text-[--color-ink]">
              Close
            </button>
          }
        >
          {/* The backoff column is the point of this view: it shows the curve
              actually growing, rather than asking you to trust that it did. */}
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead className="border-b border-[--color-line] text-[11px] uppercase tracking-wide text-[--color-muted]">
                <tr>
                  {['#', 'Status', 'Backoff before', 'Took', 'Class', 'Error', 'Worker'].map((h) => (
                    <th key={h} className="py-2 pr-4 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {selected.attempts.map((attempt) => (
                  <tr key={attempt.attempt_number} className="border-b border-[--color-line] last:border-0">
                    <td className="tnum py-2 pr-4">{attempt.attempt_number}</td>
                    <td className="py-2 pr-4"><Pill value={attempt.status} /></td>
                    <td className="py-2 pr-4"><Duration ms={attempt.backoff_ms} /></td>
                    <td className="py-2 pr-4"><Duration ms={attempt.duration_ms} /></td>
                    <td className="py-2 pr-4"><Pill value={attempt.error_class} /></td>
                    <td className="py-2 pr-4 font-[family-name:--font-mono] text-[11px]">
                      {attempt.error_type ?? '—'}
                    </td>
                    <td className="py-2 pr-4 font-[family-name:--font-mono] text-[11px] text-[--color-muted]">
                      {attempt.worker_id?.split('-').slice(-2).join('-') ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <Panel title="Recent jobs" className="overflow-hidden">
        {jobs.length === 0 ? <Empty>No jobs yet.</Empty> : (
          <div className="-m-4 overflow-x-auto">
            <table className="w-full text-left">
              <thead className="border-b border-[--color-line] text-[11px] uppercase tracking-wide text-[--color-muted]">
                <tr>
                  {['Job', 'Type', 'Priority', 'Status', 'Attempts', 'Call', 'Created'].map((h) => (
                    <th key={h} className="px-4 py-2 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id} className="border-b border-[--color-line] last:border-0 hover:bg-[--color-ground]">
                    <td className="px-4 py-2">
                      <button
                        onClick={() => void inspect(job.id)}
                        className="font-[family-name:--font-mono] text-[--color-accent] hover:underline"
                      >
                        {job.id.slice(0, 8)}
                      </button>
                    </td>
                    <td className="px-4 py-2 font-[family-name:--font-mono] text-[12px]">{job.job_type}</td>
                    <td className="px-4 py-2"><Pill value={job.priority} /></td>
                    <td className="px-4 py-2"><Pill value={job.status} /></td>
                    <td className="tnum px-4 py-2">{job.attempt_count}/{job.max_attempts}</td>
                    <td className="px-4 py-2">
                      {job.call_id ? <CallLink id={job.call_id} /> : <span className="text-[--color-muted]">—</span>}
                    </td>
                    <td className="px-4 py-2"><Ago iso={job.created_at} /></td>
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
