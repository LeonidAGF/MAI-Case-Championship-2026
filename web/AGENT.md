# AGENT.md — web

Frontend of Pulse Score: the СКФО map with index colouring, the territory card (waterfall,
pluses and minuses), the profile picker and builder, comparison, the what-if form and the chat
widget. It talks only to the backend API. Read the root [AGENT.md](../AGENT.md) first.
`web/README.md` is a 3-line stub.

## Stack

- Next.js 16.4 (App Router, Turbopack). `next.config.ts` enables `cacheComponents` and
  `partialPrefetching` and routes `*.css` through the `@tailwindcss/turbopack` loader.
- React 19.3, TypeScript 5 (strict, alias `@/*` → `web/*`), Tailwind CSS 4 (`@import "tailwindcss"`
  and `@theme` tokens in `app/globals.css`), zustand 5, maplibre-gl 6.17.
- ESLint 9 flat config: `eslint-config-next` core-web-vitals + typescript.
- No test runner and no Prettier config.

**Next 16 differs from older Next.js versions.** Before using an API you're unsure of, read the
matching guide in `node_modules/next/dist/docs/` (`01-app/`, `03-architecture/`, …) and heed
deprecation warnings.

## Commands

Run from `web/`. Node ≥ 20.9.

| Task | Command |
|---|---|
| Install (keeps the lockfile) | `npm ci` |
| Dev server, http://localhost:3000 | `npm run dev` |
| Production build with type check (about 20 s) | `npm run build` |
| Serve the build | `npm run start` |
| Lint | `npm run lint` |
| Lint only source files | `npx eslint app components lib types` |

State on 2026-11-12:

- `npm run build` passes. All routes (`/`, `/compare`, `/whatif`) prerender as static pages.
  `next build` does not run ESLint.
- On 2026-11-13 `npx eslint app components lib types` reports no errors (warnings only). `npm run
  lint` also lints the minified `public/maplibre/maplibre-gl-worker.mjs` (about 1,100 warnings), which
  is not in `globalIgnores`. Don't add new errors.
- Fonts are bundled from npm (`@fontsource-variable/manrope`, `@fontsource-variable/unbounded`,
  imported in `app/layout.tsx`), so neither the build nor the browser fetches fonts from other servers.
- `next dev` writes an `AGENTS.md` next to `package.json` (Next 16 agent rules); it isn't tracked.

The API base URL is `NEXT_PUBLIC_API_URL` (default in `lib/api.ts`: `https://cson.site`), set in
`web/.env.local`, which is gitignored by the root `.env*` rule. It is inlined at build time, so
rebuild after changing it. With `npm run dev` the backend must list the web origin in
`CORS_ORIGINS` (the root `.env` when the API runs in Docker). The Docker image (root `docker-compose.yml`, CI) is built with
`NEXT_PUBLIC_API_URL=/api`: the browser calls the API on the same origin through Caddy. So keep
request URLs as `${API}/path`, never `new URL(path, API)`, which fails with a relative base. To
check something manually, run the stack (`docker compose up -d` in the repository root, plus
`seed`, which loads the real OSM data) and confirm http://localhost:8000/healthz first.

## Layout

```
app/
  layout.tsx          html lang="ru", bundled fonts (Manrope, Unbounded), globals.css (design tokens)
  page.tsx            "/": AtlasMap + TerritoryCard + header (Brand, ProfilePicker) + Sidebar + ShareButton + ChatWidget
  climate/page.tsx    "/climate": ClimateMap, season and mode switches, ClimateLegend, regions panel
  compare/page.tsx    "/compare": pick 2–3 settlements -> CompareView
  whatif/page.tsx     "/whatif": WhatIfPanel
components/
  UI/                     Icon (inline SVG set), Brand/BrandMark, PageHeader, Segmented, Field (Select,
                          TextInput, FieldLabel), Spinner
  Nav/Sidebar.tsx         navigation drawer (pages + developer links)
  Map/AtlasMap.tsx        MapLibre map, settlements coloured by index, hover tooltip, selection marker, legend
  Map/TerritoryCard.tsx   GET /explain/{id}: total (score ring), typical score, waterfall, top pluses and minuses
  Climate/ClimateMap.tsx  climate surface (lib/climate.ts), isolines, region borders, grid-node tooltip
  Climate/ClimateLegend.tsx, Climate/ClimateStatus.tsx  legend; status pill and temperature format
  Profile/ProfilePicker.tsx   preset buttons (presets hardcoded here)
  Profile/ProfileBuilder.tsx  filters -> weights derived on the client, normalized to 100
  Compare/CompareView.tsx     POST /compare, cards + SVG radar + summary
  WhatIf/WhatIfPanel.tsx      form built from GET /whatif/scenarios, POST /whatif/run, results table
  Chat/ChatWidget.tsx         floating chat, SSE stream via streamChat; a failed or empty turn is rolled
                              back (the question returns to the input), so the history sent to /chat
                              holds only answered exchanges
  ShareButton/ShareButton.tsx copies the current URL
  ProfileBootstrapper.tsx     restores the profile from ?p= or localStorage after hydration (mounted in layout)
lib/
  api.ts       the only backend client: API base, ApiError, TS types, fetch helpers, SSE parser
  palette.ts   colour scales (index, temperature, comfort) blended in OKLab, for MapLibre, canvas and CSS
  climate.ts   builds the climate surface image and isolines from the NASA POWER grid in the browser
  profile.ts   profile <-> base64url JSON (`p`), URL and localStorage (`atlas:profile`) persistence
  store.ts     zustand store useAtlas: profile, selectedTerritoryId, setProfile, patchFilters, selectTerritory
types/profile.ts  profile contract v1 (mirror of backend/app/profiles/contract.py) + DEFAULT_PROFILE
public/maplibre/  MapLibre worker served to the browser (copied from the maplibre-gl 6.17.0 package)
```

