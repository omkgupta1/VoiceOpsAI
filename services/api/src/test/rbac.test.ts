/**
 * The RBAC matrix, verified exhaustively.
 *
 * Every role against every permission — 40 assertions — because the table is
 * small enough to check completely, and the failure mode it guards against is
 * silent: a permission quietly added to the wrong role reads fine and grants
 * access nobody intended.
 *
 *   npm test
 */
import { ROLES, PERMISSIONS, can, type Permission, type Role } from '../auth/rbac.js';

const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const DIM = '\x1b[2m';
const RESET = '\x1b[0m';

/** The intended matrix, written out independently of the implementation. */
const EXPECTED: Record<Role, Permission[]> = {
  USER: ['voice:use'],
  CX_AGENT: ['voice:use', 'calls:read', 'conversations:read', 'calls:escalate'],
  SUPERVISOR: [
    'voice:use', 'calls:read', 'conversations:read', 'calls:escalate',
    'jobs:read', 'jobs:retry', 'failures:read', 'analytics:read',
  ],
  ADMIN: [...PERMISSIONS],
};

let passed = 0;
const failures: string[] = [];

for (const role of ROLES) {
  const granted: string[] = [];
  for (const permission of PERMISSIONS) {
    const expected = EXPECTED[role].includes(permission);
    const actual = can(role, permission);
    if (actual === expected) {
      passed += 1;
      if (actual) granted.push(permission);
    } else {
      failures.push(
        `${role} ${expected ? 'should' : 'should NOT'} have ${permission}`,
      );
    }
  }
  console.log(`  ${role.padEnd(11)} ${DIM}${granted.length} permissions${RESET}`);
}

// Privilege must only ever increase up the hierarchy. Without this, a
// reorganisation of the table could give an agent something a supervisor lacks.
for (const [lower, higher] of [
  ['USER', 'CX_AGENT'],
  ['CX_AGENT', 'SUPERVISOR'],
  ['SUPERVISOR', 'ADMIN'],
] as const) {
  const missing = PERMISSIONS.filter((p) => can(lower, p) && !can(higher, p));
  if (missing.length === 0) passed += 1;
  else failures.push(`${higher} is missing ${missing.join(', ')} that ${lower} has`);
}

// The two that would matter most if they were ever wrong.
const criticalDenials: [Role, Permission][] = [
  ['CX_AGENT', 'jobs:retry'],
  ['CX_AGENT', 'analytics:read'],
  ['CX_AGENT', 'users:manage'],
  ['SUPERVISOR', 'users:manage'],
  ['SUPERVISOR', 'config:manage'],
  ['USER', 'calls:read'],
];
for (const [role, permission] of criticalDenials) {
  if (!can(role, permission)) passed += 1;
  else failures.push(`${role} must not be able to ${permission}`);
}

const total = passed + failures.length;
console.log(
  `\n  ${failures.length === 0 ? GREEN : RED}${passed}/${total} assertions passed${RESET}\n`,
);
for (const failure of failures) console.log(`    ${RED}✗${RESET} ${failure}`);
process.exit(failures.length === 0 ? 0 : 1);
