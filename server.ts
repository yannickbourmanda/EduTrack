import express from 'express';
import { createProxyMiddleware } from 'http-proxy-middleware';
import { spawn, ChildProcess } from 'child_process';
import http from 'http';

const app = express();
const PORT = 3000;
const DJANGO_PORT = 8000;
const DJANGO_URL = `http://127.0.0.1:${DJANGO_PORT}`;

let djangoProcess: ChildProcess | null = null;
let djangoReady = false;

function startDjango() {
  const pythonPath = '/venv/bin/python';
  console.log('[Dev Server] Launching Django backend on port ' + DJANGO_PORT + '...');
  
  djangoProcess = spawn(pythonPath, ['manage.py', 'runserver', `127.0.0.1:${DJANGO_PORT}`], {
    cwd: process.cwd(),
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe']
  });

  djangoProcess.stdout?.on('data', (data: Buffer) => {
    const text = data.toString();
    process.stdout.write(`[Django] ${text}`);
    if (text.includes('Starting development server') || text.includes('Quit the server')) {
      djangoReady = true;
    }
  });

  djangoProcess.stderr?.on('data', (data: Buffer) => {
    const text = data.toString();
    process.stderr.write(`[Django Error] ${text}`);
    if (text.includes('Starting development server') || text.includes('Quit the server')) {
      djangoReady = true;
    }
  });

  djangoProcess.on('exit', (code) => {
    console.log(`[Dev Server] Django process exited with code ${code}`);
    djangoReady = false;
    // Auto restart after 2 seconds if unexpectedly terminated
    setTimeout(startDjango, 2000);
  });
}

// Check if Django is accepting connections
function checkDjangoHealth(retries = 30): Promise<boolean> {
  return new Promise((resolve) => {
    const check = (remaining: number) => {
      const req = http.get(`${DJANGO_URL}/`, (res) => {
        resolve(true);
      });
      req.on('error', () => {
        if (remaining > 0) {
          setTimeout(() => check(remaining - 1), 500);
        } else {
          resolve(false);
        }
      });
      req.end();
    };
    check(retries);
  });
}

startDjango();

// Proxy middleware to forward all requests (HTML, API, static files, auth cookies) to Django
const proxy = createProxyMiddleware({
  target: DJANGO_URL,
  changeOrigin: true,
  ws: true,
  on: {
    error: (err: any, req: any, res: any) => {
      if (res && !res.headersSent && typeof res.writeHead === 'function') {
        res.writeHead(502, { 'Content-Type': 'text/html' });
        res.end(`
          <!DOCTYPE html>
          <html>
          <head>
            <title>EduTrack Starting...</title>
            <meta http-equiv="refresh" content="2">
            <style>
              body { font-family: system-ui, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #f8fafc; color: #1e293b; }
              .card { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); text-align: center; max-width: 450px; }
              .spinner { width: 40px; height: 40px; border: 4px solid #e2e8f0; border-top-color: #2563eb; border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 1.5rem; }
              @keyframes spin { to { transform: rotate(360deg); } }
            </style>
          </head>
          <body>
            <div class="card">
              <div class="spinner"></div>
              <h2>EduTrack Academic Platform</h2>
              <p>Initializing Django backend server and models. Connecting in a moment...</p>
            </div>
          </body>
          </html>
        `);
      }
    }
  }
});

app.use(proxy);

app.listen(PORT, '0.0.0.0', () => {
  console.log(`[Dev Server] EduTrack Gateway active on http://0.0.0.0:${PORT} -> Django ${DJANGO_URL}`);
});
