const { PHASE_DEVELOPMENT_SERVER } = require("next/constants");

/**
 * Multizone embedding via assetPrefix.
 *
 * The chat is embedded on policyengine.org/uk/chat as a multizone child
 * (see policyengine-app-v2 PR #1036). Static asset URLs in the generated
 * HTML get the "/_zones/uk-chat" prefix so the website (policyengine.org)
 * can proxy them back from the chat host via a Vercel rewrite:
 *
 *   /uk/chat                         → chat-host/
 *   /_zones/uk-chat/_next/:path*     → chat-host/_zones/uk-chat/_next/:path*
 *
 * On the chat host, `vercel.json` rewrites the prefixed asset URLs back to
 * the unprefixed paths Next.js actually serves from. Pages still live at "/"
 * so the standalone preview URL (policyengine-uk-chat.vercel.app) keeps
 * working. Matches the household-api-docs zone pattern.
 *
 * Dev server runs without the prefix so local development stays simple.
 */
module.exports = (phase) => {
  const isDev = phase === PHASE_DEVELOPMENT_SERVER;
  /** @type {import('next').NextConfig} */
  return {
    output: "standalone",
    assetPrefix: isDev ? undefined : "/_zones/uk-chat",
  };
};
