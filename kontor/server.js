// Kontor: small bookkeeping server (stock, invoices, VAT) for Estonian companies.
// One process, SQLite storage, no framework. Run with Node 22+.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import Anthropic from '@anthropic-ai/sdk';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC = path.join(ROOT, 'public');
const DATA = process.env.DATA_DIR || path.join(ROOT, 'data');
const PORT = +(process.env.PORT || 3000);
const AI_MODEL = process.env.AI_MODEL || 'claude-opus-5';
const COOKIE = 'kontor_sid';
const SESSION_DAYS = 30;
const COLLECTIONS = new Set(['products', 'purchases', 'invoices', 'expenses', 'adjustments', 'partners', 'bank', 'settings']);
const LANGS = new Set(['et', 'en', 'ru']);

fs.mkdirSync(path.join(DATA, 'files'), { recursive: true });
fs.mkdirSync(path.join(DATA, 'backups'), { recursive: true });

/* ---------- database ---------- */
const db = new DatabaseSync(path.join(DATA, 'kontor.db'));
db.exec(`
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS companies(id TEXT PRIMARY KEY, name TEXT NOT NULL, ai_key TEXT, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT, pass TEXT NOT NULL,
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'member', is_admin INTEGER NOT NULL DEFAULT 0, lang TEXT NOT NULL DEFAULT 'et',
  created_at INTEGER NOT NULL, last_login INTEGER);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS docs(
  company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE, col TEXT NOT NULL, id TEXT NOT NULL,
  data TEXT NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY(company_id, col, id));
CREATE TABLE IF NOT EXISTS files(
  id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  name TEXT, type TEXT NOT NULL, size INTEGER NOT NULL, created_at INTEGER NOT NULL);
`);
const q = (sql) => db.prepare(sql);
const newId = (n = 12) => crypto.randomBytes(n).toString('base64url');

function hashPassword(pw) {
  const salt = crypto.randomBytes(16);
  const hash = crypto.scryptSync(pw, salt, 64, { N: 16384 });
  return 's1$' + salt.toString('base64') + '$' + hash.toString('base64');
}
function checkPassword(pw, stored) {
  const [v, s, h] = String(stored).split('$');
  if (v !== 's1' || !s || !h) return false;
  const hash = crypto.scryptSync(pw, Buffer.from(s, 'base64'), 64, { N: 16384 });
  const want = Buffer.from(h, 'base64');
  return want.length === hash.length && crypto.timingSafeEqual(want, hash);
}
function createCompany(name) {
  const id = newId(9);
  q('INSERT INTO companies(id,name,created_at) VALUES(?,?,?)').run(id, name, Date.now());
  return id;
}
function createUser({ email, name, password, companyId, role = 'member', isAdmin = false, lang = 'et' }) {
  const id = newId(9);
  q('INSERT INTO users(id,email,name,pass,company_id,role,is_admin,lang,created_at) VALUES(?,?,?,?,?,?,?,?,?)')
    .run(id, email.trim(), name || '', hashPassword(password), companyId, role, isAdmin ? 1 : 0, LANGS.has(lang) ? lang : 'et', Date.now());
  return id;
}

// First start: create the administrator from environment variables.
if (!q('SELECT 1 FROM users LIMIT 1').get()) {
  const email = process.env.ADMIN_EMAIL, password = process.env.ADMIN_PASSWORD;
  if (email && password) {
    const cid = createCompany(process.env.COMPANY_NAME || 'Minu OÜ');
    createUser({ email, name: process.env.ADMIN_NAME || '', password, companyId: cid, role: 'owner', isAdmin: true, lang: process.env.ADMIN_LANG || 'et' });
    console.log(`Created administrator ${email}`);
  } else {
    console.warn('No users yet: set ADMIN_EMAIL and ADMIN_PASSWORD and restart to create the administrator.');
  }
}

/* ---------- backups ---------- */
function backup() {
  const day = new Date().toISOString().slice(0, 10);
  const file = path.join(DATA, 'backups', `kontor-${day}.db`);
  try {
    if (!fs.existsSync(file)) db.exec(`VACUUM INTO '${file.replace(/'/g, "''")}'`);
    const all = fs.readdirSync(path.join(DATA, 'backups')).filter(f => f.endsWith('.db')).sort();
    for (const f of all.slice(0, Math.max(0, all.length - 14))) fs.unlinkSync(path.join(DATA, 'backups', f));
  } catch (e) { console.error('Backup failed:', e.message); }
}
backup();
setInterval(backup, 6 * 3600 * 1000).unref();
setInterval(() => q('DELETE FROM sessions WHERE expires < ?').run(Date.now()), 3600 * 1000).unref();

