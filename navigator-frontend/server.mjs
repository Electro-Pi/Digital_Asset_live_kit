import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { createServer as createHttpServer } from 'node:http';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { config as loadEnv } from 'dotenv';
import { AccessToken } from 'livekit-server-sdk';

const appDirectory = dirname(fileURLToPath(import.meta.url));
const staticDirectory = resolve(appDirectory, 'dist');

// The navigator has its own LiveKit project. Load it before the repository's
// shared model/TTS environment so a map session cannot accidentally be minted
// into the public Coda project's rooms. Existing process environment always
// wins over files, as it should in systemd/containers.
loadEnv({ path: resolve(appDirectory, '.env.local'), quiet: true });
loadEnv({ path: resolve(appDirectory, '..', '.env.navigator'), quiet: true });
loadEnv({ path: resolve(appDirectory, '..', '.env.local'), quiet: true });

const host = process.env.HOST || '0.0.0.0';
const port = Number.parseInt(process.env.PORT || '4173', 10);
const development = process.env.NODE_ENV === 'development';
const livekitUrl = process.env.LIVEKIT_URL?.trim();
const livekitApiKey = process.env.LIVEKIT_API_KEY?.trim();
const livekitApiSecret = process.env.LIVEKIT_API_SECRET?.trim();
const livekitConfigured = Boolean(livekitUrl && livekitApiKey && livekitApiSecret);
const leapMapDataUrl = 'https://files.invisual.app/map-data/leap.invisual.app/index.json';
const leapMapDataCacheTtlMs = 5 * 60 * 1000;

let leapMapData;
let leapMapDataLoadedAt = 0;

const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
};

function sendFile(response, filePath, fileStat) {
  const extension = extname(filePath).toLowerCase();
  response.writeHead(200, {
    'Content-Type': contentTypes[extension] || 'application/octet-stream',
    'Content-Length': fileStat.size,
    'Cache-Control': filePath.includes(`${sep}assets${sep}`)
      ? 'public, max-age=31536000, immutable'
      : 'public, max-age=300',
    'X-Content-Type-Options': 'nosniff',
  });
  createReadStream(filePath).pipe(response);
}

function sendJson(response, status, body, extraHeaders = {}) {
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    ...extraHeaders,
  });
  response.end(JSON.stringify(body));
}

function sendText(response, status, body, contentType, extraHeaders = {}) {
  response.writeHead(status, {
    'Content-Type': contentType,
    'Cache-Control': 'public, max-age=300',
    'X-Content-Type-Options': 'nosniff',
    ...extraHeaders,
  });
  response.end(body);
}

async function getLeapMapData() {
  if (leapMapData && Date.now() - leapMapDataLoadedAt < leapMapDataCacheTtlMs) {
    return leapMapData;
  }

  const response = await fetch(leapMapDataUrl, { signal: AbortSignal.timeout(20_000) });
  if (!response.ok) throw new Error(`LEAP map data returned ${response.status}`);
  leapMapData = await response.text();
  leapMapDataLoadedAt = Date.now();
  return leapMapData;
}

async function readJsonBody(request) {
  let body = '';
  for await (const chunk of request) {
    body += chunk;
    if (body.length > 16_384) {
      const error = new Error('Request body is too large');
      error.statusCode = 413;
      throw error;
    }
  }
  if (!body) return {};
  try {
    return JSON.parse(body);
  } catch {
    const error = new Error('Request body must be valid JSON');
    error.statusCode = 400;
    throw error;
  }
}

function optionalLocationId(value) {
  if (typeof value !== 'string') return undefined;
  const normalized = value.trim();
  return /^[a-z0-9-]{1,80}$/.test(normalized) ? normalized : undefined;
}

