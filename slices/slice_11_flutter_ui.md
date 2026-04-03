# Slice 11: Flutter Web + Android App

> **Goal:** Build the Flutter Web and Android app that connects to the Donna FastAPI backend (slice 10). Provides a chat interface, task Kanban board, calendar view, agent activity monitor, and cost dashboard. Hosted on Firebase Hosting; push notifications via FCM.

> **Note:** This slice lives in a **separate Flutter repository** (`donna-app`). It is not part of the Python backend codebase. The acceptance criteria below describe the integration contract this repo must satisfy.

## Relevant Docs (Python backend)

- `docs/architecture.md` — App Architecture (Phase 4), Auth Flow, API responsibilities
- `slices/slice_10_multiuser_api.md` — REST API endpoints the Flutter app consumes
- `docker/donna-app.yml` — FastAPI backend compose service (port 8200)

## What to Build

### Flutter App (`donna-app/` — separate repo)

#### Screens

| Screen | Purpose |
|--------|---------|
| **Login** | Firebase Auth sign-in (email/password + Google OAuth). Stores JWT; auto-refreshes. |
| **Dashboard** | Summary cards: open tasks, today's schedule, daily cost, agent activity count. |
| **Task Board** | Kanban-style columns: backlog, scheduled, in_progress, blocked, done. Drag to update status via `PATCH /tasks/{id}`. |
| **Calendar** | 7-day week view. Calls `GET /schedule/week`. Donna-managed events highlighted. |
| **Chat** | Freeform text input → `POST /tasks` (task title extracted). Shows task creation confirmation. Future: streaming Donna responses. |
| **Cost Dashboard** | Daily and monthly spend vs budget. Bar chart per task_type. Data from `GET /agents/cost` + `GET /agents/activity`. |
| **Agent Activity** | Timeline of recent LLM invocations. model, task_type, tokens, cost, latency. |
| **Settings** | User profile, notification preferences, theme. |

#### Data Layer

- Flutter reads task/schedule/cost data from Supabase (cloud replica) for cross-device access.
- Writes (`POST /tasks`, `PATCH /tasks/{id}`, `DELETE /tasks/{id}`) go through the FastAPI backend.
- Auth token stored in secure storage; attached to every request as `Authorization: Bearer <jwt>`.

#### Push Notifications

- Register FCM token on login.
- Handle `donna_reminder`, `donna_escalation`, and `donna_digest` notification types.
- Tapping a notification navigates to the relevant task.

### Firebase Hosting Setup

1. Create Firebase project (or reuse existing one used for Auth).
2. `firebase init hosting` — set `public` to Flutter web build output (`build/web`).
3. Add `firebase.json` and `.firebaserc` to the `donna-app` Flutter repo.
4. Deploy: `flutter build web && firebase deploy`.
5. Set `FIREBASE_PROJECT_ID` in `docker/.env` for JWT audience validation.

### CI/CD (Flutter repo)

- GitHub Actions: `flutter test`, `flutter build web`, `firebase deploy --only hosting` on merge to main.
- Android: `flutter build apk --release` artifact on tags.

## Integration Contract (what the Python backend must provide)

The Flutter app depends on these endpoints (all implemented in slice 10):

| Endpoint | Used by |
|----------|---------|
| `GET /health` | App startup connectivity check |
| `GET /tasks` | Task board |
| `POST /tasks` | Chat input, quick-add |
| `GET /tasks/{id}` | Task detail |
| `PATCH /tasks/{id}` | Kanban drag, inline edit |
| `DELETE /tasks/{id}` | Archive/cancel |
| `GET /schedule/week` | Calendar view |
| `GET /agents/activity` | Agent activity timeline |
| `GET /agents/cost` | Cost dashboard |

All endpoints accept `Authorization: Bearer <firebase_jwt>` and return JSON.

## Acceptance Criteria

- [ ] Flutter app builds for web (`flutter build web`) and Android (`flutter build apk`)
- [ ] Login screen authenticates via Firebase Auth; JWT stored securely
- [ ] Dashboard screen loads within 2 seconds on a fresh login
- [ ] Task board displays all open tasks fetched from `GET /tasks`
- [ ] Creating a task via the chat input calls `POST /tasks` and appears on the board
- [ ] Kanban drag updates task status via `PATCH /tasks/{id}`
- [ ] Calendar shows the correct week from `GET /schedule/week`
- [ ] Cost dashboard reflects real invocation_log data from `GET /agents/cost`
- [ ] Push notification arrives when the orchestrator sends a `donna_reminder`
- [ ] `firebase deploy` succeeds and the app loads at the Firebase Hosting URL
- [ ] App works on Android (physical device or emulator)

## Not in Scope

- Local LLM responses via the Flutter chat (Claude API only for Phase 4)
- Second user onboarding (add Nick's dad) — configuration-only when ready
- Offline mode / local caching beyond what Flutter's HTTP layer provides
- Tablet or iPad layout optimisation

## Session Context

Load: `CLAUDE.md`, `slices/slice_10_multiuser_api.md`, `docs/architecture.md` (App Architecture section)
