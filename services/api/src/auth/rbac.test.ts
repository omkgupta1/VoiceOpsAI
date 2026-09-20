/**
 * The RBAC matrix, verified exhaustively.
 *
 * Every role against every permission, because the table is small enough to
 * check completely and the failure mode it guards against is silent: a
 * permission quietly added to the wrong role reads fine and grants access
 * nobody intended.
 *
 * EXPECTED is written out by hand rather than derived from the implementation.
 * A test that builds its expectation from the code under test asserts only that
 * the code equals itself.
 */
import { describe, expect, it } from 'vitest';
import { ROLES, PERMISSIONS, can, ROLE_PERMISSIONS, type Permission, type Role } from './rbac.js';

const EXPECTED: Record<Role, Permission[]> = {
  USER: ['voice:use'],
  CX_AGENT: ['voice:use', 'calls:read', 'conversations:read', 'calls:escalate'],
  SUPERVISOR: [
    'voice:use', 'calls:read', 'conversations:read', 'calls:escalate',
    'jobs:read', 'jobs:retry', 'failures:read', 'analytics:read',
  ],
  ADMIN: [...PERMISSIONS],
};

describe('the permission matrix', () => {
  // One assertion per cell: 4 roles x 10 permissions.
  for (const role of ROLES) {
    for (const permission of PERMISSIONS) {
      const expected = EXPECTED[role].includes(permission);
      it(`${role} ${expected ? 'can' : 'cannot'} ${permission}`, () => {
        expect(can(role, permission)).toBe(expected);
      });
    }
  }
});

describe('privilege only increases up the hierarchy', () => {
  // Without this, reorganising the table could give an agent something a
  // supervisor lacks — which reads as a promotion and behaves as a hole.
  const ladder: [Role, Role][] = [
    ['USER', 'CX_AGENT'],
    ['CX_AGENT', 'SUPERVISOR'],
    ['SUPERVISOR', 'ADMIN'],
  ];

  for (const [lower, higher] of ladder) {
    it(`${higher} has everything ${lower} has`, () => {
      const missing = PERMISSIONS.filter((p) => can(lower, p) && !can(higher, p));
      expect(missing).toEqual([]);
    });
  }
});

describe('the denials that would matter most', () => {
  const critical: [Role, Permission][] = [
    ['CX_AGENT', 'jobs:retry'],
    ['CX_AGENT', 'analytics:read'],
    ['CX_AGENT', 'users:manage'],
    ['SUPERVISOR', 'users:manage'],
    ['SUPERVISOR', 'config:manage'],
    ['USER', 'calls:read'],
  ];

  for (const [role, permission] of critical) {
    it(`${role} must never ${permission}`, () => {
      expect(can(role, permission)).toBe(false);
    });
  }
});

describe('the table itself', () => {
  it('grants nothing that is not a declared permission', () => {
    // A typo in the table would otherwise sit there granting a permission that
    // no `requires()` call will ever ask about — invisible until someone adds
    // an endpoint with that name.
    for (const role of ROLES) {
      for (const granted of ROLE_PERMISSIONS[role]) {
        expect(PERMISSIONS).toContain(granted);
      }
    }
  });

  it('refuses an unknown role rather than throwing', () => {
    expect(can('ROOT' as Role, 'voice:use')).toBe(false);
  });

  it('refuses an unknown permission', () => {
    expect(can('ADMIN', 'database:drop' as Permission)).toBe(false);
  });
});
