/** Local dev request URLs may contain single-use invitation/reset credentials. */
export function redactLocalLogTokens(message: string): string {
  return message.replace(/([?&](?:token|access_token|refresh_token)=)[^&\s"<>]+/gi, '$1[REDACTED]');
}
