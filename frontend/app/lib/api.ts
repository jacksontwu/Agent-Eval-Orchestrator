const TOKEN_KEY = "aeo_access_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const resp = await fetch(path, { ...init, headers });
  if (resp.status === 401) {
    clearToken();
    if (!window.location.pathname.startsWith("/login")) {
      window.location.assign("/login");
    }
  }
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

export async function patchJSON<T>(path: string, body: unknown): Promise<T> {
  const resp = await apiFetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await resp.json()) as T;
}

export async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const resp = await apiFetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await resp.json()) as T;
}

export async function del<T>(path: string): Promise<T> {
  const resp = await apiFetch(path, { method: "DELETE" });
  return (await resp.json()) as T;
}
