import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // node, not jsdom: the units under test are pure math. Adding jsdom
    // would pull a DOM in for no reason -- add it when a component test
    // actually needs one.
    environment: 'node',
    include: ['src/**/*.spec.js'],
  },
})
