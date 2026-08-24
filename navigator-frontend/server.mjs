import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const appDirectory = dirname(fileURLToPath(import.meta.url));
const staticDirectory = resolve(appDirectory, 'dist');
const host = process.env.HOST || '0.0.0.0';
const port = Number.parseInt(process.env.PORT || '4173', 10);

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

const server = createServer(async (request, response) => {
  try {
    const requestUrl = new URL(request.url || '/', `http://${request.headers.host || 'localhost'}`);
    const pathname = decodeURIComponent(requestUrl.pathname);

    if (pathname === '/healthz') {
      response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      response.end(JSON.stringify({ status: 'ok', service: 'leap-wayfinder' }));
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
    response.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('Internal server error');
  }
});

server.listen(port, host, () => {
  console.log(`LEAP Wayfinder listening on http://${host}:${port}`);
});

function shutdown(signal) {
  console.log(`Received ${signal}; shutting down`);
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 10_000).unref();
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
