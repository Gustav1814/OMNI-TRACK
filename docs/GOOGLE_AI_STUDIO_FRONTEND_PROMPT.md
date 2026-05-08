# Google AI Studio — Frontend Generation Prompt for OmniTrack AI

**Use this prompt in Google AI Studio and attach the three reference screenshots.**  
The frontend must match our backend API and auth so we can drop the generated folder into our project.

---

## Project overview

Build a **single-page React app** for **OmniTrack AI**: a multi-camera AI surveillance and retail analytics dashboard. It has:

- **Auth**: Login (JWT), protected routes, token in `localStorage` as `omnitrack_token`.
- **Layout**: Fixed left sidebar (logo + nav), top bar (page title + status), main content area. **Dark theme** with **white/light grey text** and **green or blue accents** (see reference screenshots).
- **Stack**: React 18, Vite, React Router v6, Tailwind CSS (or equivalent utility CSS). Use **axios** for API calls and **Recharts** (or similar) for charts. Optional: Framer Motion for subtle animations.

---

## Reference screenshots (attach these 3 images)

1. **Oniex-style**: Sidebar with Monitoring / Security Camera / Update / License; hero banner “Highest Level of Protection” with large camera graphic; three feature cards (Other Device, Network Monitoring, Backup); **live video feed** (one large) + **camera list** (checkboxes, Camera Hostel / Room / Garden) with volume and controls.
2. **Vigilance NVR-style**: Top nav (Overview, Live View, Playback, Storage, Integrations, Export); **view/server tabs** (View 01, View 02, View 03, Server-A, Server-Jakarta); **left sidebar** with “Network Video Recorder”, **collapsible tree** of CCTVs per view/server (e.g. CCTV 01 / Meeting Room, CCTV 02 / Open Workspace…); **main grid**: one large feed + 5 smaller feeds; **bottom bar**: “All CCTV” dropdown, **timeline** (e.g. 09:00–14:00 with blue activity bars and red playhead), **playback controls** (rewind, play/pause, forward), zoom %, layout toggles, fullscreen.
3. **Motherboard / monitoring-style**: Top nav (Dashboard, Monitoring, Reports, Model Control, Settings); time + date + user (@username); **main area**: title “Motherboard Model X370-A” with **status** (green dot “ACTIVE • Last scan …”); **large central image** (e.g. schematic/board) with **highlight overlay** (e.g. blue “X” on area of interest); zoom controls and **Export** button; **bottom**: “Detections by Hour” **bar chart** (Y: count, X: 6 AM, 9 AM, 12 PM…) and space for more widgets.

Use these for **layout, sections, and visual style** (dark theme, sidebars, hero, cards, video grid, tree nav, timeline, charts, status, export). Do **not** copy branding (Oniex, Vigilance, motherboard); use **OmniTrack** and our feature set below.

---

## Backend API (must match exactly)

- **Base URL**: `/api` (same origin; Vite proxy or deploy with same host).
- **Auth**: Every request (except login/register) must send `Authorization: Bearer <token>`. Token from login is stored in `localStorage` as `omnitrack_token`. On **401**, clear token and redirect to `/login`.

### Endpoints to use