/* ---------- http helpers ---------- */
function send(res, status, body, headers = {}) {
  const data = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...headers });
  res.end(data);
}
const fail = (res, status, error) => send(res, status, { error });
function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    const chunks = []; let size = 0;
    req.on('data', c => { size += c.length; if (size > limit) { reject(Object.assign(new Error('too_large'), { status: 413 })); req.destroy(); } else chunks.push(c); });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}
async function readJson(req, limit = 2 * 1024 * 1024) {
  const buf = await readBody(req, limit);
  if (!buf.length) return {};
  try { return JSON.parse(buf.toString('utf8')); } catch { throw Object.assign(new Error('bad_json'), { status: 400 }); }
}
function cookies(req) {
  const out = {};
  for (const p of String(req.headers.cookie || '').split(';')) { const i = p.indexOf('='); if (i > 0) out[p.slice(0, i).trim()] = decodeURIComponent(p.slice(i + 1).trim()); }
  return out;
}
const isHttps = req => process.env.COOKIE_SECURE === '1' || req.headers['x-forwarded-proto'] === 'https';
function sessionCookie(req, token, maxAge) {
  return `${COOKIE}=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}${isHttps(req) ? '; Secure' : ''}`;
}
const sha = s => crypto.createHash('sha256').update(s).digest('hex');
function currentUser(req) {
  const token = cookies(req)[COOKIE];
  if (!token) return null;
  return q(`SELECT u.id,u.email,u.name,u.company_id,u.role,u.is_admin,u.lang,c.name AS company_name, c.ai_key IS NOT NULL AS has_key
            FROM sessions s JOIN users u ON u.id=s.user_id JOIN companies c ON c.id=u.company_id
            WHERE s.token=? AND s.expires>?`).get(sha(token), Date.now()) || null;
}
const publicUser = u => ({ id: u.id, email: u.email, name: u.name, role: u.role, isAdmin: !!u.is_admin, lang: u.lang });

// Simple login throttle: 10 failed attempts per IP+email per 15 minutes.
const attempts = new Map();
function throttled(key) {
  const a = attempts.get(key);
  if (!a || a.until < Date.now()) return false;
  return a.count >= 10;
}
function noteFailure(key) {
  const a = attempts.get(key);
  if (!a || a.until < Date.now()) attempts.set(key, { count: 1, until: Date.now() + 15 * 60 * 1000 });
  else a.count++;
}

/* ---------- live updates (server-sent events) ---------- */
const streams = new Map(); // companyId -> Set<res>
function broadcast(companyId, event) {
  const set = streams.get(companyId); if (!set) return;
  const line = `data: ${JSON.stringify(event)}\n\n`;
  for (const res of set) res.write(line);
}
setInterval(() => { for (const set of streams.values()) for (const res of set) res.write(': ping\n\n'); }, 25000).unref();

/* ---------- static files ---------- */
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.webmanifest': 'application/manifest+json', '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon' };
function serveStatic(req, res, pathname) {
  let rel = pathname === '/' ? '/index.html' : pathname;
  const file = path.normalize(path.join(PUBLIC, rel));
  if (!file.startsWith(PUBLIC)) return fail(res, 404, 'not_found');
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) {
      // Single-page app: unknown paths get the app shell.
      if (!path.extname(rel)) return serveStatic(req, res, '/');
      return fail(res, 404, 'not_found');
    }
    const ext = path.extname(file);
    res.writeHead(200, {
      'Content-Type': TYPES[ext] || 'application/octet-stream',
      'Cache-Control': ext === '.html' || rel === '/sw.js' ? 'no-cache' : 'public, max-age=3600',
    });
    fs.createReadStream(file).pipe(res);
  });
}
const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'same-origin',
  'Content-Security-Policy': "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
};

