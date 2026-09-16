'use client';

import { use, useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api, type CallDetail, type Job, type Turn, tokens } from '@/lib/api';
import { Ago, Duration, Empty, ErrorNote, Panel, Pill } from '@/lib/ui';

export default function CallPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [call, setCall] = useState<CallDetail | null>(null);
  const [conversation, setConversation] = useState<Turn[] | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [escalating, setEscalating] = useState(false);

  const canEscalate = tokens.getUser()?.permissions.includes('calls:escalate') ?? false;

  const load = useCallback(async () => {
    try {
      const data = await api.call(id);
      setCall(data.call);
      setConversation(data.conversation);
      setJobs(data.jobs);
      setError(null);
    } catch (caught) {
      setError((caught as Error).message);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  async function escalate() {
    setEscalating(true);
    try {
      await api.escalate(id, 'Escalated from the dashboard');
      await load();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setEscalating(false);
    }
  }

  if (error) return <ErrorNote message={error} />;
  if (!call) return <Empty>Loading…</Empty>;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/calls" className="text-[--color-muted] hover:text-[--color-ink]">← Calls</Link>
          <h1 className="mt-1 font-[family-name:--font-mono] text-lg font-semibold">{call.id.slice(0, 8)}</h1>
        </div>
        <div className="flex items-center gap-2">
          <Pill value={call.status} />
          {call.escalation_status !== 'NONE' && <Pill value={call.escalation_status} />}
          {canEscalate && call.escalation_status === 'NONE' && (
            <button
              onClick={escalate} disabled={escalating}
              className="rounded-lg border border-[--color-warn] px-3 py-1 text-[--color-warn] disabled:opacity-60"
            >
              {escalating ? 'Escalating…' : 'Escalate to human'}
            </button>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Call" className="lg:col-span-1">
          <dl className="space-y-2">
            {([
              ['Customer', call.customer_name ?? 'unknown'],
              ['Phone', call.customer_phone ?? '—'],
              ['Intent', call.primary_intent ?? '—'],
              ['Flow', call.flow_id ?? '—'],
              ['Channel', call.channel],
              ['Turns', String(conversation?.length ?? '—')],
            ] as const).map(([label, value]) => (
              <div key={label} className="flex justify-between gap-4">
                <dt className="text-[--color-muted]">{label}</dt>
                <dd className="truncate text-right">{value}</dd>
              </div>
            ))}
            <div className="flex justify-between gap-4">
              <dt className="text-[--color-muted]">Duration</dt>
              <dd><Duration ms={call.duration_ms} /></dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-[--color-muted]">Started</dt>
              <dd><Ago iso={call.started_at} /></dd>
            </div>
          </dl>

          {call.escalation_reason && (
            <div className="mt-3 rounded-lg border border-[--color-warn] px-3 py-2 text-[12px] text-[--color-warn]">
              {call.escalation_reason}
            </div>
          )}
        </Panel>

        <Panel title="Conversation" className="lg:col-span-2">
          {conversation === null ? (
            // Transcripts are a separate permission from call metadata.
            <Empty>Your role cannot read transcripts.</Empty>
          ) : conversation.length === 0 ? (
            <Empty>No turns recorded.</Empty>
          ) : (
            <ol className="space-y-3">
              {conversation.map((turn) => (
                <li key={turn.turn_index}>
                  <div className="flex items-baseline gap-2">
                    <span className="text-[11px] uppercase tracking-wide text-[--color-muted]">
                      {turn.speaker}
                    </span>
                    {turn.intent && (
                      <span className="font-[family-name:--font-mono] text-[11px] text-[--color-muted]">
                        {turn.intent}
                      </span>
                    )}
                    <span className="ml-auto tnum text-[11px] text-[--color-muted]">
                      {[
                        turn.stt_ms && `stt ${turn.stt_ms}ms`,
                        turn.llm_ms && `llm ${turn.llm_ms}ms`,
                        turn.tts_ms && `tts ${turn.tts_ms}ms`,
                      ].filter(Boolean).join(' · ')}
                    </span>
                  </div>
                  <p className="mt-0.5">{turn.message}</p>

                  {turn.tool_calls?.length > 0 && (
                    <ul className="mt-1.5 space-y-1">
                      {turn.tool_calls.map((tool, index) => (
                        <li
                          key={index}
                          className={`font-[family-name:--font-mono] text-[11px] ${
                            tool.ok ? 'text-[--color-ok]'
                              : tool.error_code === 'CONFIRMATION_REQUIRED' ? 'text-[--color-warn]'
                              : 'text-[--color-bad]'
                          }`}
                        >
                          → {tool.name}({JSON.stringify(tool.arguments)}){' '}
                          {tool.ok ? 'ok' : tool.error_code} · {tool.duration_ms}ms
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </div>

      <Panel title="Jobs from this call">
        {jobs.length === 0 ? (
          <Empty>No background work was queued for this call.</Empty>
        ) : (
          <ul className="space-y-2">
            {jobs.map((job) => (
              <li key={job.id} className="flex flex-wrap items-center gap-2">
                <Link
                  href={`/queue?job=${job.id}`}
                  className="font-[family-name:--font-mono] text-[--color-accent] hover:underline"
                >
                  {job.id.slice(0, 8)}
                </Link>
                <span className="font-[family-name:--font-mono] text-[12px]">{job.job_type}</span>
                <Pill value={job.priority} />
                <Pill value={job.status} />
                <span className="tnum text-[--color-muted]">
                  {job.attempt_count}/{job.max_attempts} attempts
                </span>
                {job.last_error && (
                  <span className="text-[11px] text-[--color-bad]">{job.last_error}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
