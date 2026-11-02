import type { components } from "./api-types";

export type Shoot = components["schemas"]["ShootOut"];
export type Job = components["schemas"]["JobOut"];
export type ShootCard = components["schemas"]["ShootCard"];
export type Me = components["schemas"]["Me"];

export class HttpError extends Error {
  status: number;
  detail: string | null;
  constructor(status: number, detail: string | null = null) {
    super(`HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function call(path: string, init?: RequestInit) {
  const response = await fetch(path, init);
  if (response.ok) return response;
  const body = await response.json().catch(() => null);
  throw new HttpError(response.status, typeof body?.detail === "string" ? body.detail : null);
}

const send = async <T = Shoot>(path: string, init?: RequestInit): Promise<T> => (await call(path, init)).json();
const postJson = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** `before` holds a time zone with a `+`, which a query string would read as a space. */
export const shootsPath = (before?: string) => (before ? `/api/shoots?before=${encodeURIComponent(before)}` : "/api/shoots");

export const getShoot = (id: string) => send(`/api/shoots/${id}`);
export const createShoot = (form: FormData) => send("/api/shoots", { method: "POST", body: form });
export const orderJob = (id: string, variant?: number) => send(`/api/shoots/${id}/jobs`, postJson(variant ? { variant } : {}));
export const repeatAnalysis = (id: string) => send(`/api/shoots/${id}/analysis`, { method: "POST" });
export const listShoots = (before?: string) => send<ShootCard[]>(shootsPath(before));
export const deleteShoot = async (id: string) => {
  await call(`/api/shoots/${id}`, { method: "DELETE" });
};

export const me = () => send<Me>("/api/me");
export const register = (nickname: string, password: string) => send<Me>("/api/register", postJson({ nickname, password }));
export const login = (nickname: string, password: string) => send<Me>("/api/login", postJson({ nickname, password }));
export const logout = async () => {
  await call("/api/logout", { method: "POST" });
};
