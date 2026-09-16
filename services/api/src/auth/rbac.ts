/**
 * Role-based access control.
 *
 * Permissions are listed per role in one table rather than checked as `if
 * (role === 'ADMIN' || role === 'SUPERVISOR')` at each call site. Scattered
 * role comparisons drift: someone adds an endpoint, copies the wrong condition,
 * and a CX agent can suddenly requeue jobs. A table can be read in full in a few
 * seconds and tested exhaustively, which `src/test/rbac.test.ts` does.
 *
 * Roles follow overview.md §21.
 */

export const ROLES = ['USER', 'CX_AGENT', 'SUPERVISOR', 'ADMIN'] as const;
export type Role = (typeof ROLES)[number];

export const PERMISSIONS = [
  'voice:use',          // talk to the agent
  'calls:read',         // list and view calls
  'conversations:read', // read transcripts
  'calls:escalate',     // hand a call to a human
  'jobs:read',          // see the queue and job history
  'jobs:retry',         // requeue a dead-lettered job
  'failures:read',      // failure monitoring
  'analytics:read',     // aggregate reporting
  'users:manage',       // create and modify operators
  'config:manage',      // chaos controls, system configuration
] as const;
export type Permission = (typeof PERMISSIONS)[number];

const CX_AGENT: Permission[] = [
  'voice:use',
  'calls:read',
  'conversations:read',
  'calls:escalate',
];

// A supervisor sees why things went wrong and can act on it; an agent handles
// the conversation in front of them.
const SUPERVISOR: Permission[] = [
  ...CX_AGENT,
  'jobs:read',
  'jobs:retry',
  'failures:read',
  'analytics:read',
];

export const ROLE_PERMISSIONS: Record<Role, readonly Permission[]> = {
  USER: ['voice:use'],
  CX_AGENT,
  SUPERVISOR,
  ADMIN: PERMISSIONS,
};

export function can(role: Role, permission: Permission): boolean {
  return ROLE_PERMISSIONS[role]?.includes(permission) ?? false;
}
