# Review Radar web

Next.js 16 (App Router) front end for Review Radar: the approval queue, themes board, reviews, runs and the
trajectory ("flight recorder") viewer. The API lives in the repo root (FastAPI); see `../docs/spec.md`.

```sh
pnpm install
NEXT_PUBLIC_API_MOCK=1 pnpm dev        # fixtures, no API needed (any operator token works)
NEXT_PUBLIC_API_URL=http://localhost:7860 pnpm dev
```

Checks: `pnpm exec next typegen && pnpm exec tsc --noEmit && pnpm lint && pnpm test && pnpm build`.

- `lib/api.ts`: the zod contract (tolerant parsing) and every API call; `lib/mock.ts`: mock mode.
- Writes send `Authorization: Bearer <operator token>`; the token is kept in localStorage (Operator drawer).
- `app/api/health`: web health plus API reachability (503 when the API is unreachable).
