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

/** `idempotencyKey`: reuse the same key when retrying one logical action (e.g. one sale). */
export async function api(url, { method = "GET", body, idempotencyKey } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRFToken"] = cookie("csrftoken");
  if (method === "POST") headers["Idempotency-Key"] = idempotencyKey || uuid();

  const response = await fetch(url, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}
