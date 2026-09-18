'use client';

import { use, useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { api, type CallDetail, type Job, type Turn, tokens } from '@/lib/api';
import { Ago, Duration, Empty, ErrorNote, Mono, Panel, Pill, Skeleton, toneStyle, toneVar, type Tone } from '@/lib/ui';

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
  if (!call) return <Panel><Skeleton rows={6} /></Panel>;

  return (
    <div className="space-y-5">
      <div className="rise flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link
            href="/calls"
            className="text-[var(--color-muted)] transition-colors hover:text-[var(--color-accent)]"
          >
            ← Calls
          </Link>
          <h1 className="mt-1 font-[family-name:var(--font-mono)] text-xl font-semibold">
            <span className="grad-text">{call.id.slice(0, 8)}</span>
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <Pill value={call.status} />
          {call.escalation_status !== 'NONE' && <Pill value={call.escalation_status} tone="warn" />}
          {canEscalate && call.escalation_status === 'NONE' && (
            <button
              onClick={escalate} disabled={escalating}
              className="rounded-xl border px-3.5 py-1.5 font-medium transition hover:brightness-125 active:scale-[0.97] disabled:opacity-60"
              style={toneStyle('warn', 0.12)}
            >
              {escalating ? 'Escalating…' : '↗ Escalate to human'}
            </button>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel index={0} accent="accent" title="Call" className="lg:col-span-1">
          <dl className="space-y-2.5">
            {([
              ['Customer', call.customer_name ?? 'unknown'],
              ['Phone', call.customer_phone ?? '—'],
              ['Intent', call.primary_intent ?? '—'],
              ['Flow', call.flow_id ?? '—'],
              ['Channel', call.channel],
              ['Turns', String(conversation?.length ?? '—')],
            ] as const).map(([label, value]) => (
              <div key={label} className="flex justify-between gap-4">
                <dt className="text-[var(--color-muted)]">{label}</dt>
                <dd className="truncate text-right">{value}</dd>
              </div>
            ))}
            <div className="flex justify-between gap-4">
              <dt className="text-[var(--color-muted)]">Duration</dt>
              <dd><Duration ms={call.duration_ms} /></dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-[var(--color-muted)]">Started</dt>
              <dd><Ago iso={call.started_at} /></dd>
            </div>
          </dl>

          {call.escalation_reason && (
            <div className="mt-3 rounded-xl border px-3 py-2 text-[12px]" style={toneStyle('warn', 0.08)}>
              {call.escalation_reason}
            </div>
          )}
        </Panel>

        <Panel index={1} accent="accent2" title="Conversation" className="lg:col-span-2">
          {conversation === null ? (
            // Transcripts are a separate permission from call metadata.
            <Empty>Your role cannot read transcripts.</Empty>
          ) : conversation.length === 0 ? (
            <Empty>No turns recorded.</Empty>
          ) : (
            <ol className="space-y-3">
              {conversation.map((turn, index) => {
                const customer = turn.speaker?.toUpperCase() === 'CUSTOMER';
                return (
                  <li
                    key={turn.turn_index}
                    className="rise rounded-xl border px-3.5 py-2.5"
                    style={{
                      // Speaker is carried by the whole row, not a label — a
                      // transcript is scanned, not read line by line.
                      ...toneStyle(customer ? 'accent2' : 'accent', 0.05, 0.2),
                      color: 'var(--color-ink)',
                      '--i': Math.min(index, 10),
                    } as React.CSSProperties}
                  >
                    <div className="flex items-baseline gap-2">
                      <span
                        className="text-[10px] font-semibold uppercase tracking-[0.08em]"
                        style={{ color: toneVar(customer ? 'accent2' : 'accent') }}
                      >
                        {turn.speaker}
                      </span>
                      {turn.intent && (
                        <span className="font-[family-name:var(--font-mono)] text-[11px] text-[var(--color-muted)]">
                          {turn.intent}
                        </span>
                      )}
                      <span className="ml-auto tnum text-[11px] text-[var(--color-muted)]">
                        {[
                          turn.stt_ms && `stt ${turn.stt_ms}ms`,
                          turn.llm_ms && `llm ${turn.llm_ms}ms`,
                          turn.tts_ms && `tts ${turn.tts_ms}ms`,
                        ].filter(Boolean).join(' · ')}
                      </span>
                    </div>
                    <p className="mt-1 leading-relaxed">{turn.message}</p>

                    {turn.tool_calls?.length > 0 && (
                      <ul className="mt-2 space-y-1">
                        {turn.tool_calls.map((tool, toolIndex) => {
                          const tone: Tone = tool.ok ? 'ok'
                            : tool.error_code === 'CONFIRMATION_REQUIRED' ? 'warn'
                            : 'bad';
                          return (
                            <li
                              key={toolIndex}
                              className="flex items-center gap-2 rounded-lg border px-2 py-1 font-[family-name:var(--font-mono)] text-[11px]"
                              style={toneStyle(tone, 0.08, 0.22)}
                            >
                              <span aria-hidden>→</span>
                              <span className="truncate">
                                {tool.name}({JSON.stringify(tool.arguments)})
                              </span>
                              <span className="ml-auto whitespace-nowrap">
                                {tool.ok ? 'ok' : tool.error_code} · {tool.duration_ms}ms
                              </span>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </Panel>
      </div>

      <Panel index={2} title="Jobs from this call">
        {jobs.length === 0 ? (
          <Empty>No background work was queued for this call.</Empty>
        ) : (
          <ul className="space-y-2">
            {jobs.map((job, index) => (
              <li
                key={job.id}
                className="rise flex flex-wrap items-center gap-2 rounded-xl border border-[var(--color-line)] px-3 py-2 transition-colors hover:bg-[var(--color-raised)]"
                style={{ '--i': index } as React.CSSProperties}
              >
                <Mono href={`/queue?job=${job.id}`}>{job.id.slice(0, 8)}</Mono>
                <span className="font-[family-name:var(--font-mono)] text-[12px]">{job.job_type}</span>
                <Pill value={job.priority} />
                <Pill value={job.status} />
                <span className="tnum text-[var(--color-muted)]">
                  {job.attempt_count}/{job.max_attempts} attempts
                </span>
                {job.last_error && (
                  <span className="text-[11px]" style={{ color: toneVar('bad') }}>{job.last_error}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
