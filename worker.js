/**
 * Serves dist/ as static assets, adding a CORS header on data.json so the
 * portfolio site (a different origin) can fetch it directly. Cloudflare
 * Pages' _headers-file convention doesn't apply to Workers static assets --
 * this fetch-handler-plus-ASSETS-binding pattern is the supported way to
 * customize responses for a Workers-with-static-assets site.
 *
 * data.json contains only aggregate counts, titles, and URLs -- never
 * document body text (enforced by scripts/bake_dashboard_data.py and
 * tested in tests/unit/test_bake.py) -- so a wildcard origin is fine here.
 */
export default {
  async fetch(request, env) {
    const response = await env.ASSETS.fetch(request);
    const url = new URL(request.url);

    if (url.pathname === "/data.json") {
      const headers = new Headers(response.headers);
      headers.set("Access-Control-Allow-Origin", "*");
      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers,
      });
    }

    return response;
  },
};
