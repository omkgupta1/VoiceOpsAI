/**
 * Load profile for the platform API.
 *
 * Run against a running stack:
 *
 *     make test-load                  # read-only paths, safe to repeat
 *     make test-load SCENARIO=turns   # includes real voice turns — slow, and
 *                                     # each one occupies the local model
 *
 * Deliberately NOT run in CI. The interesting limit here is a single local 7B
 * model that serves roughly one turn at a time; a GitHub runner has no model at
 * all, so a load number from CI would measure the runner, not the system.
 */
import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const API = __ENV.API_URL || 'http://127.0.0.1:3000';
const SCENARIO = __ENV.SCENARIO || 'read';

const loginTime = new Trend('login_duration');
const readTime = new Trend('read_duration');
const turnTime = new Trend('voice_turn_duration');
const failures = new Rate('failed_requests');

export const options = {
  scenarios:
    SCENARIO === 'turns'
      ? {
          // One at a time. The model is the bottleneck and queueing turns
          // behind it measures the queue, not the system.
          voice: { executor: 'constant-vus', vus: 1, duration: '2m', exec: 'voiceTurn' },
        }
      : {
          // Ramp the read paths, which is where a dashboard's real load lives:
          // every open queue page polls every three seconds.
          dashboard: {
            executor: 'ramping-vus',
            startVUs: 1,
            stages: [
              { duration: '20s', target: 10 },
              { duration: '40s', target: 30 },
              { duration: '20s', target: 0 },
            ],
            exec: 'readPaths',
          },
        },
  thresholds: {
    // The dashboard polls constantly; anything above a quarter second is felt.
    read_duration: ['p(95)<250'],
    failed_requests: ['rate<0.01'],
    http_req_failed: ['rate<0.01'],
  },
};

function token() {
  const response = http.post(
    `${API}/api/auth/login`,
    JSON.stringify({ email: 'supervisor@voiceops.ai', password: 'voiceops123' }),
    { headers: { 'content-type': 'application/json' }, tags: { name: 'login' } },
  );
  loginTime.add(response.timings.duration);
  check(response, { 'login succeeded': (r) => r.status === 200 });
  return response.json('token');
}

export function setup() {
  const health = http.get(`${API}/health`);
  if (health.status !== 200) {
    throw new Error(`API is not up at ${API} — run \`make api\` (got ${health.status})`);
  }
  return { token: token() };
}

/**
 * Ad-hoc entry point: `k6 run --vus 5 --duration 30s tests/load/voice.js`.
 *
 * Passing `--vus`/`--duration`/`--stage` on the command line replaces the
 * `scenarios` block wholesale, and k6 then looks for a `default` export. Without
 * this, every ad-hoc invocation fails with "function 'default' not found".
 */
export default function (data) {
  readPaths(data);
}

export function readPaths(data) {
  const headers = { authorization: `Bearer ${data.token}` };

  group('dashboard poll', () => {
    // Exactly what an open queue page does every three seconds.
    for (const path of ['/api/jobs?limit=40', '/api/jobs/dead-letters']) {
      const response = http.get(`${API}${path}`, { headers, tags: { name: path } });
      readTime.add(response.timings.duration);
      failures.add(response.status !== 200);
      check(response, { [`${path} ok`]: (r) => r.status === 200 });
    }
  });

  group('calls', () => {
    for (const path of ['/api/calls?limit=50', '/api/analytics/calls?days=30']) {
      const response = http.get(`${API}${path}`, { headers, tags: { name: path } });
      readTime.add(response.timings.duration);
      failures.add(response.status !== 200);
      check(response, { [`${path} ok`]: (r) => r.status === 200 });
    }
  });

  sleep(3); // the dashboard's own poll interval
}

export function voiceTurn(data) {
  const response = http.post(
    `${API}/api/voice/turn`,
    JSON.stringify({ message: 'Is flight 6E597 delayed?' }),
    {
      headers: { 'content-type': 'application/json', authorization: `Bearer ${data.token}` },
      timeout: '120s',
      tags: { name: 'voice turn' },
    },
  );

  turnTime.add(response.timings.duration);
  failures.add(response.status !== 200);
  check(response, {
    'turn answered': (r) => r.status === 200,
    'the answer is grounded in a tool call': (r) =>
      r.status === 200 && (r.json('tool_calls') || []).length > 0,
  });
}
