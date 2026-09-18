/**
 * Shared presentation pieces.
 *
 * Status colouring is centralised because the same statuses appear on five
 * screens, and "did DEAD_LETTER read as red on the queue page but grey on the
 * call page" is the sort of inconsistency that makes a dashboard untrustworthy.
 *
 * Tones resolve to inline custom-property styles rather than Tailwind classes.
 * Tailwind cannot see a class name assembled at runtime, so `text-[--color-${x}]`
 * would silently produce no CSS at all — a bug that only shows up for whichever
 * status you did not happen to have on screen while developing.
 */
'use client';

import Link from 'next/link';
import { useEffect, useRef, useState, type CSSProperties } from 'react';

export type Tone = 'ok' | 'accent' | 'accent2' | 'warn' | 'bad' | 'muted';

const TONE_RGB: Record<Tone, string> = {
  ok: '--ok-rgb',
  accent: '--accent-rgb',
  accent2: '--accent2-rgb',
  warn: '--warn-rgb',
  bad: '--bad-rgb',
  muted: '--muted-rgb',
};

const TONE_INK: Record<Tone, string> = {
  ok: '--color-ok',
  accent: '--color-accent',
  accent2: '--color-accent2',
  warn: '--color-warn',
  bad: '--color-bad',
  muted: '--color-muted',
};

/** Text, border and a faint fill of the same hue, at readable alphas. */
export function toneStyle(tone: Tone, fill = 0.12, border = 0.34): CSSProperties {
  return {
    color: `var(${TONE_INK[tone]})`,
    backgroundColor: `rgb(var(${TONE_RGB[tone]}) / ${fill})`,
    borderColor: `rgb(var(${TONE_RGB[tone]}) / ${border})`,
  };
}

export function toneVar(tone: Tone): string {
  return `var(${TONE_INK[tone]})`;
}

const STATUS_TONE: Record<string, Tone> = {
  // calls
  COMPLETED: 'ok',
  IN_PROGRESS: 'accent2',
  ESCALATED: 'warn',
  FAILED: 'bad',
  ABANDONED: 'muted',
  // jobs
  SUCCESS: 'ok',
  QUEUED: 'accent',
  PROCESSING: 'accent2',
  SCHEDULED: 'muted',
  RETRY_WAIT: 'warn',
  DEAD_LETTER: 'bad',
  CANCELLED: 'muted',
  // priorities
  HIGH: 'bad',
  MEDIUM: 'warn',
  LOW: 'muted',
  // error classes
  RETRYABLE: 'warn',
  PERMANENT: 'bad',
};

/** Statuses that mean "this is moving right now" and so earn a pulsing dot. */
const LIVE = new Set(['IN_PROGRESS', 'PROCESSING', 'RETRY_WAIT', 'QUEUED']);

