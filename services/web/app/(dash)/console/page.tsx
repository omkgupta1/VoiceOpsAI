'use client';

import { useEffect, useRef, useState } from 'react';
import { api, type TurnResponse } from '@/lib/api';
import { ErrorNote, Panel, Pill, toneStyle, toneVar } from '@/lib/ui';

interface Entry {
  who: 'you' | 'agent';
  text: string;
  turn?: TurnResponse;
  failed?: boolean;
}

/** Suggested openers — the fastest way to see the agent do something real. */
const PROMPTS = [
  'Is flight 6E597 delayed?',
  "What's my booking UYO57P?",
  "What's the weather at DEL?",
];

export default function ConsolePage() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [callId, setCallId] = useState<string | undefined>();
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [typed, setTyped] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [micReady, setMicReady] = useState(false);
  const [level, setLevel] = useState(0);

  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const bottom = useRef<HTMLDivElement>(null);
  const audioCtx = useRef<AudioContext | null>(null);
  const meter = useRef<number>(0);

  useEffect(() => {
    navigator.mediaDevices
      ?.getUserMedia({ audio: true })
      .then((granted) => { stream.current = granted; setMicReady(true); })
      .catch(() => setMicReady(false));
    return () => {
      stream.current?.getTracks().forEach((track) => track.stop());
      void audioCtx.current?.close();
      cancelAnimationFrame(meter.current);
    };
  }, []);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }); }, [entries]);

  /**
   * Drives the level meter while recording.
   *
   * Without it the only feedback that the microphone is working is the reply
   * arriving several seconds later — and when STT mishears, you cannot tell
   * whether it heard you badly or heard nothing at all.
   */
  function startMeter(source: MediaStream) {
    const context = audioCtx.current ?? new AudioContext();
    audioCtx.current = context;
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    context.createMediaStreamSource(source).connect(analyser);
    const samples = new Uint8Array(analyser.frequencyBinCount);

    const tick = () => {
      analyser.getByteTimeDomainData(samples);
      let peak = 0;
      for (const sample of samples) peak = Math.max(peak, Math.abs(sample - 128));
      setLevel(Math.min(peak / 60, 1));
      meter.current = requestAnimationFrame(tick);
    };
    tick();
  }

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
    startMeter(stream.current);
  }

  function stopRecording() {
    if (!recording) return;
    recorder.current?.stop();
    setRecording(false);
    cancelAnimationFrame(meter.current);
    setLevel(0);
  }

  return (
    <div className="space-y-5">
      <div className="rise flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">
          <span className="grad-text">Console</span>
        </h1>
        {callId && (
          <a
            href={`/calls/${callId}`}
            className="font-[family-name:var(--font-mono)] text-[var(--color-accent2)] transition hover:text-[var(--color-accent)] hover:underline"
          >
            call {callId.slice(0, 8)} →
          </a>
        )}
      </div>

      {error && <ErrorNote message={error} />}

      <Panel index={0} accent={recording ? 'bad' : 'ok'}>
        <button
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onMouseLeave={stopRecording}
          onTouchStart={(e) => { e.preventDefault(); startRecording(); }}
          onTouchEnd={stopRecording}
          disabled={!micReady || busy}
          className={`relative w-full overflow-hidden rounded-2xl border px-4 py-6 font-medium transition select-none disabled:opacity-50 ${
            recording ? 'halo' : 'hover:border-[var(--color-accent)]'
          }`}
          style={
            recording
              ? { ...toneStyle('bad', 0.18), color: 'var(--color-ink)' }
              : busy
                ? { ...toneStyle('accent', 0.1), color: 'var(--color-ink)' }
                : { borderColor: 'var(--color-line)' }
          }
        >
          {/* The level meter fills the button from the left while you speak. */}
          {recording && (
            <span
              className="absolute inset-y-0 left-0 transition-[width] duration-75"
              style={{
                width: `${level * 100}%`,
                background: `linear-gradient(90deg, rgb(var(--bad-rgb) / 0.35), transparent)`,
              }}
            />
          )}
          <span className="relative flex items-center justify-center gap-2.5">
            <span
              aria-hidden
              className={`text-lg ${recording ? 'breathe' : ''}`}
              style={{ color: recording ? toneVar('bad') : toneVar('ok') }}
            >
              ◉
            </span>
            {!micReady ? 'Microphone unavailable — allow access and reload'
              : busy ? 'Thinking…'
              : recording ? 'Listening… release to send'
              : 'Hold to talk'}
          </span>
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
            className="flex-1 rounded-xl border border-[var(--color-line)] bg-[var(--color-raised)] px-3.5 py-2.5 outline-none transition focus:border-[var(--color-accent)]"
          />
          <button
            type="submit" disabled={busy || !typed.trim()}
            className="rounded-xl px-4 py-2.5 font-medium text-white transition hover:brightness-110 active:scale-[0.97] disabled:opacity-40"
            style={{ background: 'linear-gradient(135deg, var(--color-accent), var(--color-accent2))' }}
          >
            Send
          </button>
        </form>
      </Panel>

      <div className="space-y-3">
        {entries.length === 0 && (
          <div className="rise py-6 text-center" style={{ '--i': 1 } as React.CSSProperties}>
            <p className="text-[var(--color-muted)]">Hold the button and speak, or start with one of these:</p>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  onClick={() => void send(() => api.voiceTurn(prompt, callId), prompt)}
                  disabled={busy}
                  className="rounded-full border px-3.5 py-1.5 transition hover:brightness-125 active:scale-[0.97] disabled:opacity-50"
                  style={toneStyle('accent', 0.08, 0.28)}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}

        {entries.map((entry, index) => {
          const you = entry.who === 'you';
          return (
            <div
              key={index}
              className="rise rounded-2xl border px-4 py-3"
              style={{
                ...toneStyle(entry.failed ? 'bad' : you ? 'accent2' : 'accent', 0.06, 0.24),
                color: 'var(--color-ink)',
              }}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className="text-[10px] font-semibold uppercase tracking-[0.08em]"
                  style={{ color: toneVar(you ? 'accent2' : 'accent') }}
                >
                  {entry.who}
                </span>
                {entry.turn?.intent && <Pill value={entry.turn.intent} tone="accent2" />}
                {entry.turn?.awaiting_confirmation && <Pill value="AWAITING CONFIRMATION" tone="warn" />}
                {entry.turn?.escalated && <Pill value="ESCALATED" tone="warn" />}
                {entry.turn?.flow?.state && (
                  <span className="ml-auto font-[family-name:var(--font-mono)] text-[11px] text-[var(--color-muted)]">
                    {entry.turn.flow.state}
                  </span>
                )}
              </div>

              <p className="mt-1.5 leading-relaxed" style={entry.failed ? { color: toneVar('bad') } : undefined}>
                {entry.text}
              </p>

              {entry.turn && (entry.turn.tool_calls.length > 0 || entry.turn.queued_jobs.length > 0) && (
                <div className="mt-2.5 space-y-1 border-t border-[var(--color-line)] pt-2.5">
                  {entry.turn.tool_calls.map((tool, i) => {
                    const tone = tool.ok ? 'ok'
                      : tool.error_code === 'CONFIRMATION_REQUIRED' ? 'warn'
                      : 'bad';
                    return (
                      <div
                        key={i}
                        className="flex items-center gap-2 rounded-lg border px-2 py-1 font-[family-name:var(--font-mono)] text-[11px]"
                        style={toneStyle(tone, 0.08, 0.22)}
                      >
                        <span aria-hidden>→</span>
                        <span className="truncate">{tool.name}({JSON.stringify(tool.arguments)})</span>
                        <span className="ml-auto whitespace-nowrap">
                          {tool.ok ? 'ok' : tool.error_code} · {tool.duration_ms}ms
                        </span>
                      </div>
                    );
                  })}
                  {entry.turn.queued_jobs.map((job) => (
                    <div
                      key={job.job_id}
                      className="rounded-lg border px-2 py-1 font-[family-name:var(--font-mono)] text-[11px]"
                      style={toneStyle('warn', 0.08, 0.22)}
                    >
                      ⏱ queued {job.tool} as job {job.job_id.slice(0, 8)} — will complete in the background
                    </div>
                  ))}
                  <div className="tnum pt-1 text-[11px] text-[var(--color-muted)]">
                    {Object.entries(entry.turn.timings)
                      .filter(([, value]) => value)
                      .map(([key, value]) => `${key.replace('_ms', '')} ${value}ms`)
                      .join(' · ')}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        <div ref={bottom} />
      </div>
    </div>
  );
}
