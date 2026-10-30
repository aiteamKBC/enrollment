import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import AutoImport from "unplugin-auto-import/vite";
// import { readdyJsxRuntimeProxyPlugin } from "./vite.jsx-runtime-proxy";

const base = process.env.BASE_PATH || "/";
const isPreview = process.env.IS_PREVIEW ? true : false;
//const proxyPlugins = isPreview ? [readdyJsxRuntimeProxyPlugin()] : [];
// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Read the local backend address before configuring the dev proxy. All API
  // prefixes belong to the same Django application and must follow this target.
  const env = loadEnv(mode, __dirname, "VITE_API_");
  const apiTarget = env.VITE_API_TARGET || env.VITE_API_PROXY || "http://127.0.0.1:8001";
  return {
  define: {
    __BASE_PATH__: JSON.stringify(base),
    __IS_PREVIEW__: JSON.stringify(isPreview),
    __READDY_PROJECT_ID__: JSON.stringify(process.env.PROJECT_ID || ""),
    __READDY_VERSION_ID__: JSON.stringify(process.env.VERSION_ID || ""),
    __READDY_AI_DOMAIN__: JSON.stringify(process.env.READDY_AI_DOMAIN || ""),
  },
  plugins: [
    // ...proxyPlugins,
    react(),
    AutoImport({
      include: [/[\\/]src[\\/].*\.[jt]sx?$/],
      exclude: [/[\\/]src[\\/].*(?:__tests__|\.test\.|\.spec\.|mocks)[\\/].*/],
      imports: [
        {
          react: [
            ["default", "React"],
            "useState",
            "useEffect",
            "useContext",
            "useReducer",
            "useCallback",
            "useMemo",
            "useRef",
            "useImperativeHandle",
            "useLayoutEffect",
            "useDebugValue",
            "useDeferredValue",
            "useId",
            "useInsertionEffect",
            "useSyncExternalStore",
            "useTransition",
            "startTransition",
            "lazy",
            "memo",
            "forwardRef",
            "createContext",
            "createElement",
            "cloneElement",
            "isValidElement",
          ],
        },
        {
          "react-router-dom": [
            "useNavigate",
            "useLocation",
            "useParams",
            "useSearchParams",
            "Link",
            "NavLink",
            "Navigate",
            "Outlet",
          ],
        },
        // React i18n
        {
          "react-i18next": ["useTranslation", "Trans"],
        },
        {
          "@/components/feature/AppIcon": ["AppIcon"],
        },
      ],
      // TypeScript already consumes the checked-in declaration file during
      // production builds; regenerating it scans the whole source tree and is
      // the main avoidable cost in the deploy build.
      dts: mode !== 'production',
    }),
  ],
  base,
  optimizeDeps: {
    // Shared by Users, onboarding and audit signatures. Prepare it at startup
    // so opening a lazy page does not first discover/rebundle this dependency.
    include: ['signature_pad'],
  },
  build: {
    // Production ships no source maps. The bundle previously emitted 297 .map files
    // (~22 MB, 71% of the build output) reachable from the deployed site, which hands
    // any visitor the original TypeScript — component logic, code comments, internal
    // file paths and API route names included. No error-monitoring service is wired
    // up (there is no Sentry/Bugsnag/Rollbar/Datadog dependency), so nothing consumes
    // them; if one is added later, switch this to 'hidden' so the maps are emitted for
    // upload but no sourceMappingURL comment points browsers at them.
    //
    // `vite dev` is unaffected: the dev server always serves inline source maps.
    sourcemap: mode === 'production' ? false : true,
    outDir: 'out',
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "./src"),
    },
  },
    server: {
    port: 3001,
    host: "0.0.0.0",
    // Forward API calls to the Django backend so the browser sees them as
    // same-origin (no CORS). Override :8000 with VITE_API_TARGET when another
    // checkout already uses it, for example in .env.development.local.
    proxy: {
      "/curriculum_api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/coach_api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/quiz_api": {
        target: apiTarget,
        changeOrigin: true,
      },
      // 127.0.0.1, NOT localhost: on Windows, Node resolves localhost to ::1
      // first and Django listens on IPv4 only — the failed IPv6 attempt costs
      // ~2.3s on EVERY proxied request.
      "/learner_api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/audit_api": {
        target: apiTarget,
        changeOrigin: true,
      },
      // HOURS-TEST workspace: same Django audit views, cloned database.
      "/hours_test_api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/manual_audit_api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/engagement_api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/enrolment_api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/progress_reviews_api": {
        target: apiTarget,
        changeOrigin: true,
      },
      // Authentication. Same-origin through the proxy so the kbc_session
      // cookie (HttpOnly, SameSite=Lax) is set on and sent back to :3000.
      "/login_api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/ws": {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
      "/media": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  };
});
