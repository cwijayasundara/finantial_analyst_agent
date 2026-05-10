import type { NextConfig } from "next";

const API_HOST = process.env.PFH_API_HOST ?? "127.0.0.1";
const API_PORT = process.env.PFH_API_PORT ?? "8000";
const API_BASE = `http://${API_HOST}:${API_PORT}`;

const config: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_BASE}/api/:path*` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value:
              "default-src 'self'; " +
              "script-src 'self' 'unsafe-inline' 'unsafe-eval'; " +
              "style-src 'self' 'unsafe-inline'; " +
              "img-src 'self' data:; " +
              `connect-src 'self' ${API_BASE}; ` +
              "frame-ancestors 'none';",
          },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default config;
