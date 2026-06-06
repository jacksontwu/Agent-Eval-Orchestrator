const TOKEN_KEY = "aeo_token";

export function getToken(): string {
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    localStorage.setItem(TOKEN_KEY, fromUrl);
    return fromUrl;
  }
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("X-AEO-Token", token);
  const resp = await fetch(path, { ...init, headers });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.clone().json();
      detail = body.error ?? body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp;
}

export async function getJSON<T>(path: string): Promise<T> {
  const resp = await apiFetch(path);
  return (await resp.json()) as T;
}

export async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const resp = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await resp.json()) as T;
}

export async function del<T>(path: string): Promise<T> {
  const resp = await apiFetch(path, { method: "DELETE" });
  return (await resp.json()) as T;
}
