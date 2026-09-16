/**
 * Shared presentation pieces.
 *
 * Status colouring is centralised because the same statuses appear on four
 * screens, and "did DEAD_LETTER read as red on the queue page but grey on the
 * call page" is the sort of inconsistency that makes a dashboard untrustworthy.
 */
'use client';

import Link from 'next/link';

const STATUS_TONE: Record<string, string> = {
  // calls
  COMPLETED: 'text-[--color-ok] border-[--color-ok]',
  IN_PROGRESS: 'text-[--color-accent] border-[--color-accent]',
  ESCALATED: 'text-[--color-warn] border-[--color-warn]',
  FAILED: 'text-[--color-bad] border-[--color-bad]',
  ABANDONED: 'text-[--color-muted] border-[--color-line]',
  // jobs
  SUCCESS: 'text-[--color-ok] border-[--color-ok]',
  QUEUED: 'text-[--color-accent] border-[--color-accent]',
  PROCESSING: 'text-[--color-accent] border-[--color-accent]',
  SCHEDULED: 'text-[--color-muted] border-[--color-line]',
  RETRY_WAIT: 'text-[--color-warn] border-[--color-warn]',
  DEAD_LETTER: 'text-[--color-bad] border-[--color-bad]',
  CANCELLED: 'text-[--color-muted] border-[--color-line]',
  // priorities
  HIGH: 'text-[--color-bad] border-[--color-bad]',
  MEDIUM: 'text-[--color-warn] border-[--color-warn]',
  LOW: 'text-[--color-muted] border-[--color-line]',
  // error classes
  RETRYABLE: 'text-[--color-warn] border-[--color-warn]',
  PERMANENT: 'text-[--color-bad] border-[--color-bad]',
};

export function Pill({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-[--color-muted]">—</span>;
  const tone = STATUS_TONE[value] ?? 'text-[--color-muted] border-[--color-line]';
  return (
    <span className={`inline-block rounded-full border px-2 py-[1px] text-[11px] font-medium ${tone}`}>
      {value.replace(/_/g, ' ').toLowerCase()}
    </span>
  );
}

export function Panel({
  title, action, children, className = '',
}: {
  title?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-[--color-line] bg-[--color-panel] ${className}`}
    >
      {title && (
        <header className="flex items-center justify-between border-b border-[--color-line] px-4 py-2.5">
          <h2 className="text-[13px] font-semibold tracking-tight">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({
  label, value, tone = '', hint,
}: {
  label: string;
  value: React.ReactNode;
  tone?: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-[--color-line] bg-[--color-panel] px-4 py-3">
      <div className="text-[11px] uppercase tracking-wide text-[--color-muted]">{label}</div>
      <div className={`tnum mt-1 text-2xl font-semibold ${tone}`}>{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-[--color-muted]">{hint}</div>}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="py-10 text-center text-[--color-muted]">{children}</div>;
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-[--color-bad] px-3 py-2 text-[--color-bad]">
      {message}
    </div>
  );
}

export function Ago({ iso }: { iso: string | null }) {
  if (!iso) return <span className="text-[--color-muted]">—</span>;
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  const [value, unit] =
    seconds < 60 ? [seconds, 's'] :
    seconds < 3600 ? [Math.floor(seconds / 60), 'm'] :
    seconds < 86400 ? [Math.floor(seconds / 3600), 'h'] :
    [Math.floor(seconds / 86400), 'd'];
  return (
    <span className="tnum text-[--color-muted]" title={new Date(iso).toLocaleString()}>
      {value}{unit} ago
    </span>
  );
}

export function Duration({ ms }: { ms: number | null | undefined }) {
  if (ms === null || ms === undefined) return <span className="text-[--color-muted]">—</span>;
  return <span className="tnum">{ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`}</span>;
}

export function CallLink({ id }: { id: string }) {
  return (
    <Link href={`/calls/${id}`} className="font-[family-name:--font-mono] text-[--color-accent] hover:underline">
      {id.slice(0, 8)}
    </Link>
  );
}
