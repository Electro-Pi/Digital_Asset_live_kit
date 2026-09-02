# LEAP Wayfinder

A voice-guided indoor navigator that combines a native LEAP 2026 WebGL map
with deterministic routing extracted from the supplied `leap plan.pdf`.
The PDF remains the route engine's fixed coordinate system, while the live map
supplies the current venue model, exhibitors, places, filters, and map controls.

## Run it

Configure the navigator LiveKit project in `../.env.navigator`:

```ini
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
```

Start the repository's LiveKit worker in one terminal:

```bash
cd ..
set -a && . ./.env.navigator && set +a
.venv/bin/python agent.py dev
```

Start the frontend and its server-side token route in another terminal:

```bash
npm install
npm run dev
```

Open <http://localhost:4173>.

## What is implemented

- Native LEAP 2026 WebGL map with venue raster tiles, extruded booth geometry,
  walkways, place search, category/sponsor filters, selection, pan, pitch,
  rotation, and zoom behavior.
- A PDF-exact catalog of 842 assigned named booths plus 233 other named public,
  meeting, and operations destinations (1,075 routable destinations total).
- Shortest-path routing over walkable corridors, with the complete ordered
  route shown at once in the guidance panel beside the 3D map.
- One-shot route narration in English or conversational Saudi Arabic: Coda
  reads every deterministic instruction in order, then asks whether the visitor
  needs anything else or another destination. There is no next-step workflow.
- Realtime conversational audio over LiveKit: the browser publishes its
  microphone, while the Coda worker supplies STT, LLM reasoning, interruption
  handling, and the Nouf/Nawaf voice rather than the browser's operating-system
  speech voice.
- Validated LiveKit route actions let the agent update the deterministic map;
  the model never supplies route geometry itself.
- Responsive desktop and mobile UI around the live interactive 3D viewer.

The map is rendered directly by the React application; there is no iframe or
embedded third-party navigation shell. The fixed `/api/leap-map-data` endpoint
retrieves and briefly caches the public LEAP venue dataset, so the latest places
remain available without proxying arbitrary URLs. Venue data and raster tiles
still require network access and remain subject to upstream availability.

The LiveKit API secret stays in the Node server. Browsers receive a unique,
15-minute room token from `POST /api/connection-details`; never expose the
secret through a `VITE_*` variable.

## Destination scope and PDF extraction

The displayed count is generated from the interactive records, not hard-coded.
The PDF-exact catalog contains every assigned named booth record in the PDF's
embedded exhibitor directory: 842 unique assigned booth codes. It also contains
233 other named public, meeting, and operations destinations from the plan, for
1,075 named navigation destinations in total.

The plan also prints 248 unassigned booth codes. Because those codes have no
exhibitor or place name, they are not offered as destinations. Fixture labels
and annotations that cannot be read or identified reliably are excluded as
well; they are not counted as named visitor destinations.

The PDF contains no Arabic text, so exhibitor brand names intentionally retain
their exact Latin spelling in Arabic mode. Public-landmark translations are
curated. When the source plan changes, regenerate the booth catalog with:

```bash
python3 scripts/extract-pdf-places.py \
  --pdf "/path/to/leap plan.pdf" \
  --output src/data/pdfPlaces.json
```

The extractor aligns Poppler's raw text stream with its exact word bounding
boxes, converts the printed booth-code coordinate to the app's map coordinate,
and fails closed if the directory boundaries, counts, or text alignment change.
Data-integrity tests require unique IDs, valid edges, one connected route graph,
and the expected 842 assigned named booths plus 233 other named PDF
destinations.

## Accuracy boundary

The PDF contains no geographic coordinates, routing layer, or live position
feed. Distances are explicitly presented as plan estimates. This experience is
intentionally not live turn tracking: it draws the full schematic route and
narrates it once. Live progress would require a position source such as BLE
beacons, Wi-Fi RTT, QR checkpoints, or venue indoor positioning.

The curated corridor graph lives in `src/data/mapData.ts`; generated PDF catalog
destinations live in `src/data/pdfPlaces.json`. Each catalog endpoint uses its
exact printed position and connects to the nearest calibrated corridor anchor.
Refine those corridor anchors if a newer operational routing plan is provided.
