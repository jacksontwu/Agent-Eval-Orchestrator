import { clearToken, getJSON, postJSON, setToken } from "@/lib/api";
import type { LoginResponse, Principal } from "@/lib/types";

export async function login(username: string, password: string): Promise<LoginResponse> {
  const resp = await postJSON<LoginResponse>("/api/auth/login", { username, password });
  setToken(resp.accessToken);
  return resp;
}

export async function currentUser(): Promise<Principal> {
  return getJSON<Principal>("/api/auth/me");
}

export function logout(): void {
  clearToken();
  window.location.assign("/login");
}

export function hasPermission(user: Principal | undefined, permission: string): boolean {
  return Boolean(user?.permissions.includes(permission));
}
