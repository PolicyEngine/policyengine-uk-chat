# Frontend

The `frontend/` directory is a [Next.js 15](https://nextjs.org/) app (React 19,
App Router) that provides the chat UI, chart rendering, Supabase auth, and
conversation sharing. It is deployed on Vercel; locally it runs on port 3006.

## The stack

```{list-table}
:header-rows: 1

* - Concern
  - Library
* - Framework
  - Next.js `^15.0.0` (App Router), React `^19.0.0`
* - UI components
  - Mantine (`@mantine/core ^8.3.8`), Tabler icons (`@tabler/icons-react ^3.0.0`)
* - Markdown
  - `react-markdown ^10` + `remark-gfm ^4`, `react-syntax-highlighter ^16`
* - Charts
  - D3 (`d3 ^7.9.0`) with custom chart components
* - Auth & data
  - `@supabase/supabase-js ^2.101.1`
* - Language
  - TypeScript `^5`
```

## Key files

Everything lives under `frontend/src/`:

```text
src/
├── app/
│   ├── layout.tsx                  Root layout (CSS custom-property theme vars)
│   ├── page.tsx                    Entry; dynamically imports ChatPage (ssr: false)
│   ├── ChatPage.tsx                Main chat UI (streaming, history, attach, slash commands)
│   ├── Providers.tsx               MantineProvider + AuthProvider
│   ├── s/[token]/page.tsx          Public read-only shared-conversation view
│   └── api/proxy/[...slug]/route.ts  Server-side proxy to the backend
├── components/
│   ├── charts/                     Chart renderers + extractChartSpecs + types
│   └── theme.ts                    Theme mapping
└── utils/
    ├── AuthContext.tsx             Supabase auth state (useAuth)
    ├── supabase.ts                 Supabase client
    └── backend.ts                  Backend API client
```

- **`app/ChatPage.tsx`** is the main chat UI: streaming responses, a history
  sidebar, image attachment, and slash commands (`/charts`, `/new`, `/clear`,
  `/help`).
- **`components/charts/`** holds the chart renderers (`LineChart`, `BarChart`,
  `ScatterChart`, `Tooltip`) plus `index.tsx` (`extractChartSpecs`) and
  `types.ts`.
- **`utils/AuthContext.tsx`** exposes Supabase auth state through `useAuth`
  (`user`, `session`, `signUp`, `signIn`, `signOut`).
- **`utils/supabase.ts`** builds the Supabase client from
  `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
- **`utils/backend.ts`** is the backend API client (`getBackendBase` /
  `getBackendEndpoint`).

## The backend proxy

In production the browser does **not** call the backend directly. The Next.js
route handler at `app/api/proxy/[...slug]/route.ts` forwards all methods
(GET / POST / PUT / PATCH / DELETE) to the backend.

It resolves the backend URL from the `BACKEND_URL` env var, falling back to a
per-branch Modal preview URL when `VERCEL_ENV=preview` (derived from
`VERCEL_GIT_COMMIT_REF`), else `http://localhost:8080`. When a response carries
`content-type: text/event-stream`, the handler pipes the SSE stream through a
`TransformStream` so streaming is not buffered.

Client code (`utils/backend.ts`) uses `NEXT_PUBLIC_BACKEND_URL` or defaults to
`/api/proxy`, so the [chat agent](backend/chat.md) and [tools](backend/tools.md)
are reached through the same proxy path in every environment.

## Multizone / asset prefix

```{note}
`frontend/next.config.js` sets `output: "standalone"` and, in production only,
`assetPrefix: "/_zones/uk-chat"` so the app can be embedded under
policyengine.org. `frontend/vercel.json` rewrites
`/_zones/uk-chat/_next/:path*` → `/_next/:path*` on the chat host so the
prefixed asset URLs resolve.
```

## Charts

The agent's [`generate_chart`](backend/tools.md) tool emits a fenced ` ```chart `
JSON block inside the assistant message. `extractChartSpecs(content)`
(`components/charts/index.tsx`) parses those blocks (a regex over ` ```chart `
fences), validates the type against `line` / `bar` / `area` / `scatter`, and
replaces each with a placeholder rendered by the `Chart` component:

```{list-table}
:header-rows: 1

* - Spec type
  - Renderer
* - `line`
  - `LineChart`
* - `bar`
  - `BarChart`
* - `area`
  - `LineChart` with `areaFill`
* - `scatter`
  - `ScatterChart`
```

While streaming, it also shows a loading placeholder for an incomplete trailing
chart block. Chart `*_format` hints drive axis and tooltip formatting. The same
renderer is used in the main chat and the shared view.

## Auth and sharing

`AuthContext` wraps the app in Supabase auth state; signed-in users get saved
history and credit tracking. Any conversation can be shared via a public token;
the read-only view lives at `/s/[token]`, backed by the backend
`GET /conversations/shared/{share_token}` endpoint. See [the API](backend/api.md).

## Configuration

```{list-table}
:header-rows: 1

* - Variable
  - Purpose
* - `NEXT_PUBLIC_SUPABASE_URL`
  - Supabase project URL (client auth)
* - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
  - Supabase anon key (client auth)
* - `NEXT_PUBLIC_BACKEND_URL`
  - Optional client-side backend override
* - `BACKEND_URL`
  - Server-side backend URL used by the proxy route
* - `VERCEL_ENV` / `VERCEL_GIT_COMMIT_REF`
  - Set automatically; drive preview backend routing
```
