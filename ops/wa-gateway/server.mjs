// WhatsApp gateway: keeps one linked WhatsApp session per client and sends from it, for
// apps/messaging/gateways.py. Built on Baileys (WhatsApp Web's own protocol, no browser per
// session). Unofficial, like Gweb's whatsapp-web.js gateway it replaces: WhatsApp may block a
// number that sends to many people who do not know it.
//
//   WA_GATEWAY_KEY=…  [PORT=3002] [HOST=127.0.0.1] [DATA_DIR=<this folder>/sessions]  node server.mjs
//
// Every request carries "X-API-KEY: <WA_GATEWAY_KEY>". JSON in and out.
//   POST   /sessions/:id/start   start (or restart) linking → {ok, status}
//   GET    /sessions/:id/status  {status: connected|qr|starting|not_started|disconnected, phone}
//   GET    /sessions/:id/qr      {qr: "data:image/png;base64,…"} while waiting for the scan
//   DELETE /sessions/:id         log the number out and forget the session
//   POST   /send                 {session, phone, message} → {ok, id}
//   POST   /send-document        {session, phone, message, filename, pdf_base64} → {ok, id}
// Errors: {ok: false, error}. Linked sessions are kept in DATA_DIR and come back on restart.

import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { existsSync, readdirSync, rmSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import pino from "pino";
import QRCode from "qrcode";

const KEY = process.env.WA_GATEWAY_KEY || "";
const PORT = Number(process.env.PORT || 3002);
const HOST = process.env.HOST || "127.0.0.1";
const DATA_DIR = resolve(process.env.DATA_DIR || join(dirname(fileURLToPath(import.meta.url)), "sessions"));
const MAX_BODY = 25 * 1024 * 1024; // a PDF in base64
const ID = /^[a-z0-9][a-z0-9-]{2,79}$/;

if (!KEY) {
  console.error("Set WA_GATEWAY_KEY (the same value as the platform's WA_GATEWAY_KEY).");
  process.exit(1);
}
mkdirSync(DATA_DIR, { recursive: true });

const logger = pino({ level: process.env.LOG_LEVEL || "warn" });
const sessions = new Map(); // id → {sock, status, qr, phone, retries}

function folder(id) {
  return join(DATA_DIR, id);
}

async function start(id, { force = false } = {}) {
  const current = sessions.get(id);
  if (!force && current && ["starting", "qr", "connected"].includes(current.status)) return current;
  const state = { sock: null, status: "starting", qr: "", phone: "", retries: current?.retries || 0 };
  sessions.set(id, state);

  const { state: auth, saveCreds } = await useMultiFileAuthState(folder(id));
  const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: undefined }));
  const sock = makeWASocket({ auth, version, logger, printQRInTerminal: false, markOnlineOnConnect: false,
                              browser: ["Gweb", "Chrome", "1.0"] });
  state.sock = sock;
  sock.ev.on("creds.update", saveCreds);
  sock.ev.on("connection.update", async (update) => {
    if (sessions.get(id) !== state) return; // replaced by a newer start
    if (update.qr) {
      state.status = "qr";
      state.qr = await QRCode.toDataURL(update.qr, { margin: 1, width: 320 });
    }
    if (update.connection === "open") {
      state.status = "connected";
      state.qr = "";
      state.retries = 0;
      state.phone = (sock.user?.id || "").split(":")[0].split("@")[0];
    }
    if (update.connection === "close") {
      const code = update.lastDisconnect?.error?.output?.statusCode;
      if (code === DisconnectReason.loggedOut) {
        // Unlinked from the phone: start over with a new QR next time.
        state.status = "disconnected";
        rmSync(folder(id), { recursive: true, force: true });
        return;
      }
      // A dropped connection, or the restart WhatsApp asks for right after the scan: a new
      // socket on the same saved keys (the status stays "starting" meanwhile).
      state.retries += 1;
      state.qr = "";
      if (state.retries > 8) {
        state.status = "disconnected";
        return;
      }
      state.status = "starting";
      setTimeout(() => {
        if (sessions.get(id) === state) start(id, { force: true }).catch((error) => logger.error(error));
      }, Math.min(state.retries, 5) * 1000);
    }
  });
  return state;
}

