/**
 * Tracing and metrics for the platform API.
 *
 * This file must be imported before anything else in the process. OpenTelemetry
 * instruments a library by replacing exports on its module object, so a module
 * that has already been imported and bound its handles keeps the unpatched ones
 * — and produces no spans at all, silently. `src/index.ts` imports this first
 * for exactly that reason.
 *
 * The API is where most traces begin: the dashboard calls it, it calls the AI
 * service, and the trace context rides along in the outgoing HTTP headers
 * automatically from there.
 */
import { NodeSDK } from '@opentelemetry/sdk-node';
import { getNodeAutoInstrumentations } from '@opentelemetry/auto-instrumentations-node';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { ATTR_SERVICE_NAME, ATTR_SERVICE_VERSION } from '@opentelemetry/semantic-conventions';
import { collectDefaultMetrics, Counter, Histogram, Registry } from 'prom-client';
import { config } from './config.js';

const endpoint = process.env.OTEL_EXPORTER_OTLP_ENDPOINT ?? 'http://localhost:4318';

const sdk = new NodeSDK({
  resource: resourceFromAttributes({
    [ATTR_SERVICE_NAME]: 'api',
    [ATTR_SERVICE_VERSION]: '0.1.0',
    'deployment.environment': config.env,
  }),
  traceExporter: new OTLPTraceExporter({ url: `${endpoint.replace(/\/$/, '')}/v1/traces` }),
  instrumentations: [
    getNodeAutoInstrumentations({
      // Reading a file is not a distributed-systems problem, and fs spans bury
      // the HTTP and SQL ones that are.
      '@opentelemetry/instrumentation-fs': { enabled: false },
      '@opentelemetry/instrumentation-http': {
        // Health and metrics are polled constantly; tracing them would drown
        // the traces of real work in a view meant to be read by a human.
        ignoreIncomingRequestHook: (request) =>
          request.url === '/health' || request.url === '/metrics',
      },
    }),
  ],
});

sdk.start();

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  // Flush whatever is buffered. Without this the spans from the last few
  // seconds before a restart are simply lost — which is exactly the window you
  // care about when a restart is what you are investigating.
  process.once(signal, () => {
    void sdk.shutdown().catch(() => {});
  });
}

// ---------------------------------------------------------------- metrics

export const registry = new Registry();
registry.setDefaultLabels({ service: 'api' });
collectDefaultMetrics({ register: registry });

export const httpRequests = new Counter({
  name: 'voiceops_api_requests_total',
  help: 'HTTP requests served by the platform API.',
  labelNames: ['method', 'route', 'status'],
  registers: [registry],
});

export const httpDuration = new Histogram({
  name: 'voiceops_api_request_duration_seconds',
  help: 'Time to serve one API request.',
  labelNames: ['method', 'route', 'status'],
  // A voice turn proxied to the AI service takes seconds, not milliseconds, so
  // the default buckets would pile every real turn into +Inf.
  buckets: [0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30],
  registers: [registry],
});
