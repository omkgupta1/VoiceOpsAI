/**
 * Login and identity.
 */
import bcrypt from 'bcryptjs';
import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { queryOne } from '../lib/db.js';
import { requires, type TokenPayload } from '../auth/plugin.js';
import { ROLE_PERMISSIONS, type Role } from '../auth/rbac.js';

const LoginBody = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

interface UserRow {
  id: string;
  email: string;
  password_hash: string;
  full_name: string;
  role: Role;
  is_active: boolean;
}

export async function authRoutes(app: FastifyInstance): Promise<void> {
  app.post('/api/auth/login', async (request, reply) => {
    const parsed = LoginBody.safeParse(request.body);
    if (!parsed.success) {
      return reply.code(400).send({
        error: { code: 'INVALID_BODY', message: 'email and password are required', retryable: false },
      });
    }

    const user = await queryOne<UserRow>(
      'SELECT id, email, password_hash, full_name, role, is_active FROM users WHERE lower(email) = lower($1)',
      [parsed.data.email],
    );

    // One message and one code for "no such user", "wrong password" and
    // "deactivated". Distinguishing them tells an attacker which emails are real.
    const ok = user?.is_active && (await bcrypt.compare(parsed.data.password, user.password_hash));
    if (!ok || !user) {
      return reply.code(401).send({
        error: { code: 'INVALID_CREDENTIALS', message: 'Email or password is incorrect', retryable: false },
      });
    }

    await queryOne('UPDATE users SET last_login_at = now() WHERE id = $1 RETURNING id', [user.id]);

    const payload: TokenPayload = {
      sub: user.id,
      email: user.email,
      role: user.role,
      name: user.full_name,
    };

    return {
      token: app.jwt.sign(payload),
      user: { ...payload, permissions: ROLE_PERMISSIONS[user.role] },
    };
  });

  app.get('/api/auth/me', { preHandler: requires('voice:use') }, async (request) => {
    const user = request.user as TokenPayload;
    return { user: { ...user, permissions: ROLE_PERMISSIONS[user.role] } };
  });
}
