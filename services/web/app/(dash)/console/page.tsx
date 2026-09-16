'use client';

import { useEffect, useRef, useState } from 'react';
import { api, type TurnResponse } from '@/lib/api';
import { ErrorNote, Panel, Pill } from '@/lib/ui';

interface Entry {
  who: 'you' | 'agent';
  text: string;
  turn?: TurnResponse;
  failed?: boolean;
}

export default function ConsolePage() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [callId, setCallId] = useState<string | undefined>();
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [typed, setTyped] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [micReady, setMicReady] = useState(false);

  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    navigator.mediaDevices
      ?.getUserMedia({ audio: true })
      .then((granted) => { stream.current = granted; setMicReady(true); })
      .catch(() => setMicReady(false));
    return () => stream.current?.getTracks().forEach((track) => track.stop());
  }, []);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }); }, [entries]);

  function record(turn: TurnResponse, heard?: string) {
    setCallId(turn.call_id);
    setEntries((current) => [
      ...current,
      ...(heard ? [{ who: 'you' as const, text: heard }] : []),
      { who: 'agent', text: turn.reply, turn },
    ]);
    if (turn.audio?.base64) {
      void new Audio(`data:${turn.audio.mime_type};base64,${turn.audio.base64}`).play().catch(() => {});
    }
  }

  async function send(action: () => Promise<TurnResponse>, echo?: string) {
    setBusy(true);
    setError(null);
    if (echo) setEntries((current) => [...current, { who: 'you', text: echo }]);
    try {
      const turn = await action();
      record(turn, echo ? undefined : turn.transcript);
    } catch (caught) {
      setEntries((current) => [
        ...current,
        { who: 'agent', text: (caught as Error).message, failed: true },
      ]);
    } finally {
      setBusy(false);
    }
  }

  function startRecording() {
    if (!stream.current || busy || recording) return;
    chunks.current = [];
    const media = new MediaRecorder(stream.current);
    media.ondataavailable = (event) => { if (event.data.size) chunks.current.push(event.data); };
    media.onstop = () => {
      const blob = new Blob(chunks.current, { type: media.mimeType });
      // A stab at the button produces a few bytes of nothing.
      if (blob.size < 1200) return;
      void send(() => api.voiceAudio(blob, callId));
    };
    media.start();
    recorder.current = media;
    setRecording(true);
  }

  function stopRecording() {
    if (!recording) return;
    recorder.current?.stop();
    setRecording(false);
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold tracking-tight">Console</h1>
        {callId && (
          <a href={`/calls/${callId}`} className="font-[family-name:--font-mono] text-[--color-accent] hover:underline">
            call {callId.slice(0, 8)} →
          </a>
        )}
      </div>

      {error && <ErrorNote message={error} />}

      <Panel>
        <button
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onMouseLeave={stopRecording}
          onTouchStart={(e) => { e.preventDefault(); startRecording(); }}
          onTouchEnd={stopRecording}
          disabled={!micReady || busy}
          className={`w-full rounded-xl border px-4 py-5 font-medium transition select-none ${
            recording
              ? 'border-[--color-bad] bg-[--color-bad] text-white'
              : 'border-[--color-line] hover:border-[--color-accent]'
          } disabled:opacity-50`}
        >
          {!micReady ? 'Microphone unavailable — allow access and reload'
            : busy ? 'Thinking…'
            : recording ? 'Listening… release to send'
            : 'Hold to talk'}
        </button>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            const text = typed.trim();
            if (!text || busy) return;
            setTyped('');
            void send(() => api.voiceTurn(text, callId), text);
          }}
          className="mt-3 flex gap-2"
        >
          <input
            value={typed} onChange={(event) => setTyped(event.target.value)}
            placeholder="…or type, if you would rather not talk"
            className="flex-1 rounded-lg border border-[--color-line] bg-[--color-panel] px-3 py-2 outline-none focus:border-[--color-accent]"
          />
          <button
            type="submit" disabled={busy || !typed.trim()}
            className="rounded-lg border border-[--color-line] px-3 py-2 hover:border-[--color-accent] disabled:opacity-50"
          >
            Send
          </button>
        </form>
      </Panel>

      <div className="space-y-3">
        {entries.length === 0 && (
          <p className="py-8 text-center text-[--color-muted]">
            Try: &ldquo;is flight AI858 delayed?&rdquo;
          </p>
        )}

        {entries.map((entry, index) => (
          <Panel key={index}>
            <div className="flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-wide text-[--color-muted]">
                {entry.who}
              </span>
              {entry.turn?.intent && <Pill value={entry.turn.intent} />}
              {entry.turn?.awaiting_confirmation && <Pill value="AWAITING CONFIRMATION" />}
              {entry.turn?.escalated && <Pill value="ESCALATED" />}
              {entry.turn?.flow?.state && (
                <span className="ml-auto font-[family-name:--font-mono] text-[11px] text-[--color-muted]">
                  {entry.turn.flow.state}
                </span>
              )}
            </div>

            <p className={`mt-1 ${entry.failed ? 'text-[--color-bad]' : ''}`}>{entry.text}</p>

            {entry.turn && (entry.turn.tool_calls.length > 0 || entry.turn.queued_jobs.length > 0) && (
              <div className="mt-2 space-y-0.5 border-t border-[--color-line] pt-2">
                {entry.turn.tool_calls.map((tool, i) => (
                  <div
                    key={i}
                    className={`font-[family-name:--font-mono] text-[11px] ${
                      tool.ok ? 'text-[--color-ok]'
                        : tool.error_code === 'CONFIRMATION_REQUIRED' ? 'text-[--color-warn]'
                        : 'text-[--color-bad]'
                    }`}
                  >
                    → {tool.name}({JSON.stringify(tool.arguments)}){' '}
                    {tool.ok ? 'ok' : tool.error_code} · {tool.duration_ms}ms
                  </div>
                ))}
                {entry.turn.queued_jobs.map((job) => (
                  <div key={job.job_id} className="font-[family-name:--font-mono] text-[11px] text-[--color-warn]">
                    ⏱ queued {job.tool} as job {job.job_id.slice(0, 8)} — will complete in the background
                  </div>
                ))}
                <div className="tnum pt-1 text-[11px] text-[--color-muted]">
                  {Object.entries(entry.turn.timings)
                    .filter(([, value]) => value)
                    .map(([key, value]) => `${key.replace('_ms', '')} ${value}ms`)
                    .join(' · ')}
                </div>
              </div>
            )}
          </Panel>
        ))}
        <div ref={bottom} />
      </div>
    </div>
  );
}
