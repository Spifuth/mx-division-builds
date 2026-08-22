import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue2'
import { viteCommonjs } from '@originjs/vite-plugin-commonjs'

export default ({ mode }) => {

  // grab the environment so we can set base to the BASE_URL value from the env file
  const env = loadEnv(mode, process.cwd(), '');

  return defineConfig({
    plugins: [
      // PrimeVue 2 is CommonJS, and its entry points re-export a .vue file:
      // `primevue/panel/index.js` is literally `module.exports =
      // require('./Panel.vue')`. esbuild cannot bundle a .vue file, so Vite's
      // dependency optimiser externalises it and the browser fetches Panel.vue
      // raw -- at which point its own relative imports (`../ripple/Ripple`,
      // `../utils/*`) land on un-prebundled CommonJS that the browser loads as
      // ESM and finds no `default` export on. SyntaxError before Vue mounts:
      // a blank page while every server-side check stays green.
      //
      // Only the DEV server was affected. `npm run build-prod` was always fine
      // because Rollup's own commonjs plugin handles it -- which is exactly why
      // nothing caught this until someone opened a browser. `npm run
      // check:render` is now that someone.
      viteCommonjs(),
      vue(),
    ],
    base: env.BASE_URL,
    server: {
      // Bind every interface *inside the container*: the published port in
      // docker-compose.dev.yml is what restricts reachability to the tailnet,
      // not this. Vite's own default is localhost, which would leave the
      // published port mapping to nothing. The fallback is for running vite
      // outside the container, where localhost is the right answer.
      //
      // loadEnv() with an empty prefix also reads process.env, which is how
      // compose's `environment:` DEV_HOST/DEV_PORT reach this file.
      host: env.DEV_HOST || 'localhost',
      // 8090 here is the third of three hardcoded copies of this port --
      // also DEV_PORT and the `ports:` publish in docker-compose.dev.yml.
      // This fallback only bites when DEV_PORT is unset (e.g. running vite
      // outside the container). Change it to match if the other two ever
      // change, or an outside-the-container run silently disagrees with the
      // containerised one.
      port: Number(env.DEV_PORT) || 8090,
      // Never silently fall through to another port; the publish is fixed.
      strictPort: true,
      // Vite 5.4 added a Host-header allowlist (the DNS-rebinding half of the
      // CVE-2025-31486 fixes) that accepts only localhost and bare IPs. That
      // is a real protection and stays on -- but it means browsing this dev
      // server by its Tailscale MagicDNS name returns "Blocked request" on an
      // otherwise empty page, which reads as "the app is broken".
      //
      // A leading dot allows subdomains. `.ts.net` is Tailscale's own public
      // domain, not an internal identifier, so naming it here leaks nothing
      // into a public repo -- and it widens nothing in practice: the published
      // port is already bound to the tailnet address alone, so the only hosts
      // that can arrive here are tailnet hosts. `allowedHosts: true` would
      // have disabled the guard outright; this keeps it.
      allowedHosts: ['.ts.net'],
    },
  });
}