| Area | Method | Path | Purpose |
|------|--------|------|---------|
| **Auth** | POST | `/auth/login` | Body: `{ "username", "password" }` → `{ "access_token", "token_type" }` |
| | POST | `/auth/register` | Register (if needed) |
| | GET | `/auth/me` | Current user (protected) |
| **Dashboard** | GET | `/dashboard/overview` | Overview: `total_cameras`, `active_cameras`, `total_detections_today`, `current_occupancy`, `fire_alerts_today`, `avg_checkout_wait`, `store_vibe`, `peak_hour_today`, `top_zone` |
| **Cameras** | GET | `/cameras/` | List cameras |
| | GET | `/cameras/{id}` | One camera |
| | POST | `/cameras/` | Create camera |
| **Pipeline** | GET | `/pipeline/status` | State, cameras (active/total), AI modules |
| | POST | `/pipeline/start` | Start processing |
| | POST | `/pipeline/stop` | Stop |
| | POST | `/pipeline/cameras/add` | Params: `camera_id`, `source`, `stream_type`, `zone` |
| | GET | `/pipeline/results` | Query: `?camera_id=` optional — latest detections |
| **Detection** | POST | `/detection/start/{camera_id}` | Params: `source`, `stream_type`, `zone` |
| | POST | `/detection/stop/{camera_id}` | Stop detection |
| | GET | `/detection/status` | Status |
| | GET | `/detection/results/{camera_id}` | Detection results |
| | POST | `/detection/recording/start/{camera_id}` | Start recording |
| | POST | `/detection/recording/stop/{camera_id}` | Stop |
| | GET | `/detection/recording/status` | Recording status |
| **Live stream** | GET | `/stream/camera/{camera_id}/live` | MJPEG stream (use in `<img src="...">` with auth query or header) |
| **Re-ID** | POST | `/reid/search` | Body: e.g. crop/embedding — search for person |
| | GET | `/reid/journey/{global_id}` | Cross-camera journey |
| | GET | `/reid/active` | Active re-ID matches |
| **Footage** | GET | `/footage/list` | Query: `?camera_id=` optional — list stored clips |
| | POST | `/footage/upload` | Query: `camera_id`, body: multipart file |
| | GET | `/footage/serve/{filename}` | Stream a stored clip (URL for video src) |
| **Synopsis** | GET | `/synopsis/` | List synopses |
| | POST | `/synopsis/generate` | Query: `camera_id` |
| **Shelf** | GET | `/shelf/engagement` | Zone engagement list |
| | GET | `/shelf/top-zones` | Top zones |
| **Fire** | GET | `/fire/alerts` | Fire/smoke alerts |
| | GET | `/fire/status` | Status |
| **Crowd** | GET | `/crowd/status` | Per-zone crowd status |
| | GET | `/crowd/history/{zone}` | History for zone |
| **Checkout** | GET | `/checkout/metrics` | Metrics |
| | GET | `/checkout/summary` | Summary |
| **Emotion** | GET | `/emotion/current` | Current emotion data |
| | GET | `/emotion/store-sentiment` | Store sentiment |
| **Audit** | GET | `/audit/logs` | Audit logs |
| | GET | `/audit/verify` | Chain verification |
| **Vibe** | GET | `/vibe/current` | Store vibe |
| | GET | `/vibe/trend` | Trend |
| **Demographics** | GET | `/demographics/current` | Demographics |
| **Peak hours** | GET | `/peak-hours/today` | Peak hours summary + hourly data |
| **Export** | GET | `/export/detections` | Query: `format=csv|json` |
| | GET | `/export/traffic` | Query: `format=csv|json` |
| **System** | GET | `/health` | Health check |
| | GET | `/pipeline/status` | Pipeline status |

**WebSocket (optional but recommended for live view):**  
`ws://<host>/ws/live` — events: `detection_update`, `fire_alert`, `crowd_alert`, `vibe_update`, `reid_match`.  
Per-camera: `ws://<host>/ws/camera/{camera_id}`.

---

## Routes and pages (create these)

| Route | Page name | Purpose |
|-------|-----------|--------|
| `/login` | Login | Username/password form → POST `/auth/login` → store token → redirect to `/` |
| `/` | Dashboard | Overview (hero + cards + live feed + camera list) |
| `/detection` | Detection / Live View | NVR-style: sidebar tree (cameras/views), video grid, timeline + playback |
| `/reid` | Re-Identification | Re-ID search, journey, active matches |
| `/synopsis` | Video Synopsis | List synopses, generate, link to footage |
| `/shelf` | Shelf Engagement | Engagement table, top zones |
| `/fire` | Fire & Smoke | Alerts list, status |
| `/crowd` | Crowd Density | Zone status, history |
| `/checkout` | Checkout Analytics | Metrics, summary |
| `/emotion` | Emotion Recognition | Current emotion, store sentiment |
| `/audit` | Audit Log | Logs table, verify button |
| `/vibe` | Store Vibe | Vibe score, trend |
| `/peak-hours` | Peak Hours | Today’s peak, hourly chart |
| `/demographics` | Demographics | Demographics breakdown |

All except `/login` are **protected**: if no `omnitrack_token`, redirect to `/login`.

---

## Layout structure (use in every protected page)

1. **Sidebar (fixed left)**  
   - Logo + “OmniTrack” + “Retail Analytics” (or “Endpoint Security” style).  
   - Nav sections: **Overview** (Dashboard, Store Vibe), **AI Modules** (Detection, Re-ID, Video Synopsis), **Analytics** (Shelf, Fire, Crowd, Checkout, Emotions), **Insights** (Peak Hours, Demographics), **Security** (Audit Log).  
   - Bottom: optional “Managed by / Server / Version” and icons (docs, settings, support).  
   - Highlight active route.