async function stop(id) {
  const state = sessions.get(id);
  sessions.delete(id);
  try {
    await state?.sock?.logout();
  } catch {
    // Already gone.
  }
  rmSync(folder(id), { recursive: true, force: true });
}

function status(id) {
  const state = sessions.get(id);
  if (state) return { status: state.status, phone: state.phone };
  if (existsSync(join(folder(id), "creds.json"))) {
    start(id).catch((error) => logger.error(error)); // linked before a restart: reconnect
    return { status: "starting", phone: "" };
  }
  return { status: "not_started", phone: "" };
}

async function send(body, document) {
  const state = sessions.get(String(body.session || ""));
  if (!state || state.status !== "connected") return [409, { ok: false, error: "session not connected" }];
  const phone = String(body.phone || "").replace(/\D/g, "");
  if (phone.length < 10 || phone.length > 15) return [400, { ok: false, error: "invalid number" }];
  const [found] = await state.sock.onWhatsApp(phone);
  if (!found?.exists) return [404, { ok: false, error: "number not registered on WhatsApp" }];
  const content = document
    ? { document: Buffer.from(String(body.pdf_base64 || ""), "base64"), mimetype: "application/pdf",
        fileName: String(body.filename || "document.pdf"), caption: String(body.message || "") }
    : { text: String(body.message || "") };
  const sent = await state.sock.sendMessage(found.jid, content);
  return [200, { ok: true, id: sent?.key?.id || "" }];
}

function authorised(request) {
  const given = Buffer.from(String(request.headers["x-api-key"] || ""));
  const wanted = Buffer.from(KEY);
  return given.length === wanted.length && timingSafeEqual(given, wanted);
}

function readJson(request) {
  return new Promise((done, fail) => {
    let size = 0;
    const parts = [];
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        fail(Object.assign(new Error("too large"), { status: 413 }));
        request.destroy();
      } else parts.push(chunk);
    });
    request.on("end", () => {
      try {
        done(parts.length ? JSON.parse(Buffer.concat(parts).toString("utf8")) : {});
      } catch {
        fail(Object.assign(new Error("invalid JSON"), { status: 400 }));
      }
    });
  });
}

function reply(response, code, body) {
  response.writeHead(code, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
}

const server = createServer(async (request, response) => {
  if (!authorised(request)) return reply(response, 401, { ok: false, error: "unauthorised" });
  const url = new URL(request.url, "http://gateway");
  const parts = url.pathname.split("/").filter(Boolean);
  try {
    if (parts[0] === "sessions" && parts[1]) {
      const id = parts[1];
      if (!ID.test(id)) return reply(response, 400, { ok: false, error: "invalid session id" });
      if (request.method === "POST" && parts[2] === "start") {
        const state = await start(id);
        return reply(response, 200, { ok: true, status: state.status });
      }
      if (request.method === "GET" && parts[2] === "status") return reply(response, 200, { ok: true, ...status(id) });
      if (request.method === "GET" && parts[2] === "qr") {
        return reply(response, 200, { ok: true, qr: sessions.get(id)?.qr || "" });
      }
      if (request.method === "DELETE" && !parts[2]) {
        await stop(id);
        return reply(response, 200, { ok: true });
      }
    }
    if (request.method === "POST" && (url.pathname === "/send" || url.pathname === "/send-document")) {
      const [code, body] = await send(await readJson(request), url.pathname === "/send-document");
      return reply(response, code, body);
    }
    if (request.method === "GET" && url.pathname === "/health") {
      return reply(response, 200, { ok: true, sessions: sessions.size });
    }
    return reply(response, 404, { ok: false, error: "not found" });
  } catch (error) {
    logger.error(error);
    return reply(response, error.status || 500, { ok: false, error: error.message || "error" });
  }
});

// Sessions linked before a restart reconnect on their own.
for (const id of readdirSync(DATA_DIR)) {
  if (ID.test(id) && existsSync(join(folder(id), "creds.json"))) start(id).catch((error) => logger.error(error));
}
server.listen(PORT, HOST, () => console.log(`WhatsApp gateway on http://${HOST}:${PORT} (sessions in ${DATA_DIR})`));
