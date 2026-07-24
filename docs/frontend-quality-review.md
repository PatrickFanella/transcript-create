# HasAnAra Web Frontend Quality Review

> **Verdict:** Not production-ready without fixes. The archive experience is promising and the production build succeeds, but a serious injection risk, broken or incomplete flows, stale browser coverage, failing quality gates, and documentation drift must be addressed.

| Review field | Assessment |
| --- | --- |
| Prepared | July 10, 2026 |
| Reviewed revision | Commit `873a78b` on `main` |
| Scope | React frontend, relevant API contracts, tests, CI, and project documentation |
| Strongest signal | The build and unit tests pass; security, quality-gate, browser-coverage, and contract issues remain |
| Review mode | Read-only repository review; no source files or services were changed |

## Executive summary

The frontend has a strong product foundation: archive-wide search, topic intelligence, timestamp deep links, transcript playback synchronization, citation-backed chapters, saved moments, and responsive discovery views. The most important risks are concentrated in trust boundaries and contract integrity rather than basic compilation.

- **Critical:** External transcript and search content is inserted as raw HTML in four user-facing surfaces.
- **Important:** Anonymous saving, saved-search filters, authentication failures, admin access feedback, route errors, and VOD-to-VOD player transitions have broken or misleading behavior.
- **Delivery risk:** Unit tests and the production build pass, while ESLint, Prettier, browser specifications, and dependency audits show unresolved issues.
- **Scalability risk:** All routes are eagerly bundled and long transcript views rerender continuously during playback.
- **Documentation risk:** Product, architecture, accessibility, PWA, design-system, and test documentation no longer describe the shipped frontend accurately.

## Review triage

- Docs-only review: **No**
- React performance review: **Yes**
- UI guidelines audit: **Yes**

The project is a React 19 single-page application containing large transcript views, route-level data fetching, extensive interactive controls, and custom accessibility behavior. UI findings were assessed against the current [Vercel Web Interface Guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md).

## Strengths

- The production build and TypeScript type-check succeed.
- All 23 unit-test files pass: 95 tests passed and 2 are skipped.
- Configured coverage thresholds pass, with 69.4% line coverage.
- Search and VOD-library state is generally represented in the URL.
- The interface has a coherent editorial identity, semantic color tokens, a skip link, global focus treatment, reduced-motion CSS, and responsive layouts.
- Transcript chapters, active playback tracking, timestamp links, saved moments, topic statistics, Explore periods, and archive metadata form a compelling product foundation.
- Backend admin endpoints are authorization-gated even though the frontend admin guard needs improvement.

## Critical security finding

### CRITICAL: Search and transcript snippets permit stored HTML injection

Search snippets from PostgreSQL/OpenSearch and transcript data are rendered through `dangerouslySetInnerHTML` without sanitization. The backend passes external transcript text through `ts_headline` or OpenSearch highlighting but does not escape arbitrary HTML before returning it. A malicious tag or event attribute could therefore become stored cross-site scripting.

Affected code:

- `frontend/src/components/archive/SearchMomentsList.tsx:50`
- `frontend/src/components/archive/TopicMentionCard.tsx:21`
- `frontend/src/routes/TopicPage.tsx:191`
- `frontend/src/components/video/PlainTranscriptTurns.tsx:89`

Recommended action:

1. Prefer returning plain text plus structured highlight ranges and rendering React nodes.
2. As a near-term mitigation, sanitize with a strict allowlist that permits only `<mark>`, and configure every search backend to emit that same tag.
3. Add hostile-snippet regression tests covering event attributes, script tags, malformed markup, and safe highlighting.

There is also a related contract mismatch: PostgreSQL normally emits `<b>`, OpenSearch defaults to `<em>`, API documentation shows `<em>`, while frontend styling and tests expect `<mark>`. Fixing the security boundary should also establish one explicit highlighting contract.

## Important functional findings

### P1: Anonymous users can save moments but cannot open their saved collection

Search and Video pages write anonymous saves to `localStorage`, and `FavoritesPage` contains a local-mode UI. Both `/saved` and `/favorites` are wrapped in `Protected`, so anonymous users see a sign-in requirement rather than their saved moments. This contradicts the page copy that local moments still work in the browser.

Affected code:

- `frontend/src/main.tsx:39-53`
- `frontend/src/routes/FavoritesPage.tsx:102-138`

Recommended action:

- Make the Saved page accessible anonymously and gate only synchronized server data.
- Offer migration of local moments after login.
- Add an anonymous save-to-reopen browser test.

### P1: Supported search filters are silently discarded

The URL parser and saved-search model support `source`, `category`, `duration`, `sort`, `video`, `limit`, and `offset`. `SearchPage` then replaces `source`, `category`, `duration`, and `sort` with `undefined`. Reopened saved searches and deep links can therefore return broader or differently ordered results than requested.