2. **Top bar**  
   - Page title (e.g. “Dashboard”, “Person Detection”).  
   - Right: status badge (“System Online” with green dot), **time**, **date**, **user** (e.g. @username with dropdown).

3. **Main content**  
   - Scrollable; no fixed height that breaks on small screens.  
   - Use the reference screenshots for **section layout** (hero, cards, grid, list, timeline, chart).

---

## Page-by-page sections (what to build)

### Dashboard (`/`)
- **Hero**: “Highest Level of Protection” style — title + short subtitle + 2–3 bullet points; optional large camera/security illustration.
- **Feature cards**: 3 cards (e.g. “Live Monitoring”, “AI Analytics”, “Export & Audit”) with icon + short text. Data can come from `/dashboard/overview` and `/pipeline/status`.
- **Live feed + camera list**:  
  - One **large live feed**: `<img src="/api/stream/camera/1/live" />` (or first active camera from pipeline/cameras).  
  - **Camera list** (right or below): list from `/cameras/` or pipeline status; each row: checkbox, name (e.g. “Camera 1 – Lobby”), “…” menu. Clicking a row can switch the main feed to that camera’s live stream.  
  - Optional: volume slider, snapshot, fullscreen (mirror reference 1).

### Detection / Live View (`/detection`)
- **Top tabs**: “Overview” | “Live View” | “Playback” | “Storage” | “Integrations” | “Export” (match reference 2; “Live View” default for this page).
- **View/Server tabs**: e.g. “View 01”, “View 02”, “Server-A” — can map to camera groups or pipeline cameras; data from `/cameras/` and `/pipeline/status`.
- **Left sidebar**: “Network Video Recorder” with search. **Collapsible tree**: e.g. View 01 → CCTV 01 / Meeting Room, CCTV 02 / Open Workspace… (use camera names and IDs from API). Selecting a node sets which feed is “primary” in the grid.
- **Main area**: **Video grid** — one large cell + 5 smaller (or 2x3). Each cell shows live stream for a camera: `src="/api/stream/camera/{id}/live"`. Labels: “CCTV 01 / Meeting Room” etc. Icons on each: camera, snapshot, settings.
- **Bottom bar**:  
  - “All CCTV” (or selected camera) dropdown.  
  - **Timeline**: time range (e.g. 09:00–14:00), **blue activity bars** (can use `/detection/recording/status` or placeholder), **red playhead**.  
  - **Playback**: rewind, play/pause, forward (for **playback** mode use `/footage/list` and `/footage/serve/{filename}` for the selected camera).  
  - Zoom %, layout toggles (grid 1+5, 2x2, etc.), fullscreen.

### Re-ID (`/reid`)
- Search (e.g. upload crop or enter global_id).  
- Results: list of matches from `/reid/search` or `/reid/active`.  
- Journey view: from `/reid/journey/{global_id}` — timeline or path across cameras.

### Synopsis (`/synopsis`)
- Table of synopses from `/synopsis/`.  
- “Generate” with camera selector → POST `/synopsis/generate?camera_id=`.  
- Link to footage/serve for playback.

### Shelf (`/shelf`)
- Table/cards from `/shelf/engagement`.  
- “Top zones” from `/shelf/top-zones` (e.g. top 5).

### Fire (`/fire`)
- Status from `/fire/status`.  
- Alerts table from `/fire/alerts`.

### Crowd (`/crowd`)
- Cards or table from `/crowd/status` (per zone).  
- Per-zone history from `/crowd/history/{zone}` (dropdown or tabs).

### Checkout (`/checkout`)
- Metrics and summary from `/checkout/metrics`, `/checkout/summary`.

### Emotion (`/emotion`)
- Current emotion from `/emotion/current`.  
- Store sentiment from `/emotion/store-sentiment`.

### Audit (`/audit`)
- Logs table from `/audit/logs`.  
- “Verify” button → GET `/audit/verify`, show result (e.g. chain status).

### Vibe (`/vibe`)
- Store vibe from `/vibe/current` (score, label).  
- Trend from `/vibe/trend` (e.g. small chart).

