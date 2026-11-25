const TOKEN_KEY = "toutbet.access_token";

let accessToken = readStoredToken();

function readStoredToken() {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token) {
  accessToken = token;
  try {
    if (token) {
      window.sessionStorage.setItem(TOKEN_KEY, token);
    } else {
      window.sessionStorage.removeItem(TOKEN_KEY);
    }
  } catch {
    accessToken = token;
  }
}

export function hasToken() {
  return Boolean(accessToken);
}

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function request(method, path, body, options = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  if (options.idempotent) {
    headers["Idempotency-Key"] = crypto.randomUUID();
  }
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "omit",
    cache: "no-store",
    redirect: "error",
  });
  if (response.status === 204) {
    return null;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = payload.error || {};
    if (response.status === 401 && path !== "/api/auth/login") {
      setToken(null);
    }
    throw new ApiError(response.status, error.code || "UNKNOWN", error.message || "Erreur inattendue.");
  }
  return payload;
}
