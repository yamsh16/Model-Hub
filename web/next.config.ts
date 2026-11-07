import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // a self-contained server for the Docker image (web/Dockerfile)
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