### Peak Hours (`/peak-hours`)
- Summary from `/peak-hours/today` (peak hour, count, total).  
- **Chart**: “Detections by Hour” or “Visitors by Hour” — X: hours, Y: count (use `hourly_data` from API). Match reference 3 style (bar chart, dark theme, blue bars).

### Demographics (`/demographics`)
- From `/demographics/current`: age distribution, gender distribution, total count (pie/bar charts).

### “Monitoring” / “Reports” style (optional or part of Dashboard/Detection)
- If you add a **Monitoring** or **Reports** page: status line (e.g. “ACTIVE • Last scan: …”), optional central visual (e.g. map or schematic), **Export** button → `/export/detections?format=csv` and `/export/traffic?format=csv`.  
- **Detections by Hour** chart (data from pipeline/detection results or peak-hours hourly_data). Match reference 3.

---

## API client (axios)

- Create an `api` instance: `baseURL: '/api'`, `headers: { 'Content-Type': 'application/json' }`.  
- Request interceptor: read `localStorage.getItem('omnitrack_token')` and set `Authorization: Bearer <token>`.  
- Response interceptor: on **401**, remove `omnitrack_token`, redirect to `/login`.  
- Export typed methods for: auth (login, me), dashboard (overview), cameras, pipeline (status, start, stop, addCamera, results), detection (start, stop, status, results, recording…), reid, footage (list, upload, serveUrl), synopsis, shelf, fire, crowd, checkout, emotion, audit, vibe, demographics, peakHours, export (detections, traffic).  
- **Live stream URL**: `getStreamUrl(cameraId) => \`/api/stream/camera/${cameraId}/live\`` (browser will send cookie/Bearer if same origin; otherwise append `?token=` if backend supports it).

---

## Visual and UX requirements

- **Theme**: Dark background (#1a1a1a–#1e1e1e), white/light grey text, **green or blue** accents for buttons, links, status, charts.  
- **Cards/panels**: Rounded corners, subtle border or shadow.  
- **Tables**: Striped or hover; clear headers.  
- **Charts**: Bar/line/pie from Recharts (or equivalent); match dark theme and accent color.  
- **Video**: Use `<img>` for MJPEG `/api/stream/camera/{id}/live`; use `<video src="...">` for `/api/footage/serve/{filename}`.  
- **Responsive**: Sidebar can collapse to icons on small screens; grid can stack; tables scroll horizontally if needed.

---

## File structure to generate

- `src/App.jsx`: Router, `/login` (public), `/*` (protected layout with sidebar + top bar + outlet).  
- `src/main.jsx`: React root, theme provider if any.  
- `src/services/api.js`: Axios instance + interceptors + all API methods above.  
- `src/pages/LoginPage.jsx`, `DashboardPage.jsx`, `DetectionPage.jsx`, `ReIDPage.jsx`, `SynopsisPage.jsx`, `ShelfPage.jsx`, `FirePage.jsx`, `CrowdPage.jsx`, `CheckoutPage.jsx`, `EmotionPage.jsx`, `AuditPage.jsx`, `VibePage.jsx`, `PeakHoursPage.jsx`, `DemographicsPage.jsx`.  
- `src/components/`: Reusable pieces: `Sidebar.jsx`, `TopBar.jsx`, `LiveFeedCard.jsx`, `CameraList.jsx`, `VideoGrid.jsx`, `TimelinePlayback.jsx`, `DetectionsByHourChart.jsx`, etc.  
- `src/contexts/` (optional): `AuthContext.jsx` for token and user.  
- Router: exact paths as in table; default redirect to `/` when logged in and `/login` when not.

---

## Checklist for the generated app

- [ ] All API paths and methods match the table (prefix `/api`).  
- [ ] JWT in `localStorage` as `omnitrack_token`; sent as `Authorization: Bearer <token>`; 401 → logout and redirect to `/login`.  
- [ ] All 14+ pages exist and are reachable from sidebar.  
- [ ] Dashboard: hero + 3 cards + live feed + camera list.  
- [ ] Detection: NVR-style sidebar tree, video grid, timeline + playback (footage list + serve URL).  
- [ ] At least one “Detections by Hour” (or similar) chart using backend data.  
- [ ] Export buttons for detections and traffic (CSV/JSON).  
- [ ] Dark theme, green or blue accents, layout inspired by the three reference screenshots.

---

**End of prompt.** Generate the full React + Vite project so we can drop it into our repo and run `npm install && npm run dev` with our backend.
