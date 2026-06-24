# Frontend

The frontend (`frontend/`) is a [Next.js 15](https://nextjs.org) app (React 19,
App Router) that provides the chat UI, renders charts, handles authentication,
and supports conversation sharing. It runs on port 3006 locally.

## Stack

| Concern | Library |
| --- | --- |
| Framework | Next.js 15 (App Router), React 19 |
| UI components | Mantine (`@mantine/core`), Tabler icons |
| Markdown | `react-markdown` + `remark-gfm`, `react-syntax-highlighter` |
| Charts | D3 (`d3`) with custom chart components |
| Auth & data | `@supabase/supabase-js` |

## Key files

```text
frontend/src/
├── app/
│   ├── layout.tsx              Root layout
│   ├── page.tsx                Entry page
│   ├── ChatPage.tsx            Main chat interface
│   ├── Providers.tsx           Mantine + auth context providers
│   ├── s/[token]/page.tsx      Public shared-conversation view
│   └── api/proxy/[...slug]/    Server-side proxy to the backend
├── components/
│   ├── theme.ts                Mantine theme
│   └── charts/                 Chart renderers (Bar, Line, Scatter, Tooltip)
└── utils/
    ├── AuthContext.tsx         Supabase auth state
    ├── supabase.ts             Supabase client
    └── backend.ts              Backend API client
```

## The backend proxy

In production the browser does not call the backend directly. The Next.js route
handler at `app/api/proxy/[...slug]/route.ts` forwards requests to the backend
(`BACKEND_URL`). This keeps the backend origin and the streaming SSE connection
behind the frontend's domain, and is how multizone embedding (prefixed asset
URLs) works.

## Charts

The agent's `generate_chart` tool emits a fenced ` ```chart ` JSON block inside
the assistant message. The frontend parses that block and renders it with the D3
components in `components/charts/` — `BarChart`, `LineChart`, `ScatterChart`,
with a shared `Tooltip` and `useInView` for lazy rendering. Chart `*_format`
hints (currency, percent, year, …) drive axis and tooltip formatting.

## Auth and sharing

`AuthContext` wraps the app in Supabase auth state. Signed-in users get saved
conversation history and credit tracking. Any conversation can be shared via a
public token; the read-only view lives at `/s/[token]` and is backed by the
`GET /conversations/shared/{share_token}` endpoint.

## Configuration

The frontend reads:

- `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` — client auth.
- `BACKEND_URL` — where the proxy forwards (set to the backend container in
  Docker, or the Modal URL in production).
