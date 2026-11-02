# Model Hub web app (Next.js)

The new front end, built over Days 22-24 of the roadmap to replace the original static HTML/JS site (retired on Day 24).
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

## Pages

| Route      | What it is |
|------------|------------|
| `/`        | Landing: the pitch, "how it works", backend status |
| `/connect` | Inputs: attach an endpoint, upload a test set + pick the target + leakage check + evaluate, load a training log |
| `/report`  | Readiness score, accuracy, training progress / still needed, loss curve, evaluation tables, advice |
| `/chat`    | Streamed chat with an attached model; history kept per model in the browser |

Pages are server-rendered shells; the parts that talk to the backend are client components.

## How the pieces fit

```
src/app/                 routes and layout (page.tsx per route, layout.tsx wraps everything in the providers)
src/components/
  AuthProvider.tsx       who is signed in (context); sign in / register / sign out; restores a saved token
  AuthControls.tsx       header buttons + the sign-in dialog
  WorkspaceProvider.tsx  the test set, evaluation and training log the user has loaded (context,
                         kept in sessionStorage so a reload keeps it; emptied when the account changes)
  connect/               the three input panels on /connect
  report/                the /report view and its LossChart (inline SVG)
  chat/                  the /chat view
  ui.tsx                 Button, Field, Status, Card, Panel, DataTable
src/lib/
  api.ts                 backend URL, bearer-token storage, apiFetch / apiJson
  types.ts               shapes of the backend's JSON
  chart.ts               loss-chart geometry (log -> SVG coordinates)
  sse.ts                 reads the chat's server-sent-events stream
  report.ts              wording for the "training still needed" and "progress" cards
  format.ts              pct / fmt / errorMessage
  storage.ts             what is wiped from the browser when the account changes
  useEndpoints.ts        the signed-in user's attached endpoints
```

The server computes every number on the report (`/readiness`); the browser only displays them.
