import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /**
   * Traces the files each route actually needs and copies them, with a minimal `server.js`, into
   * `.next/standalone`. The container then runs without `node_modules` at all — which is the
   * difference between shipping the built app and shipping the whole toolchain that built it.
   */
  output: "standalone",
};

export default nextConfig;