Affected code:

- `frontend/src/features/search/filters.ts:5-34`
- `frontend/src/routes/SearchPage.tsx:150-160`

Recommended action:

- Wire every supported filter into the request and user interface, or remove unsupported fields from frontend contracts until they are implemented.
- Add an integration test that saves and reopens a multi-filter query.

### P1: Async route requests can overwrite newer state

Search, Topic, Streams, Video, and Explore issue requests without cancellation or request-generation guards. A slow response for query, period, or video A can arrive after B and replace the current display. Search also retries the flat endpoint after any grouped-search failure, masking authorization, quota, timeout, or server errors and doubling load.

Affected code:

- `frontend/src/routes/SearchPage.tsx:167-187`
- `frontend/src/routes/ExplorePage.tsx:125-146`

Recommended action:

- Add `AbortController` support or adopt a query library with cancellation, caching, deduplication, and stale-response protection.
- Fall back to flat search only for a recognized unsupported-endpoint response.
- Normalize HTTP errors into authentication, quota, validation, timeout, and outage states.

### P1: Video-player state is unsafe across route changes

`YouTubePlayer` captures `start` once, does not reset `ready` when `videoId` changes, and reuses mutable player state while destroying and recreating the iframe. Direct navigation between two VOD routes can initialize the second VOD with the first timestamp or seek a destroyed player. Script failures have no visible error state.

Affected code:

- `frontend/src/components/YouTubePlayer.tsx:52-109`

Recommended action:

- Reset readiness and pending state on `videoId` and use the current `start` value for each player instance.
- Centralize the YouTube API loader in a shared singleton promise and expose loading and error states.
- Add a rerender test that changes both `videoId` and `start`.

### P1: Authentication initialization produces an unhandled rejection

The `/auth/me` chain has `finally` but no `catch`. The matching error test is skipped because it triggers unhandled rejection warnings. A network failure therefore looks like an anonymous session while also producing an unhandled browser error. Logout suppresses server failure and clears local state even when the session cookie may remain valid.

Affected code:

- `frontend/src/services/auth.tsx:25-48`
- `frontend/src/tests/auth.test.tsx:86-104`

Recommended action:

- Represent auth as `loading`, `authenticated`, `anonymous`, or `error`.
- Restore the skipped network-error test.
- Present logout as successful only after confirmation, or show a retryable failure.

### P1: Route-level not-found and error handling are missing

The router has no wildcard route and no `errorElement`. Unknown URLs render an empty layout, and unexpected render errors can blank the application. The browser suite expects a 404 page that does not exist. The `/login` page also still says authentication is coming soon despite working Google and Twitch OAuth entry points.

Affected code:

- `frontend/src/main.tsx:25-69`
- `frontend/src/routes/LoginPage.tsx:1-8`

Recommended action:

- Add a wildcard 404 page and route-level error boundary with retry and home actions.
- Provide a distinct missing-VOD state.
- Replace or remove the obsolete login placeholder.

### P1: Admin authorization is not reflected in the frontend contract

The current-user response does not expose a role or capability set, and `AdminLayout` allows any authenticated user to render the admin shell before backend requests return 403. Backend enforcement prevents privilege escalation, but normal users can reach a broken-looking administrative experience.

Affected code:

- `app/routes/auth.py:87-102`
- `frontend/src/routes/admin/AdminLayout.tsx:5-11`

Recommended action:

- Return a safe role or capability set from `/auth/me`.
- Require the admin capability before rendering the shell and provide a proper 403 page.
- Prefer explicit capabilities to frontend email or domain heuristics.

### P1: Dependency advisories require immediate triage

The reviewed lockfile audit reported high-severity production findings, including `react-router-dom` 7.9.4, plus critical and high findings in development and build tooling. Several Router advisories concern SSR/RSC paths this SPA does not use, so applicability must be assessed, but the versions should still be updated promptly.

Affected files:

- `frontend/package.json`
- `frontend/package-lock.json`

Recommended action:

- Upgrade production and toolchain dependencies in a bounded change.
- Record which advisories are reachable in this SPA and which are build-time only.
- Run router, deep-link, authentication, production-build, and browser regressions after updating.

## Performance and scalability

### Route bundle

All public and admin routes are eagerly imported. The reviewed production build emitted a single 460.42 KB JavaScript asset (130.90 KB gzip) plus 94.14 KB CSS (13.11 KB gzip). Public visitors therefore download large administrative screens they may never use.

Recommended action:

- Use route-level lazy loading for public pages and especially for the 853-line metadata editor, 588-line period editor, dashboard and chart code, and other admin pages.
- Add an enforceable compressed-size budget per entry chunk rather than only warning on a 500 KB raw bundle.

