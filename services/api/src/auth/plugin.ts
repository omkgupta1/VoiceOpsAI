/**
 * JWT authentication and the `requires()` guard.
 */
import type { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import fastifyJwt from '@fastify/jwt';
import { config } from '../config.js';
import { can, type Permission, type Role } from './rbac.js';

export interface TokenPayload {
  sub: string;
  email: string;
  role: Role;
  name: string;
}

// @fastify/jwt already augments FastifyRequest with `user`; declaring it again
// here conflicts with that. Only the plugin's own module is augmented.
declare module '@fastify/jwt' {
  interface FastifyJWT {
    payload: TokenPayload;
    user: TokenPayload;
  }
}

export async function registerAuth(app: FastifyInstance): Promise<void> {
  await app.register(fastifyJwt, {
    secret: config.jwtSecret,
    sign: { expiresIn: config.jwtExpiresIn },
  });

  if (config.env !== 'local' && config.jwtSecret === 'dev-only-change-me') {
    // Refuse to start rather than run in production signing tokens anyone can forge.
    throw new Error('JWT_SECRET is still the development default — refusing to start');
  }
}

/**
 * Guard a route on a single permission.
 *
 *   app.get('/api/jobs', { preHandler: requires('jobs:read') }, handler)
 *
 * 401 when the token is missing or invalid, 403 when it is valid but the role
 * does not carry the permission. Keeping those distinct matters: one means
 * "log in", the other means "you cannot do this", and collapsing them into one
 * status makes both harder to debug.
 */
export function requires(permission: Permission) {
  return async function guard(request: FastifyRequest, reply: FastifyReply): Promise<void> {
    try {
      await request.jwtVerify();
    } catch {
      return reply.code(401).send({
        error: { code: 'UNAUTHENTICATED', message: 'A valid token is required', retryable: false },
      });
    }

    const user: TokenPayload | undefined = request.user;
    if (!user || !can(user.role, permission)) {
      return reply.code(403).send({
        error: {
          code: 'FORBIDDEN',
          message: `Role ${user?.role ?? 'unknown'} cannot ${permission}`,
          retryable: false,
        },
      });
    }
  };
}
