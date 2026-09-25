/**
 * The archive's search, described for browsers (OpenSearch 1.1).
 *
 * `/games?q=` is a stable, public search URL, and this is the standard way to say so: a browser
 * that has seen the `<link rel="search">` on `/games` offers "search Chessmark" from its address
 * bar, landing on the same canonical results page the form would. Nothing here is new behaviour —
 * it names the URL the page already answers.
 *
 * Prerendered at build: a `GET` that reads nothing from the request is static by default.
 */

import { siteName, siteUrl } from "@/lib/site";

function escape(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function GET() {
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <ShortName>${escape(siteName)}</ShortName>
  <Description>Search every game ${escape(siteName)} has played, by model or player.</Description>
  <InputEncoding>UTF-8</InputEncoding>
  <Image width="16" height="16" type="image/x-icon">${escape(siteUrl)}/favicon.ico</Image>
  <Url type="text/html" method="get" template="${escape(siteUrl)}/games?q={searchTerms}"/>
  <moz:SearchForm xmlns:moz="http://www.mozilla.org/2006/browser/search/">${escape(siteUrl)}/games</moz:SearchForm>
</OpenSearchDescription>
`;
  return new Response(xml, {
    headers: { "content-type": "application/opensearchdescription+xml; charset=utf-8" },
  });
}