/* ---------- AI extraction ---------- */
const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);
function parseJsonReply(text) {
  const s = text.replace(/```(?:json)?/g, '');
  const a = s.indexOf('{'), b = s.lastIndexOf('}');
  if (a < 0 || b <= a) throw new Error('invalid_json');
  return JSON.parse(s.slice(a, b + 1));
}
async function askClaude(apiKey, content) {
  const client = new Anthropic({ apiKey });
  const msg = await client.beta.messages.create({
    model: AI_MODEL,
    max_tokens: 16000,
    betas: ['server-side-fallback-2026-07-01'],
    fallbacks: 'default',
    messages: [{ role: 'user', content }],
  });
  if (msg.stop_reason === 'refusal') throw Object.assign(new Error('refused'), { status: 422 });
  const text = msg.content.filter(b => b.type === 'text').map(b => b.text).join('');
  return parseJsonReply(text);
}

/* ---------- routes ---------- */
async function api(req, res, url, user) {
  const p = url.pathname, m = req.method;
  let r;

  if (p === '/api/login' && m === 'POST') {
    const { email = '', password = '' } = await readJson(req);
    const key = (req.headers['x-forwarded-for'] || req.socket.remoteAddress) + '|' + String(email).toLowerCase();
    if (throttled(key)) return fail(res, 429, 'too_many_attempts');
    const u = q('SELECT * FROM users WHERE email=?').get(String(email).trim());
    if (!u || !checkPassword(String(password), u.pass)) { noteFailure(key); return fail(res, 401, 'wrong_login'); }
    attempts.delete(key);
    const token = crypto.randomBytes(32).toString('base64url');
    q('INSERT INTO sessions(token,user_id,expires) VALUES(?,?,?)').run(sha(token), u.id, Date.now() + SESSION_DAYS * 86400000);
    q('UPDATE users SET last_login=? WHERE id=?').run(Date.now(), u.id);
    return send(res, 200, { ok: true }, { 'Set-Cookie': sessionCookie(req, token, SESSION_DAYS * 86400) });
  }
  if (!user) return fail(res, 401, 'auth');
  const cid = user.company_id;

  if (p === '/api/logout' && m === 'POST') {
    const token = cookies(req)[COOKIE]; if (token) q('DELETE FROM sessions WHERE token=?').run(sha(token));
    return send(res, 200, { ok: true }, { 'Set-Cookie': sessionCookie(req, '', 0) });
  }
  if (p === '/api/me' && m === 'GET') {
    return send(res, 200, { user: publicUser(user), company: { id: cid, name: user.company_name, aiReady: !!(user.has_key || process.env.ANTHROPIC_API_KEY) } });
  }
  if (p === '/api/me/lang' && m === 'POST') {
    const { lang } = await readJson(req); if (!LANGS.has(lang)) return fail(res, 400, 'bad_lang');
    q('UPDATE users SET lang=? WHERE id=?').run(lang, user.id); return send(res, 200, { ok: true });
  }
  if (p === '/api/me/password' && m === 'POST') {
    const { current = '', next = '' } = await readJson(req);
    const u = q('SELECT pass FROM users WHERE id=?').get(user.id);
    if (!checkPassword(String(current), u.pass)) return fail(res, 400, 'wrong_password');
    if (String(next).length < 8) return fail(res, 400, 'short_password');
    q('UPDATE users SET pass=? WHERE id=?').run(hashPassword(String(next)), user.id);
    return send(res, 200, { ok: true });
  }

  /* data */
  if (p === '/api/data' && m === 'GET') {
    const out = {};
    for (const c of COLLECTIONS) out[c] = [];
    for (const row of q('SELECT col,id,data,updated_at FROM docs WHERE company_id=?').all(cid)) out[row.col].push({ ...JSON.parse(row.data), id: row.id, _v: row.updated_at });
    return send(res, 200, out);
  }
  if ((r = p.match(/^\/api\/doc\/([a-z]+)(?:\/([A-Za-z0-9_.:-]{1,80}))?$/))) {
    const [, col, id0] = r;
    if (!COLLECTIONS.has(col)) return fail(res, 400, 'bad_collection');
    const now = Date.now();
    if (m === 'DELETE' && id0) {
      q('DELETE FROM docs WHERE company_id=? AND col=? AND id=?').run(cid, col, id0);
      broadcast(cid, { col, id: id0, data: null });
      return send(res, 200, { ok: true });
    }
    if (m === 'POST' || m === 'PUT' || m === 'PATCH') {
      const body = await readJson(req);
      if (!body || typeof body !== 'object' || Array.isArray(body)) return fail(res, 400, 'bad_body');
      delete body.id; delete body._v;
      const id = id0 || newId(10);
      let data = body;
      if (m === 'PATCH') {
        const old = q('SELECT data FROM docs WHERE company_id=? AND col=? AND id=?').get(cid, col, id);
        if (!old) return fail(res, 404, 'not_found');
        data = { ...JSON.parse(old.data), ...body };
      }
      q(`INSERT INTO docs(company_id,col,id,data,updated_at) VALUES(?,?,?,?,?)
         ON CONFLICT(company_id,col,id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at`).run(cid, col, id, JSON.stringify(data), now);
      broadcast(cid, { col, id, data: { ...data, id, _v: now } });
      return send(res, 200, { id, data: { ...data, id, _v: now } });
    }
  }
  if (p === '/api/events' && m === 'GET') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', Connection: 'keep-alive' });
    res.write(': ok\n\n');
    if (!streams.has(cid)) streams.set(cid, new Set());
    streams.get(cid).add(res);
    req.on('close', () => streams.get(cid)?.delete(res));
    return;
  }

  /* files */
  if (p === '/api/files' && m === 'POST') {
    const type = String(req.headers['content-type'] || '').split(';')[0].trim();
    if (!(type === 'application/pdf' || IMAGE_TYPES.has(type))) return fail(res, 415, 'bad_type');
    const buf = await readBody(req, 20 * 1024 * 1024);
    if (!buf.length) return fail(res, 400, 'empty');
    const id = newId(16);
    const name = decodeURIComponent(String(req.headers['x-filename'] || 'file')).slice(0, 200);
    fs.mkdirSync(path.join(DATA, 'files', cid), { recursive: true });
    fs.writeFileSync(path.join(DATA, 'files', cid, id), buf);
    q('INSERT INTO files(id,company_id,name,type,size,created_at) VALUES(?,?,?,?,?,?)').run(id, cid, name, type, buf.length, Date.now());
    return send(res, 200, { id, url: '/api/files/' + id, contentType: type, name });
  }
  if ((r = p.match(/^\/api\/files\/([A-Za-z0-9_-]{10,40})$/)) && m === 'GET') {
    const f = q('SELECT * FROM files WHERE id=? AND company_id=?').get(r[1], cid);
    if (!f) return fail(res, 404, 'not_found');
    res.writeHead(200, { 'Content-Type': f.type, 'Content-Disposition': `inline; filename*=UTF-8''${encodeURIComponent(f.name)}`, 'Cache-Control': 'private, max-age=86400' });
    return fs.createReadStream(path.join(DATA, 'files', cid, f.id)).pipe(res);
  }

  /* AI: read an uploaded invoice, or answer a small text question (bank column mapping) */
  if (p === '/api/ai/extract' && m === 'POST') {
    const { fileId, prompt } = await readJson(req);
    const key = q('SELECT ai_key FROM companies WHERE id=?').get(cid)?.ai_key || process.env.ANTHROPIC_API_KEY;
    if (!key) return fail(res, 400, 'no_ai_key');
    if (!prompt || String(prompt).length > 60000) return fail(res, 400, 'bad_prompt');
    const content = [];
    if (fileId) {
      const f = q('SELECT * FROM files WHERE id=? AND company_id=?').get(String(fileId), cid);
      if (!f) return fail(res, 404, 'not_found');
      const data = fs.readFileSync(path.join(DATA, 'files', cid, f.id)).toString('base64');
      if (f.type === 'application/pdf') content.push({ type: 'document', source: { type: 'base64', media_type: 'application/pdf', data } });
      else if (IMAGE_TYPES.has(f.type)) content.push({ type: 'image', source: { type: 'base64', media_type: f.type, data } });
      else return fail(res, 415, 'bad_type');
    }
    content.push({ type: 'text', text: String(prompt) });
    try { return send(res, 200, { result: await askClaude(key, content) }); }
    catch (e) {
      console.error('AI error:', e.status || '', e.message);
      if (e.message === 'invalid_json') return fail(res, 502, 'ai_bad_answer');
      if (e.status === 401) return fail(res, 400, 'ai_bad_key');
      if (e.status === 429) return fail(res, 429, 'ai_busy');
      if (e.status === 422) return fail(res, 422, 'ai_refused');
      return fail(res, 502, 'ai_error');
    }
  }

  /* company settings that stay on the server */
  if (p === '/api/company/aikey' && m === 'POST') {
    if (user.role !== 'owner') return fail(res, 403, 'forbidden');
    const { key } = await readJson(req);
    q('UPDATE companies SET ai_key=? WHERE id=?').run(key ? String(key).trim() : null, cid);
    return send(res, 200, { ok: true });
  }
  if (p === '/api/company/users' && m === 'GET') {
    return send(res, 200, q('SELECT id,email,name,role,lang,last_login FROM users WHERE company_id=? ORDER BY created_at').all(cid));
  }
  if (p === '/api/company/users' && m === 'POST') {
    if (user.role !== 'owner') return fail(res, 403, 'forbidden');
    const { email, name, password, lang } = await readJson(req);
    if (!email || !/.+@.+\..+/.test(email)) return fail(res, 400, 'bad_email');
    if (!password || String(password).length < 8) return fail(res, 400, 'short_password');
    if (q('SELECT 1 FROM users WHERE email=?').get(String(email).trim())) return fail(res, 409, 'email_taken');
    const id = createUser({ email, name, password: String(password), companyId: cid, lang });
    return send(res, 200, { id });
  }
  if ((r = p.match(/^\/api\/company\/users\/([A-Za-z0-9_-]+)$/)) && m === 'DELETE') {
    if (user.role !== 'owner' || r[1] === user.id) return fail(res, 403, 'forbidden');
    q("DELETE FROM users WHERE id=? AND company_id=? AND role!='owner'").run(r[1], cid);
    return send(res, 200, { ok: true });
  }

  /* administrator: companies and their owners */
  if (p.startsWith('/api/admin/')) {
    if (!user.is_admin) return fail(res, 403, 'forbidden');
    if (p === '/api/admin/companies' && m === 'GET') {
      return send(res, 200, q(`SELECT c.id,c.name,c.created_at,
          (SELECT email FROM users WHERE company_id=c.id AND role='owner' ORDER BY created_at LIMIT 1) AS owner,
          (SELECT COUNT(*) FROM users WHERE company_id=c.id) AS users,
          (SELECT COUNT(*) FROM docs WHERE company_id=c.id) AS docs
        FROM companies c ORDER BY c.created_at`).all());
    }
    if (p === '/api/admin/companies' && m === 'POST') {
      const { name, email, ownerName, password, lang } = await readJson(req);
      if (!name) return fail(res, 400, 'bad_name');
      if (!email || !/.+@.+\..+/.test(email)) return fail(res, 400, 'bad_email');
      if (!password || String(password).length < 8) return fail(res, 400, 'short_password');
      if (q('SELECT 1 FROM users WHERE email=?').get(String(email).trim())) return fail(res, 409, 'email_taken');
      const newCid = createCompany(String(name).trim());
      createUser({ email, name: ownerName, password: String(password), companyId: newCid, role: 'owner', lang });
      return send(res, 200, { id: newCid });
    }
    if ((r = p.match(/^\/api\/admin\/companies\/([A-Za-z0-9_-]+)\/password$/)) && m === 'POST') {
      const { password } = await readJson(req);
      if (!password || String(password).length < 8) return fail(res, 400, 'short_password');
      q("UPDATE users SET pass=? WHERE company_id=? AND role='owner'").run(hashPassword(String(password)), r[1]);
      q("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE company_id=? AND role='owner')").run(r[1]);
      return send(res, 200, { ok: true });
    }
  }
  return fail(res, 404, 'not_found');
}

const server = http.createServer(async (req, res) => {
  for (const [k, v] of Object.entries(SECURITY_HEADERS)) res.setHeader(k, v);
  const url = new URL(req.url, 'http://localhost');
  try {
    if (url.pathname.startsWith('/api/')) {
      // Cross-site request protection: writes must carry our header (a plain form or link cannot set it).
      if (req.method !== 'GET' && req.headers['x-kontor'] !== '1') return fail(res, 403, 'forbidden');
      return await api(req, res, url, currentUser(req));
    }
    if (req.method !== 'GET' && req.method !== 'HEAD') return fail(res, 405, 'method');
    return serveStatic(req, res, decodeURIComponent(url.pathname));
  } catch (e) {
    if (!res.headersSent) fail(res, e.status || 500, e.status ? e.message : 'server_error');
    if (!e.status) console.error(e);
  }
});
server.listen(PORT, () => console.log(`Kontor listening on :${PORT}, data in ${DATA}`));
