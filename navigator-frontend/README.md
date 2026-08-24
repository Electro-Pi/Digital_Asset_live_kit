# LEAP Wayfinder

A voice-guided indoor navigator built over the supplied `leap plan.pdf` floor
plan. The PDF is treated as a fixed local coordinate system; a hand-calibrated
corridor graph supplies deterministic shortest-path routing.

## Run it

```bash
npm install
npm run dev
```

Open <http://localhost:4173>.

## What is implemented

- Original PDF floor plan rendered as a high-resolution web asset.
- 17 named, searchable destinations with English and Arabic aliases.
- Shortest-path routing over walkable corridors, with an animated GPS-style
  route line and manual navigation progress.
- Detailed turn-by-turn instructions in English and conversational Saudi
  Arabic.
- Browser speech recognition and text-to-speech. Chrome and Edge provide the
  best speech-input support; all route controls also work without voice.
- Tap-to-select landmarks, zoom, drag, route fitting, and responsive mobile UI.

## Accuracy boundary

The PDF contains no geographic coordinates, routing layer, scale, or live
position feed. Distances are explicitly presented as plan estimates, and the
user advances each instruction manually. Real automatic progress requires a
position source such as BLE beacons, Wi-Fi RTT, QR checkpoints, or venue indoor
positioning. Those sensors can update the `currentPoint` input without changing
the routing or UI architecture.

The graph and landmark coordinates live in `src/data/mapData.ts`; this is the
single place to calibrate more booths or corridors when the event plan changes.
