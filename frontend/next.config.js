/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  env: {
    // Surface the git ref to the client so the preview can derive the matching
    // Modal backend URL — Vercel's preview *hostname* gets truncated for long
    // branch names, so we can't reliably parse it from window.location.
    NEXT_PUBLIC_VERCEL_GIT_COMMIT_REF: process.env.VERCEL_GIT_COMMIT_REF,
    NEXT_PUBLIC_VERCEL_ENV: process.env.VERCEL_ENV,
  },
};

module.exports = nextConfig;
