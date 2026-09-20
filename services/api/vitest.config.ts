import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['src/**/*.test.ts'],
    // Integration tests reach a running Postgres and Redis; the unit pass in CI
    // runs `--project unit` equivalent via `npm run test:unit`, which excludes them.
    environment: 'node',
    reporters: process.env.CI ? ['default', 'junit'] : ['default'],
    outputFile: { junit: 'reports/junit.xml' },
    // A voice turn proxied to the AI service is genuinely slow.
    testTimeout: 30_000,
  },
});
