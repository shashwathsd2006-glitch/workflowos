# WorkFlowOS Frontend

Next.js + TypeScript + Tailwind CSS dashboard shell for WorkFlowOS.

```bash
npm install
npm run dev     # http://localhost:3000
npm run build   # production build check
npm run lint
```

Backend URL comes from `NEXT_PUBLIC_API_BASE_URL` (default `http://127.0.0.1:8000`),
see `.env.example`. The API client lives in `src/lib/api.ts`; response types in
`src/types/api.ts`.

The Activity page and the Overview activity statistics consume the real
backend API (`/api/activity…`); the remaining pages stay placeholders until
their phase lands (see `../docs/phases.md`).