## Conventions

- Pages and interactive components are client components (`'use client'`) that fetch in
  `useEffect`. There are no server-side data fetches, API routes or middleware; keep it that way
  unless the task says otherwise.
- **All backend calls go through `lib/api.ts`**: add a typed function that uses the `json<T>()`
  helper, which throws `ApiError(message, code, details)`. Reads use TanStack Query (`useQuery`,
  `keepPreviousData` where the old view should stay while the next loads).
- The TS interfaces in `api.ts` are hand-written and partial (several `any`; `IndexCluster` lacks
  `groups`). The source of truth is `backend/app/api/schemas.py`, `backend/openapi.json` or
  Swagger at `/docs`. When the API changes, update the types in the same change.
- Profile: pass `p: encodeProfile(profile)` in GET query strings and the profile object as
  `profile` in POST bodies. Weights S1–S6 must sum to 100 (the backend allows ±1) or it answers
  `422 profile_invalid`.
- Errors: show `error.message` (already Russian) and, for 422, the list of `details[].loc` and
  `details[].msg` (see `WhatIfPanel`).
- All UI copy is Russian. Never show raw codes such as F01, S3 or W2 to users; take labels from
  API fields (`label`, `name`, `text`, `kinds[].label`). Two known violations exist: the
  `TerritoryCard` waterfall prints `feature_code`, and `CompareView` prints subindex keys (S1–S6).
- Styling uses Tailwind utilities inline with the tokens of `app/globals.css`: colours `ink-*`
  (text, neutrals) and `brand-*` (indigo-violet), `font-display` (Unbounded: logo, titles, big
  numbers) over `font-sans` (Manrope), shadows `shadow-soft`/`shadow-float`/`shadow-glow`, easing
  `ease-spring`, animations `animate-fade-up`/`scale-in`/`sheet-up`. Panels over a map are `glass`
  or `glass-strong`, cards on the other pages `surface` (pages use the `bg-mesh` backdrop and
  `PageHeader`); primary actions `bg-brand-gradient`. Rounded shapes (`rounded-2xl`/`rounded-3xl`).
  No component or icon library: shared pieces are in `components/UI`, icons in `UI/Icon.tsx`.
  Indent with tabs; most files use single quotes and semicolons.
- New page: create `app/<route>/page.tsx` with the page header pattern from `compare`/`whatif`
  (title plus a «← Назад к карте» link), and add the link to the nav in `app/page.tsx`.

## Map notes

- Basemap: Carto Positron (`https://basemaps.cartocdn.com/gl/positron-gl-style/style.json`),
  centred on [44.0, 43.5] at zoom 6.5.
- Source `settlements` (`promoteId: 'id'`) comes from `fetchSettlementsGeoJSON`
  (`GET /territories/geojson?level=settlement&with_index=true&p=...`), drawn as `settlements-halo`
  (blurred glow) and `settlements-circle`. Colour is `interpolate-lab` over `properties.total` with
  `INDEX_STOPS` (lib/palette.ts, also the legend); hover is a feature state. Clicking a point calls
  `selectTerritory(id)`, `TerritoryCard` reacts to it, and a pulsing marker shows the selection.
- The climate map draws a PNG built in the browser (`lib/climate.ts`: bicubic interpolation of the
  NASA POWER grid nodes, rows in Web Mercator, clipped to the region boundaries) as two image
  sources that cross-fade between seasons, plus vector isolines (marching squares). The tooltip shows
  the nearest grid node's real values. "Комфорт" applies the backend's target rule
  (`comfortSeverity` mirrors `color_for` in backend `app/climate/service.py`).
- The basemap labels stay on top: map layers are inserted before the style's first symbol layer.
- `maplibregl.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs')` loads the worker from `public/`.
  When you upgrade `maplibre-gl`, copy the matching worker file from the package.
  `maplibre-gl-shared.mjs` there is an empty placeholder.

## Known gaps (as of 2026-11-12)

- `ProfilePicker` duplicates preset weights and filters from the backend YAML (the realtor weights
  are already rescaled to 100). `GET /profiles/presets` returns them along with a ready `p`.
- `ProfileBuilder` derives weights from filters with client-side heuristics that don't exist in
  the backend.
- `/compare` offers only the 50 largest scored settlements. Comparing territories from different
  clusters returns `422 cluster_mismatch`.
- `WhatIfPanel` hardcodes the airport list for W8.