### Transcript rendering

`VideoPage` polls playback every 750 ms and stores `currentMs` at the route root. That can rerender hundreds of transcript blocks continuously. Match calculation also performs `hits.find()` for every segment, and smooth scrolling may restart on each polling update.

Recommended action:

- Isolate playback time in a small subscribed component or store.
- Memoize transcript blocks and update only the previously and currently active blocks.
- Pre-index hits by timestamp or segment instead of performing repeated nested scans.
- Use `content-visibility: auto` or virtualization/windowing for long transcripts.
- Throttle automatic scrolling and avoid overlapping smooth-scroll animations.

### Data fetching

`SearchPage` requests the complete Explore intelligence response merely to obtain suggested searches. Archive summary, period lists, topic data, and metadata dictionaries are repeatedly fetched without a shared freshness model.

Recommended action:

- Add a lightweight suggestions endpoint or share cached queries.
- Define freshness, retry, and cancellation policies for archive summary, Explore periods, topics, and dictionaries.

### Maintainability hotspots

Several route components combine networking, state machines, data conversion, mutations, and presentation:

| Component | Reviewed size |
| --- | ---: |
| `AdminVideoMetadata.tsx` | 853 lines |
| `VideoPage.tsx` | 724 lines |
| `ExplorePage.tsx` | 660 lines |
| `AdminArchivePeriods.tsx` | 588 lines |
| `SearchPage.tsx` | 485 lines |

Split these by feature responsibility: query hooks, URL-state adapters, view models, mutations, and presentational sections. `VideoPage` especially needs a playback-controller hook and a transcript-navigation model. Add a root verification command that runs tests, coverage, lint, formatting, type-check, build, and selected browser smoke tests.

## Accessibility and UI consistency

### Mobile menu focus

The collapsed menu uses `aria-hidden` but leaves links in the DOM and keyboard tab order. Conditionally render it or use `hidden`/`inert`; support Escape and return focus to the menu button.

- `frontend/src/routes/AppLayout.tsx:173-290`

### Nested landmarks

Search and Explore place additional `main` elements inside `AppLayout`'s `main`. Replace them with `section` or `div` elements.

- `frontend/src/routes/SearchPage.tsx:296`
- `frontend/src/routes/ExplorePage.tsx:397`

### Incorrect tab semantics

Explore declares `role="tablist"` but uses pressed buttons without `role="tab"` or arrow-key behavior. Implement tabs completely or use a labelled button group.

- `frontend/src/routes/ExplorePage.tsx:327-346`

### Unannounced selection

Transcript layout buttons do not expose the active mode with `aria-pressed`.

- `frontend/src/routes/VideoPage.tsx:501-511`

### Touch targets

Several shared controls are 38-40 px despite documentation claiming a 44 px minimum.

- `frontend/src/index.css:148-181`

### Over-broad transitions

`transition-all` is used repeatedly. Enumerate only the intended properties to avoid unnecessary layout and paint work and unpredictable animation.

- `frontend/src/index.css:153-181`

### Image layout stability

Several thumbnail images omit explicit `width` and `height`, increasing the risk of layout shifts.

- `frontend/src/components/archive/VideoCard.tsx:25-30`

### Form metadata

Form fields generally lack meaningful `name` and `autocomplete` attributes.

- `frontend/src/components/archive/SearchFiltersPanel.tsx:45-84`

### Automated accessibility coverage

Axe dependencies are installed but not used. Add assertions for layout, Search, Explore, Saved, Topic, and Video states.

- `frontend/package.json`

## Missing or incomplete product features

Against the product direction recorded in [`docs/frontend-overhaul.md`](frontend-overhaul.md), the largest gaps are:

- Topic history has summary statistics but no topic-over-time visualization.
- There is no citation-backed opinion-over-time experience.
- Episode pages lack related episodes and most-quoted moments.
- Every-mention collections cannot be exported as a list or playlist.
- Timeline exists but is absent from primary navigation.
- Explore period selections are not reflected in the URL, so they cannot be bookmarked or shared.
- PWA and offline behavior are intentionally disabled while PWA assets and claims remain.
- Copy and save actions often lack success or failure feedback.

### Regression coverage gap

The previously observed blank topic-detail and missing people/tag behavior appears to be the target of commit `873a78b`, but frontend tests only use mocked responses. Add one seeded browser flow from an Explore topic card to a nonempty Topic page containing statistics, first/latest mentions, related topics, and grouped results.

## Documentation review

### README and browser-test documentation

The README claims a pricing/upgrade flow and upgrade interstitial, but no pricing route or frontend billing implementation exists. Billing E2E tests navigate to `/pricing`, while job tests expect job-creation screens and `/jobs/:id` routes that are also absent.

Browser specifications also:

