import { defineConfig } from "vitest/config";

/**
 * Tests run in plain Node: they cover the pure logic the UI depends on --
 * footprint geometry and hit testing, trajectory interpolation, the camera
 * transform, and the event-log fold. None of that needs a DOM, so the suite
 * stays fast and the canvas stays out of the way.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
});
