import type { NextConfig } from "next";

// CORS of the frontend's own responses (pages, static files, fonts, the map worker): the site that may
// load them from another address. The API has its own list, CORS_ORIGINS of the backend.
const CORS_ORIGIN = "https://cson.ru";

const nextConfig: NextConfig = {
  /* config options here */
  devIndicators: false,
  output: 'standalone',
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
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Access-Control-Allow-Origin", value: CORS_ORIGIN },
          { key: "Access-Control-Allow-Methods", value: "GET, HEAD, OPTIONS" },
        ],
      },
    ];
  },
};

export default nextConfig;