async function connectionDetails(request, response) {
  if (request.method !== 'POST') {
    sendJson(response, 405, { error: 'method_not_allowed' }, { Allow: 'POST' });
    return;
  }
  if (!livekitConfigured) {
    sendJson(response, 503, {
      error: 'voice_not_configured',
      message: 'Live voice navigation is not configured on this server.',
    });
    return;
  }

  const input = await readJsonBody(request);
  const language = input.language === 'ar' ? 'ar' : 'en';
  const voice = input.voice === 'male' ? 'male' : 'female';
  const startId = optionalLocationId(input.startId);
  const destinationId = optionalLocationId(input.destinationId);
  const sessionId = randomUUID();
  const roomName = `leap-navigator-${sessionId}`;
  const participantName = `navigator-${sessionId}`;
  const metadata = JSON.stringify({
    experience: 'leap-navigator',
    language,
    voice,
    ...(startId ? { startId } : {}),
    ...(destinationId ? { destinationId } : {}),
  });

  const token = new AccessToken(livekitApiKey, livekitApiSecret, {
    identity: participantName,
    name: language === 'ar' ? 'زائر خريطة ليب' : 'LEAP map visitor',
    ttl: '15m',
    metadata,
  });
  token.addGrant({
    room: roomName,
    roomJoin: true,
    canPublish: true,
    canSubscribe: true,
    canPublishData: true,
    canUpdateOwnMetadata: true,
  });

  sendJson(response, 200, {
    serverUrl: livekitUrl,
    roomName,
    participantName,
    participantToken: await token.toJwt(),
  });
}

let vite;
if (development) {
  const { createServer: createViteServer } = await import('vite');
  vite = await createViteServer({
    root: appDirectory,
    appType: 'spa',
    server: { middlewareMode: true },
  });
}

const server = createHttpServer(async (request, response) => {
  try {
    const requestUrl = new URL(request.url || '/', `http://${request.headers.host || 'localhost'}`);
    const pathname = decodeURIComponent(requestUrl.pathname);

    if (pathname === '/healthz') {
      sendJson(response, 200, {
        status: 'ok',
        service: 'leap-wayfinder',
        voice: livekitConfigured ? 'configured' : 'unconfigured',
      });
      return;
    }

    if (pathname === '/api/connection-details') {
      await connectionDetails(request, response);
      return;
    }

    if (pathname === '/api/leap-map-data') {
      if (request.method !== 'GET') {
        sendJson(response, 405, { error: 'method_not_allowed' }, { Allow: 'GET' });
        return;
      }
      sendText(response, 200, await getLeapMapData(), 'application/json; charset=utf-8');
      return;
    }

    if (vite) {
      vite.middlewares(request, response, (error) => {
        if (error) {
          console.error('Vite request failed', error);
          if (!response.headersSent) sendJson(response, 500, { error: 'development_server_error' });
        }
      });
      return;
    }

    let filePath = pathname === '/'
      ? join(staticDirectory, 'index.html')
      : resolve(staticDirectory, `.${pathname}`);

    if (filePath !== staticDirectory && !filePath.startsWith(`${staticDirectory}${sep}`)) {
      response.writeHead(403);
      response.end('Forbidden');
      return;
    }

    let fileStat;
    try {
      fileStat = await stat(filePath);
      if (fileStat.isDirectory()) {
        filePath = join(filePath, 'index.html');
        fileStat = await stat(filePath);
      }
    } catch {
      filePath = join(staticDirectory, 'index.html');
      fileStat = await stat(filePath);
    }

    sendFile(response, filePath, fileStat);
  } catch (error) {
    console.error('Request failed', error);
    if (!response.headersSent) {
      sendJson(response, error.statusCode || 500, {
        error: error.statusCode ? 'invalid_request' : 'internal_server_error',
        message: error.statusCode ? error.message : 'Internal server error',
      });
    }
  }
});

server.listen(port, host, () => {
  console.log(`LEAP Wayfinder listening on http://${host}:${port}`);
});

async function shutdown(signal) {
  console.log(`Received ${signal}; shutting down`);
  await vite?.close();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 10_000).unref();
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
