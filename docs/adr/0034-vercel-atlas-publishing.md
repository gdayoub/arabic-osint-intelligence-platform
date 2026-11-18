# 0034: Publish the atlas frontend on Vercel

George requested a publicly accessible Vercel deployment of the new interface.
The pipeline and current reporting already run on Cloudflare. Rather than
copying an aging preview snapshot or introducing another backend, deploy
`src/api/static` as a static Vercel project. Its vercel.json rewrites data.json
and countries paths to the existing public Cloudflare reporting endpoint.
The root and index.html resolve to dashboard.html.

For a Git-linked Vercel project, select `src/api/static` as Root Directory,
Other as framework, and leave install/build commands empty. The connected
reporting feed continues following the existing scheduled pipeline. Once the
atlas baker is deployed there, the Vercel frontend automatically reads the
complete archive; until then it explicitly labels legacy data as a sample.

This publishes only frontend assets and public reporting. It requires no
database credentials on Vercel. Availability depends on both Vercel and the
Cloudflare public feed. The frozen example remains a separate, explicit choice.
