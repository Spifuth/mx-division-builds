import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // node, not jsdom: the units under test are pure math. Adding jsdom
    // would pull a DOM in for no reason -- add it when a component test
    // actually needs one.
    //
    // When that day comes, jsdom is NOT all you will need. Measured: because
    // this file exists, Vitest loads it INSTEAD of vite.config.ts and does not
    // inherit its `plugins` array, so importing a .vue SFC from a spec fails
    // with "Install @vitejs/plugin-vue to handle .vue files". The fix is to
    // merge the two configs (`mergeConfig` from 'vite') rather than to add the
    // plugin twice. Not done here because nothing imports a .vue file yet.
    environment: 'node',
    include: ['src/**/*.spec.js'],
  },
})
