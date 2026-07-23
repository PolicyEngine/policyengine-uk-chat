const { basePath } = require("./app-paths.json");

/**
 * The chat owns this path in every environment. Standalone deployments serve
 * it directly, and policyengine.org can preserve the same path through a
 * multizone rewrite without special handling for pages, assets, or APIs.
 */
/** @type {import('next').NextConfig} */
module.exports = {
  output: "standalone",
  basePath,
};
