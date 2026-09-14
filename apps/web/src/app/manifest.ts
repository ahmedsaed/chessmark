import type { MetadataRoute } from "next";

import { siteDescription, siteName, siteTagline } from "@/lib/site";

/**
 * The web app manifest.
 *
 * Not here to make Chessmark installable — it is a spectator site, not an app. It is here because
 * without a manifest the browser names a bookmark after the `<title>` (wordmark, em dash, tagline)
 * and paints its own chrome white above a page whose ground is `#16130e`.
 *
 * `theme_color` is the ground rather than the accent: it fills the address bar and the task
 * switcher card, which should read as an extension of the page, not as a band of amber above it.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: `${siteName} — ${siteTagline}`,
    short_name: siteName,
    description: siteDescription,
    start_url: "/",
    display: "browser",
    background_color: "#16130e",
    theme_color: "#16130e",
    icons: [
      /* `purpose: "any"` on the SVG and a raster beside it: a manifest with only an SVG icon is
         ignored outright by Android, which wants a bitmap it can size. */
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
      { src: "/apple-icon.png", sizes: "180x180", type: "image/png" },
    ],
  };
}
