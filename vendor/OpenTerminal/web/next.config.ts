import type { NextConfig } from "next";

// OpenTerminal is served behind the SAS PRO reverse proxy at /terminal.
// basePath makes Next.js generate and resolve its assets/routes under that path.
const nextConfig: NextConfig = {
  output: "standalone",
  basePath: "/terminal",
};

export default nextConfig;
