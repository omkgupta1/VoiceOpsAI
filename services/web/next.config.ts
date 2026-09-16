import type { NextConfig } from 'next';

const config: NextConfig = {
  // The dashboard talks only to the platform API, which is the only public
  // entry point (ADR: the AI service stays internal).
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:3000',
  },
};

export default config;
