'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { tokens, type User } from '@/lib/api';

/** Nav entries a role cannot use are hidden rather than shown and then refused. */
const NAV = [
  { href: '/calls', label: 'Calls', permission: 'calls:read' },
  { href: '/queue', label: 'Queue', permission: 'jobs:read' },
  { href: '/failures', label: 'Failures', permission: 'failures:read' },
  { href: '/console', label: 'Console', permission: 'voice:use' },
];

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

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-[--color-line] bg-[--color-panel]/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link href="/calls" className="font-semibold tracking-tight">VoiceOps</Link>
          <nav className="flex gap-1">
            {visible.map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href} href={item.href}
                  className={`rounded-lg px-2.5 py-1 ${
                    active ? 'bg-[--color-ground] font-medium' : 'text-[--color-muted] hover:text-[--color-ink]'
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-[--color-muted]">
            <span className="hidden sm:inline">{user.name}</span>
            <span className="rounded-full border border-[--color-line] px-2 py-[1px] text-[11px]">
              {user.role}
            </span>
            <button
              onClick={() => { tokens.clear(); router.replace('/login'); }}
              className="hover:text-[--color-ink]"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}
