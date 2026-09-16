// 認證：PBKDF2-SHA256 密碼雜湊（Web Crypto）+ D1 opaque session token
import { nowIso, addDays, randomHex } from "./util.js";

// Workers 免費層 CPU 上限 10ms（p99 才強制）；登入/註冊是低頻請求，10 萬次迭代可行，
// 若實測撞牆可降到 60_000 — 迭代數存在 hash 字串裡，調整不影響既有帳號。
const PBKDF2_ITERATIONS = 100_000;
const SESSION_DAYS = 30;

async function pbkdf2(password, saltBytes, iterations) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"]
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: saltBytes, iterations },
    key,
    256
  );
  return new Uint8Array(bits);
}

const b64 = (bytes) => btoa(String.fromCharCode(...bytes));
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

export async function hashPassword(password) {
  const salt = new Uint8Array(16);
  crypto.getRandomValues(salt);
  const hash = await pbkdf2(password, salt, PBKDF2_ITERATIONS);
  return `pbkdf2$${PBKDF2_ITERATIONS}$${b64(salt)}$${b64(hash)}`;
}

export async function verifyPassword(password, stored) {
  const [scheme, iterStr, saltB64, hashB64] = String(stored).split("$");
  if (scheme !== "pbkdf2") return false;
  const expected = unb64(hashB64);
  const actual = await pbkdf2(password, unb64(saltB64), parseInt(iterStr, 10));
  if (actual.length !== expected.length) return false;
  let diff = 0; // constant-time compare
  for (let i = 0; i < actual.length; i++) diff |= actual[i] ^ expected[i];
  return diff === 0;
}

export async function createSession(db, userId) {
  const token = randomHex(32);
  await db
    .prepare("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)")
    .bind(token, userId, addDays(SESSION_DAYS))
    .run();
  return token;
}

/** Authorization: Bearer <token> → user row（無效/過期回 null） */
export async function getUser(request, db) {
  const auth = request.headers.get("authorization") || "";
  const m = auth.match(/^Bearer\s+([a-f0-9]{64})$/i);
  if (!m) return null;
  const row = await db
    .prepare(
      `SELECT u.id, u.email, u.display_name, u.phone, u.role
         FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ?`
    )
    .bind(m[1], nowIso())
    .first();
  return row || null;
}

export async function destroySession(request, db) {
  const auth = request.headers.get("authorization") || "";
  const m = auth.match(/^Bearer\s+([a-f0-9]{64})$/i);
  if (m) await db.prepare("DELETE FROM sessions WHERE token = ?").bind(m[1]).run();
}
