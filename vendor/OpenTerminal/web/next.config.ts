import type { NextConfig } from "next";

// OpenTerminal is served behind the SAS PRO reverse proxy at /terminal.
// basePath makes Next.js generate and resolve its assets/routes under that path.
// trailingSlash keeps /terminal/ as the canonical URL and avoids a reverse-proxy redirect loop.
const nextConfig: NextConfig = {
  output: "standalone",
  basePath: "/terminal",
  trailingSlash: true,
};

export default nextConfig;
