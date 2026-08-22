import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue2'

export default ({ mode }) => {

  // grab the environment so we can set base to the BASE_URL value from the env file
  const env = loadEnv(mode, process.cwd(), '');

  return defineConfig({
    plugins: [vue()],
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
      port: Number(env.DEV_PORT) || 8090,
      // Never silently fall through to another port; the publish is fixed.
      strictPort: true,
    },
  });
}