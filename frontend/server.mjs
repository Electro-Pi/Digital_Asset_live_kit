// Serves the built React app (dist/) and mints short-lived LiveKit access
// tokens so browsers can join a room. The agent worker auto-dispatches to any
// room (it registers with no agent_name), so joining is all that's needed.
import express from "express";
import { AccessToken } from "livekit-server-sdk";
import dotenv from "dotenv";
import path from "path";
import { fileURLToPath } from "url";
import { randomUUID } from "crypto";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Credentials live in the repo-root .env (one level up).
dotenv.config({ path: path.resolve(__dirname, "../.env") });

const PORT = process.env.FRONTEND_PORT || 8090;
const LIVEKIT_URL = process.env.LIVEKIT_URL;
const API_KEY = process.env.LIVEKIT_API_KEY;
const API_SECRET = process.env.LIVEKIT_API_SECRET;

if (!LIVEKIT_URL || !API_KEY || !API_SECRET) {
  console.error(
    "Missing LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET in ../.env"
  );
  process.exit(1);
}

const app = express();

app.get("/api/token", async (req, res) => {
  try {
    const room = `demo-${randomUUID().slice(0, 8)}`;
    const identity = `user-${randomUUID().slice(0, 8)}`;
    const at = new AccessToken(API_KEY, API_SECRET, { identity, ttl: "15m" });
    at.addGrant({
      room,
      roomJoin: true,
      canPublish: true,
      canSubscribe: true,
    });
    const token = await at.toJwt();
    res.json({ token, serverUrl: LIVEKIT_URL, room, identity });
  } catch (e) {
    console.error("token error", e);
    res.status(500).json({ error: "failed to mint token" });
  }
});

app.get("/healthz", (_req, res) => res.json({ ok: true }));

// Static built assets + SPA fallback.
const dist = path.resolve(__dirname, "dist");
app.use(express.static(dist));
app.get("*", (_req, res) => res.sendFile(path.join(dist, "index.html")));

app.listen(PORT, "0.0.0.0", () => {
  console.log(`Digital Asset frontend listening on http://0.0.0.0:${PORT}`);
});
