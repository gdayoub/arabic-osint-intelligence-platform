# Atlas assets

`globe.gl.min.js` is Globe.GL 2.46.2, downloaded from
https://unpkg.com/globe.gl@2.46.2/dist/globe.gl.min.js on 2026-09-07.
MIT license: `globe-LICENSE`. The bundled renderer is approximately 1.89 MB
uncompressed. It is loaded only when the interactive globe starts.

`countries.geojson` derives from Natural Earth's public-domain 1:110m
administrative country boundaries:
https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_0_countries.geojson
Downloaded 2026-09-07; retained geometry and ADMIN, ADM0_A3, LABEL_Y,
LABEL_X as name, iso, lat, lng. These are generalized visualization boundaries,
not precise territorial or operational maps. Gaza and Bahrain have explicitly
approximate navigation markers because the coarse geometry is insufficient.

`demo.json` is a frozen, deliberately limited public reporting example from
the existing dashboard snapshot dated 2026-09-07. Headlines retain their
publishers and original URLs. It is marked incomplete and contains no full
article bodies. It is never substituted silently for current reporting.

The interface uses Google Fonts with system-font fallbacks. All application
JavaScript, map data, and example reporting are served from this deployment.
