'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { tokens } from '@/lib/api';

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace(tokens.get() ? '/calls' : '/login');
  }, [router]);
  return null;
}
