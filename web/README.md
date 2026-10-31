# Model Hub web app (Next.js)

The new front end, replacing the static site in `../frontend` over Days 22-24 of the roadmap.
Next.js (App Router) + TypeScript + Tailwind CSS. Colours and fonts live in
`src/app/globals.css` (`@theme`), carried over from the static site's `styles.css`.

```bash
cd web
npm install
npm run dev        # http://localhost:3000  (needs the backend on :8001, see ../README.md)
npm test           # unit tests (Vitest)
npm run lint && npm run typecheck
npm run build      # production build
```

The backend address comes from `NEXT_PUBLIC_API_BASE` (default `http://localhost:8001`; see
`.env.example`). It is baked in at build time, and the backend must list this app's origin in
its CORS settings (`../backend/main.py`).

Next.js here is a recent major version with changes from older releases: check
`node_modules/next/dist/docs/` before writing framework code (see `AGENTS.md`).

```
src/app/         routes and layout (page.tsx, layout.tsx, globals.css)
src/components/  SiteHeader (static), ApiStatus (shows whether the backend is reachable)
src/lib/api.ts   backend URL, bearer-token storage, apiFetch / apiJson
```
