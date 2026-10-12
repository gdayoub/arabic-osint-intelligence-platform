import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// Loading the checked-in worker as an ES module needs no package.json change.
const source = await readFile(new URL("../worker.js", import.meta.url), "utf8");
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

function environment() {
  return {
    ASSETS: {
      async fetch() {
        return new Response('{"metadata":true}', {
          status: 200,
          headers: { "Content-Type": "application/json", ETag: "fixture" },
        });
      },
    },
  };
}

for (const path of ["/data.json", "/countries/_atlas.json", "/countries/_atlas.json?version=1"]) {
  test(`allows public JSON CORS for ${path}`, async () => {
    const response = await worker.fetch(new Request(`https://example.com${path}`), environment());
    assert.equal(response.headers.get("Access-Control-Allow-Origin"), "*");
    assert.equal(response.headers.get("Content-Type"), "application/json");
    assert.equal(response.headers.get("ETag"), "fixture");
    assert.equal(response.status, 200);
    assert.equal(await response.text(), '{"metadata":true}');
  });
}

for (const path of ["/", "/index.html", "/private.json", "/countries/private.json", "/countries/_atlas.json/extra", "/countries/_atlas.json.bak"]) {
  test(`does not broaden CORS to ${path}`, async () => {
    const response = await worker.fetch(new Request(`https://example.com${path}`), environment());
    assert.equal(response.headers.get("Access-Control-Allow-Origin"), null);
  });
}
