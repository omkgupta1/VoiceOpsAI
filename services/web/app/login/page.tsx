'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api, tokens, ApiError } from '@/lib/api';

/** One click fills the form — four seeded roles, and the point is to compare them. */
const ACCOUNTS = [
  { email: 'admin@voiceops.ai', role: 'Admin', note: 'everything' },
  { email: 'supervisor@voiceops.ai', role: 'Supervisor', note: 'queue + failures' },
  { email: 'agent1@voiceops.ai', role: 'Agent', note: 'calls + console only' },
];

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('supervisor@voiceops.ai');
  const [password, setPassword] = useState('voiceops123');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { token, user } = await api.login(email, password);
      tokens.set(token, user);
      router.replace('/calls');
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not sign in');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-6 py-10">
      <div className="rise">
        <span
          aria-hidden
          className="grid h-11 w-11 place-items-center rounded-2xl text-lg text-white"
          style={{
            background: 'linear-gradient(135deg, var(--color-accent), var(--color-accent2))',
            boxShadow: '0 0 34px -6px var(--color-accent)',
          }}
        >
          ◆
        </span>
        <h1 className="mt-4 text-2xl font-semibold tracking-tight">
          <span className="grad-text">VoiceOps</span>
        </h1>
        <p className="mt-1 text-[var(--color-muted)]">Self-healing voice support · servicing dashboard</p>
      </div>

      <form onSubmit={submit} className="glass rise mt-7 space-y-3 rounded-2xl p-5" style={{ '--i': 1 } as React.CSSProperties}>
        <label className="block">
          <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--color-muted)]">Email</span>
          <input
            type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
            className="mt-1.5 w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-raised)] px-3 py-2.5 outline-none transition focus:border-[var(--color-accent)]"
          />
        </label>
        <label className="block">
          <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--color-muted)]">Password</span>
          <input
            type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
            className="mt-1.5 w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-raised)] px-3 py-2.5 outline-none transition focus:border-[var(--color-accent)]"
          />
        </label>

        {error && (
          <div
            className="rise rounded-xl border px-3 py-2"
            style={{
              color: 'var(--color-bad)',
              backgroundColor: 'rgb(var(--bad-rgb) / 0.1)',
              borderColor: 'rgb(var(--bad-rgb) / 0.34)',
            }}
          >
            {error}
          </div>
        )}

        <button
          type="submit" disabled={busy}
          className="w-full rounded-xl px-3 py-2.5 font-medium text-white transition hover:brightness-110 active:scale-[0.99] disabled:opacity-60"
          style={{
            background: 'linear-gradient(135deg, var(--color-accent), var(--color-accent2))',
            boxShadow: '0 10px 26px -12px var(--color-accent)',
          }}
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>

      <div className="rise mt-5" style={{ '--i': 2 } as React.CSSProperties}>
        <p className="text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--color-muted)]">
          Seeded accounts · password voiceops123
        </p>
        <div className="mt-2 space-y-1.5">
          {ACCOUNTS.map((account) => (
            <button
              key={account.email}
              type="button"
              onClick={() => { setEmail(account.email); setPassword('voiceops123'); }}
              className="flex w-full items-center gap-2 rounded-xl border border-[var(--color-line)] bg-[var(--color-panel)]/50 px-3 py-2 text-left transition hover:border-[var(--color-accent)] hover:bg-[var(--color-raised)]"
            >
              <span className="font-medium">{account.role}</span>
              <span className="font-[family-name:var(--font-mono)] text-[11px] text-[var(--color-muted)]">
                {account.email}
              </span>
              <span className="ml-auto text-[11px] text-[var(--color-muted)]">{account.note}</span>
            </button>
          ))}
        </div>
      </div>
    </main>
  );
}
