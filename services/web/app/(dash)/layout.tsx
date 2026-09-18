'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { tokens, type User } from '@/lib/api';
import { toneStyle, toneVar, type Tone } from '@/lib/ui';

/** Nav entries a role cannot use are hidden rather than shown and then refused. */
const NAV = [
  { href: '/calls', label: 'Calls', permission: 'calls:read', icon: '◉', tone: 'accent2' as Tone },
  { href: '/queue', label: 'Queue', permission: 'jobs:read', icon: '≡', tone: 'accent' as Tone },
  { href: '/failures', label: 'Failures', permission: 'failures:read', icon: '△', tone: 'warn' as Tone },
  { href: '/console', label: 'Console', permission: 'voice:use', icon: '◍', tone: 'ok' as Tone },
];

/** Seniority reads at a glance when the badge carries it. */
const ROLE_TONE: Record<string, Tone> = {
  ADMIN: 'bad',
  SUPERVISOR: 'accent',
  CX_AGENT: 'accent2',
  USER: 'muted',
};

export default function DashLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    const current = tokens.getUser();
    if (!current) router.replace('/login');
    else setUser(current);
  }, [router]);

  // Render nothing until the token is confirmed, so dashboard content never
  // flashes on screen for someone who is about to be redirected to login.
  if (!user) return null;

  const visible = NAV.filter((item) => user.permissions.includes(item.permission));
  const roleTone = ROLE_TONE[user.role] ?? 'muted';
  const initials = user.name.split(' ').map((part) => part[0]).slice(0, 2).join('').toUpperCase();

  return (
    <div className="min-h-screen">
      <header className="glass sticky top-0 z-20 rounded-none border-x-0 border-t-0">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
          <Link href="/calls" className="flex items-center gap-2 font-semibold tracking-tight">
            <span
              aria-hidden
              className="grid h-6 w-6 place-items-center rounded-lg text-[11px] text-white"
              style={{
                background: 'linear-gradient(135deg, var(--color-accent), var(--color-accent2))',
                boxShadow: '0 0 16px -4px var(--color-accent)',
              }}
            >
              ◆
            </span>
            <span className="grad-text">VoiceOps</span>
          </Link>

          <nav className="flex gap-1">
            {visible.map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`relative flex items-center gap-1.5 rounded-xl px-3 py-1.5 transition-colors ${
                    active ? 'font-medium' : 'text-[var(--color-muted)] hover:text-[var(--color-ink)]'
                  }`}
                  style={active ? toneStyle(item.tone, 0.14, 0.3) : undefined}
                >
                  <span aria-hidden className="text-[11px]">{item.icon}</span>
                  {item.label}
                  {active && (
                    // The underline is the only element that says "you are here"
                    // twice; it survives when the tint is hard to see on a bright
                    // external display.
                    <span
                      className="absolute inset-x-3 -bottom-[1px] h-[2px] rounded-full"
                      style={{ background: toneVar(item.tone) }}
                    />
                  )}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-2.5 text-[var(--color-muted)]">
            <span
              aria-hidden
              className="grid h-7 w-7 place-items-center rounded-full border text-[10px] font-semibold"
              style={toneStyle(roleTone, 0.16)}
            >
              {initials}
            </span>
            <span className="hidden text-[var(--color-ink)] sm:inline">{user.name}</span>
            <span
              className="rounded-full border px-2 py-[1px] text-[10px] font-medium tracking-wide"
              style={toneStyle(roleTone)}
            >
              {user.role.replace('_', ' ')}
            </span>
            <button
              onClick={() => { tokens.clear(); router.replace('/login'); }}
              className="rounded-lg px-2 py-1 transition-colors hover:bg-[var(--color-raised)] hover:text-[var(--color-ink)]"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-7">{children}</main>
    </div>
  );
}
