import { expect, it } from 'vitest';
import { redactLocalLogTokens } from './localLogRedaction';

it('redacts invitation/reset credentials from proxy errors while keeping useful diagnostics', () => {
  const log=redactLocalLogTokens('proxy error /login_api/invitation/?token=SYNTHETIC_ONLY&x=1 ECONNREFUSED');
  expect(log).not.toContain('SYNTHETIC_ONLY');
  expect(log).toContain('token=[REDACTED]&x=1 ECONNREFUSED');
});
