// fetch() wrapper for /api/v1: CSRF, JSON, Idempotency-Key, and the error envelope (§11.5).

export class ApiError extends Error {
  constructor(status, body) {
    const error = (body && body.error) || {};
    super(error.message || "");
    this.status = status;
    this.code = error.code || "ERROR";
    this.fields = error.fields || {};
    this.requestId = error.request_id || null;
  }
}

function cookie(name) {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : "";
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

export { uuid };

/** `idempotencyKey`: reuse the same key when retrying one logical action (e.g. one sale).
 * `quiet`: no top loading bar (a screen that shows its own, e.g. live prices while typing). */
export async function api(url, { method = "GET", body, idempotencyKey, quiet = false } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRFToken"] = cookie("csrftoken");
  if (method === "POST") headers["Idempotency-Key"] = idempotencyKey || uuid();

  // Announced for the loading indicators (static/core/js/loading.js).
  const id = uuid();
  const activity = (step) => window.dispatchEvent(new CustomEvent("api:request", {
    detail: { id, step, quiet, writes: method !== "GET" } }));
  activity(1);
  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } finally {
    activity(-1);
  }
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}
