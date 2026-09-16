/**
 * Proxy to the AI service.
 *
 * The AI service is never exposed publicly. It holds the confirmation gate
 * (ADR 0004), and an internet-reachable turn endpoint would be a way around
 * authentication entirely — anyone who could reach it could cancel bookings.
 * Everything goes through here, where a token is checked first.
 *
 * Audio is forwarded as an opaque body rather than parsed and rebuilt. Decoding
 * a multipart upload only to re-encode it would waste memory on every turn and
 * risk corrupting the audio in the round trip, for no gain: this layer has no
 * business looking inside the recording.
 */
import type { FastifyInstance, FastifyRequest } from 'fastify';
import { config } from '../config.js';
import { requires, type TokenPayload } from '../auth/plugin.js';

const AI = config.aiServiceUrl.replace(/\/$/, '');

async function forward(
  request: FastifyRequest,
  path: string,
  init: { body: string | Buffer; contentType: string },
): Promise<{ status: number; body: unknown }> {
  const user = request.user as TokenPayload;
  try {
    const response = await fetch(`${AI}${path}`, {
      method: 'POST',
      headers: {
        'content-type': init.contentType,
        // Carried through so a turn in the AI service's logs can be tied back to
        // the operator who made it.
        'x-voiceops-user': user.sub,
        'x-voiceops-role': user.role,
      },
      body: init.body,
      signal: AbortSignal.timeout(180_000),
    });
    return { status: response.status, body: await response.json() };
  } catch (error: unknown) {
    // The AI service being down is transient, and saying so lets the caller
    // (and, later, the retry engine) treat it as such.
    return {
      status: 503,
      body: {
        error: {
          code: 'AI_SERVICE_UNAVAILABLE',
          message: `Could not reach the AI service: ${(error as Error).message}`,
          retryable: true,
        },
      },
    };
  }
}

export async function voiceRoutes(app: FastifyInstance): Promise<void> {
  // Keep multipart bodies as raw bytes so they can be passed straight through.
  app.addContentTypeParser(
    'multipart/form-data',
    { parseAs: 'buffer' },
    (_request, body, done) => done(null, body),
  );

  app.post('/api/voice/turn', { preHandler: requires('voice:use') }, async (request, reply) => {
    const { status, body } = await forward(request, '/v1/turn', {
      body: JSON.stringify(request.body ?? {}),
      contentType: 'application/json',
    });
    return reply.code(status).send(body);
  });

  app.post('/api/voice/turn/audio', { preHandler: requires('voice:use') }, async (request, reply) => {
    const contentType = request.headers['content-type'];
    if (!contentType?.startsWith('multipart/form-data')) {
      return reply.code(400).send({
        error: {
          code: 'INVALID_CONTENT_TYPE',
          message: 'Audio turns must be multipart/form-data',
          retryable: false,
        },
      });
    }
    const { status, body } = await forward(request, '/v1/turn/audio', {
      body: request.body as Buffer,
      contentType,
    });
    return reply.code(status).send(body);
  });
}