export function Pill({ value, tone }: { value: string | null | undefined; tone?: Tone }) {
  if (!value) return <span className="text-[var(--color-muted)]">—</span>;
  const resolved = tone ?? STATUS_TONE[value] ?? 'muted';
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[2px] text-[11px] font-medium whitespace-nowrap"
      style={toneStyle(resolved)}
    >
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${LIVE.has(value) ? 'breathe' : ''}`}
        style={{ backgroundColor: toneVar(resolved) }}
      />
      {value.replace(/_/g, ' ').toLowerCase()}
    </span>
  );
}

export function Panel({
  title, action, children, className = '', index = 0, accent,
}: {
  title?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  /** Position in a group, for the entry stagger. */
  index?: number;
  /** Paints a hairline of this hue along the panel's top edge. */
  accent?: Tone;
}) {
  return (
    <section
      className={`glass lift rise relative overflow-hidden rounded-2xl ${className}`}
      style={{ '--i': index } as CSSProperties}
    >
      {accent && (
        <span
          className="absolute inset-x-0 top-0 h-px"
          style={{
            background: `linear-gradient(90deg, transparent, ${toneVar(accent)}, transparent)`,
          }}
        />
      )}
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-[var(--color-line)] px-4 py-3">
          <h2 className="text-[13px] font-semibold tracking-tight">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

/**
 * Counts from the previous value to the next one.
 *
 * It animates between renders, not just from zero on mount, so a queue depth
 * moving 3 → 7 while you watch reads as movement rather than as a number that
 * silently became a different number.
 */
export function useCountUp(target: number, ms = 700): number {
  const [shown, setShown] = useState(target);
  const from = useRef(target);
  const frame = useRef<number>(0);

  useEffect(() => {
    const start = performance.now();
    const origin = from.current;
    const delta = target - origin;
    if (delta === 0) return;

    const step = (now: number) => {
      const t = Math.min((now - start) / ms, 1);
      // Ease out cubic: fast first, settling gently onto the final value.
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(Math.round(origin + delta * eased));
      if (t < 1) frame.current = requestAnimationFrame(step);
      else from.current = target;
    };

    frame.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame.current);
  }, [target, ms]);

  return shown;
}

/** Adds `.flash` for a moment whenever the watched value changes. */
export function useFlashOnChange(value: unknown): string {
  const previous = useRef(value);
  const [lit, setLit] = useState(false);

  useEffect(() => {
    if (previous.current === value) return;
    previous.current = value;
    setLit(true);
    const timer = setTimeout(() => setLit(false), 900);
    return () => clearTimeout(timer);
  }, [value]);

  return lit ? 'flash' : '';
}

export function Stat({
  label, value, tone, hint, icon, index = 0, live = false,
}: {
  label: string;
  value: React.ReactNode;
  /** A Tone name, or the legacy Tailwind class the pages used to pass. */
  tone?: Tone | string;
  hint?: string;
  icon?: string;
  index?: number;
  /** Count up and flash on change — for values that move on their own. */
  live?: boolean;
}) {
  const numeric = typeof value === 'number' ? value : null;
  const counted = useCountUp(numeric ?? 0);
  const flash = useFlashOnChange(numeric);
  const resolved = (tone && tone in TONE_INK ? tone : undefined) as Tone | undefined;
  const shown = numeric === null ? value : live ? counted : numeric;

  return (
    <div
      className="glass lift rise relative overflow-hidden rounded-2xl px-4 py-3.5"
      style={{ '--i': index } as CSSProperties}
    >
      {resolved && (
        <span
          className="absolute inset-y-0 left-0 w-[3px]"
          style={{ background: toneVar(resolved) }}
        />
      )}
      <div className="flex items-center gap-1.5">
        {icon && <span aria-hidden className="text-[12px] opacity-80">{icon}</span>}
        <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--color-muted)]">
          {label}
        </span>
      </div>
      <div
        className={`tnum mt-1.5 text-[28px] leading-none font-semibold ${live ? flash : ''}`}
        style={resolved ? { color: toneVar(resolved) } : undefined}
      >
        {shown}
      </div>
      {hint && <div className="mt-1.5 text-[11px] text-[var(--color-muted)]">{hint}</div>}
    </div>
  );
}

/**
 * A labelled proportional bar.
 *
 * `grow` defers the width by one frame so the browser has a zero-width state to
 * transition from; without it the bar is simply painted at its final size.
 */
export function Bar({
  label, value, max, tone = 'accent', display,
}: {
  label: string;
  value: number;
  max: number;
  tone?: Tone;
  display?: React.ReactNode;
}) {
  const [grown, setGrown] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setGrown(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;

  return (
    <div className="flex items-center gap-3">
      <span className="w-28 shrink-0 truncate text-[var(--color-muted)]">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--color-raised)]">
        <div
          className="bar-fill h-full rounded-full"
          style={{
            width: grown ? `${pct}%` : '0%',
            background: `linear-gradient(90deg, rgb(var(${TONE_RGB[tone]}) / 0.55), ${toneVar(tone)})`,
            boxShadow: `0 0 12px -2px ${toneVar(tone)}`,
          }}
        />
      </div>
      <span className="tnum w-16 shrink-0 text-right">{display ?? value}</span>
    </div>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2.5 py-2">
      {Array.from({ length: rows }).map((_, index) => (
        <div
          key={index}
          className="shimmer h-4 rounded-lg"
          style={{ width: `${92 - index * 11}%` }}
        />
      ))}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="py-12 text-center text-[var(--color-muted)]">
      <div aria-hidden className="mb-2 text-2xl opacity-40">◦</div>
      {children}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div
      className="rise flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5"
      style={toneStyle('bad', 0.1)}
    >
      <span aria-hidden className="mt-[1px]">⚠</span>
      <span>{message}</span>
    </div>
  );
}

export function Ago({ iso }: { iso: string | null }) {
  if (!iso) return <span className="text-[var(--color-muted)]">—</span>;
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  const [value, unit] =
    seconds < 60 ? [seconds, 's'] :
    seconds < 3600 ? [Math.floor(seconds / 60), 'm'] :
    seconds < 86400 ? [Math.floor(seconds / 3600), 'h'] :
    [Math.floor(seconds / 86400), 'd'];
  return (
    <span className="tnum whitespace-nowrap text-[var(--color-muted)]" title={new Date(iso).toLocaleString()}>
      {value}{unit} ago
    </span>
  );
}

export function Duration({ ms }: { ms: number | null | undefined }) {
  if (ms === null || ms === undefined) return <span className="text-[var(--color-muted)]">—</span>;
  return <span className="tnum">{ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`}</span>;
}

/** Monospace identifiers, underlined on hover — they are the main way around. */
export function Mono({
  children, onClick, href,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  href?: string;
}) {
  const style =
    'font-[family-name:var(--font-mono)] text-[var(--color-accent2)] transition hover:text-[var(--color-accent)] hover:underline underline-offset-2';
  if (href) return <Link href={href} className={style}>{children}</Link>;
  return <button onClick={onClick} className={style}>{children}</button>;
}

export function CallLink({ id }: { id: string }) {
  return <Mono href={`/calls/${id}`}>{id.slice(0, 8)}</Mono>;
}

/** A row that tints and nudges right when the pointer is over it. */
export const ROW =
  'border-b border-[var(--color-line)] last:border-0 transition-colors hover:bg-[var(--color-raised)]';
