'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api, tokens, ApiError } from '@/lib/api';

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
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-6">
      <h1 className="text-lg font-semibold tracking-tight">VoiceOps</h1>
      <p className="mt-1 text-[--color-muted]">Servicing dashboard</p>

      <form onSubmit={submit} className="mt-8 space-y-3">
        <label className="block">
          <span className="text-[11px] uppercase tracking-wide text-[--color-muted]">Email</span>
          <input
            type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
            className="mt-1 w-full rounded-lg border border-[--color-line] bg-[--color-panel] px-3 py-2 outline-none focus:border-[--color-accent]"
          />
        </label>
        <label className="block">
          <span className="text-[11px] uppercase tracking-wide text-[--color-muted]">Password</span>
          <input
            type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
            className="mt-1 w-full rounded-lg border border-[--color-line] bg-[--color-panel] px-3 py-2 outline-none focus:border-[--color-accent]"
          />
        </label>

        {error && (
          <div className="rounded-lg border border-[--color-bad] px-3 py-2 text-[--color-bad]">{error}</div>
        )}

        <button
          type="submit" disabled={busy}
          className="w-full rounded-lg bg-[--color-accent] px-3 py-2 font-medium text-white disabled:opacity-60"
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>

      <p className="mt-6 text-[11px] leading-relaxed text-[--color-muted]">
        Seeded accounts, all with password <code>voiceops123</code>:<br />
        <code>admin@</code> · <code>supervisor@</code> · <code>agent1@</code> ·{' '}
        <code>agent2@voiceops.ai</code>
        <br />
        <span className="mt-1 block">
          Roles differ: an agent sees calls but not the queue or analytics.
        </span>
      </p>
    </main>
  );
}