- use `/videos/:id` instead of `/v/:id`;
- omit the required `{ user: ... }` auth envelope;
- expect automatic authenticated redirects;
- expect a nonexistent 404 page; and
- return search fields such as `results` and `segment_text` instead of `hits` and `snippet`.

The published “255 E2E tests” figure describes scenario/browser combinations rather than 255 current valid workflows. The PR-critical CI runs the particularly stale auth, job, and search specifications.

Recommended action: rewrite the browser suite around current archive workflows—anonymous search, filters, Explore, topic detail, VOD playback and deep links, local and synchronized saves, exports, admin denial, and mobile navigation.

### Architecture documentation

[`docs/development/architecture.md`](development/architecture.md) describes React 18 and Axios. The current frontend uses React 19 and Ky. It also documents frontend job submission and polling that do not exist.

### Design-system documentation

[`docs/DESIGN_SYSTEM.md`](DESIGN_SYSTEM.md) describes the former blue/orange Satoshi SaaS theme. The implementation now uses Alegreya/Atkinson, dark editorial surfaces, a lime accent, and a purple CTA. The token table and referenced pricing surfaces are obsolete.

### Accessibility and PWA documentation

[`docs/ACCESSIBILITY.md`](ACCESSIBILITY.md) claims WCAG 2.1 AA compliance, Escape-to-close, a slash search shortcut, 44 px targets, working offline support/background sync, and roughly 101 KB gzip JavaScript. Several claims are false. Most significantly, `main.tsx` unregisters every service worker and deletes all caches while documentation still describes a working offline PWA.

Recommended action:

- Change compliance language to “targets WCAG 2.2 AA” until independently audited.
- Record the browser and assistive-technology combinations actually tested.
- State PWA status explicitly and either restore it or remove obsolete assets and installation claims.

### Developer testing documentation

[`docs/development/testing.md`](development/testing.md) documents `npm run test:watch`, which does not exist. Frontend CI documentation should also state that lint and formatting are blocking checks; the current local failures are not merely informational warnings.

### Documentation improvement model

- Separate shipped behavior, planned work, and historical implementation notes into clearly labelled documents.
- Add a frontend route and capability inventory maintained beside the router.
- Generate or validate frontend API types against the OpenAPI schema to prevent envelope and field drift.
- Create one canonical developer workflow with setup, verification, browser smoke, and troubleshooting commands.
- Treat accessibility and performance figures as measured evidence with a date and environment, not permanent claims.

## Verification results

| Check | Result | Evidence |
| --- | --- | --- |
| Unit tests | PASS | 23 files; 95 passed, 2 skipped |
| Coverage thresholds | PASS | 69.4% lines; 67.31% statements; 57.19% branches |
| TypeScript | PASS | `npx tsc --noEmit` |
| Production build | PASS | 460.42 KB JS / 94.14 KB CSS |
| ESLint | FAIL | 20 errors and 1 warning |
| Prettier | FAIL | 42 files require formatting |
| `git diff --check` | PASS | No whitespace errors |
| Browser E2E | NOT RUN | Docker-starting suite skipped; static inspection shows route and contract drift |
| Dependency audit | ACTION | Production and development dependency advisories require triage |

The Docker-backed browser suite was not started because this was a read-only review and the Playwright configuration creates external service state. These results are a snapshot of commit `873a78b`; rerun the checks after subsequent frontend changes.

## Recommended implementation order

1. Eliminate unsafe HTML rendering and update vulnerable dependencies.
2. Restore a green frontend gate: lint, formatting, the auth error test, and current browser smoke tests.
3. Fix user-facing contract breaks: anonymous Saved access, preserved search filters, 404/error boundaries, and auth/admin capabilities.
4. Add request cancellation and correct YouTube-player route-change behavior.
5. Split routes and isolate long-transcript playback updates.
6. Reconcile README, architecture, design-system, accessibility, PWA, and E2E documentation.
7. Implement product-direction gaps such as topic timelines, related episodes, cited opinion history, and exportable mention collections.

## Acceptance criteria for the next review

- [ ] No user-controlled or externally sourced string reaches `dangerouslySetInnerHTML` unsanitized.
- [ ] Anonymous users can save and reopen local moments.
- [ ] Every serialized search filter is honored or rejected explicitly.
- [ ] Changing query, period, or video cannot display stale prior responses.
- [ ] Unknown routes and missing videos have tested recovery pages.
- [ ] Admin routes render only for users with the corresponding capability.
- [ ] Unit tests, coverage, type-check, lint, formatting, build, and a current browser smoke set all pass from one command.
- [ ] Public-route JavaScript excludes admin bundles and meets an explicit compressed-size budget.
- [ ] Accessibility and PWA documentation matches tested behavior.
- [ ] README and architecture documentation match the current router, packages, and API envelopes.
