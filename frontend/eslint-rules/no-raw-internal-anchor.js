/**
 * Forbid `<a href="/in-app/route">` — use react-router's `<Link to>` instead.
 *
 * A raw anchor to an in-app route is a full browser navigation: it tears the SPA
 * down and boots it again. Every lazy route chunk has to be refetched, so the
 * router's Suspense fallback is shown on a click that should have been instant,
 * and all in-memory state is lost. Fifty-two of these had accumulated across the
 * workspace dashboards — the "it loads another page for a second" report — which
 * is why this is a lint rule and not just a fixed batch.
 *
 * Example (bad):
 *   <a href="/admin/roles">Manage roles</a>
 *   <a href={`/users/${id}`}>Open</a>
 *
 * Example (good):
 *   <Link to="/admin/roles">Manage roles</Link>
 *   <Link to={`/users/${id}`}>Open</Link>
 *
 * Anchors to things that are NOT routes stay anchors — a Django endpoint that
 * streams a file, an absolute URL, a mailto:, a #fragment. `apiPrefixes` lists
 * the server prefixes served outside the SPA; keep it in step with the proxy
 * table in vite.config.ts.
 */
const DEFAULT_API_PREFIXES = [
  '/quiz_api/',
  '/learner_api/',
  '/coach_api/',
  '/curriculum_api/',
  '/enrolment_api/',
  '/audit_api/',
  '/hours_test_api/',
  '/engagement_api/',
  '/login_api/',
  '/manual_audit_api/',
  '/api/',
  '/static/',
  '/media/',
]

export default {
  rules: {
    'no-raw-internal-anchor': {
      meta: {
        type: 'problem',
        docs: {
          description:
            'Use <Link to> for in-app routes; a raw <a href="/..."> reloads the whole SPA.',
        },
        schema: [
          {
            type: 'object',
            properties: {
              apiPrefixes: { type: 'array', items: { type: 'string' } },
            },
            additionalProperties: false,
          },
        ],
        messages: {
          useLink:
            'Use <Link to={…}> instead of <a href="{{target}}">. A raw anchor to an in-app route reloads the entire SPA and re-fetches every route chunk. If this is a file download or a non-SPA endpoint, add its prefix to the rule\'s apiPrefixes option.',
        },
      },
      create(context) {
        const options = context.options[0] || {}
        const apiPrefixes = options.apiPrefixes || DEFAULT_API_PREFIXES

        /** The literal path an href points at, or null when it cannot be read statically. */
        function staticTarget(value) {
          if (!value) return null
          // href="/x"
          if (value.type === 'Literal') {
            return typeof value.value === 'string' ? value.value : null
          }
          if (value.type !== 'JSXExpressionContainer') return null
          const expression = value.expression
          // href={"/x"}
          if (expression.type === 'Literal') {
            return typeof expression.value === 'string' ? expression.value : null
          }
          // href={`/x/${id}`} — the leading quasi is enough to classify it.
          if (expression.type === 'TemplateLiteral') {
            return expression.quasis[0]?.value?.cooked ?? null
          }
          return null
        }

        return {
          JSXOpeningElement(node) {
            if (node.name?.type !== 'JSXIdentifier' || node.name.name !== 'a') return

            const href = node.attributes.find(
              attribute =>
                attribute.type === 'JSXAttribute' &&
                attribute.name?.type === 'JSXIdentifier' &&
                attribute.name.name === 'href',
            )
            if (!href) return

            const target = staticTarget(href.value)
            // Not statically readable (href={someVar}) — could be anything, and
            // guessing would produce false positives on genuinely external URLs.
            if (target === null) return
            // Only same-origin absolute paths are routes. "//host" is
            // protocol-relative and leaves the origin.
            if (!target.startsWith('/') || target.startsWith('//')) return
            if (apiPrefixes.some(prefix => target.startsWith(prefix))) return

            context.report({ node: href, messageId: 'useLink', data: { target } })
          },
        }
      },
    },
  },
}
