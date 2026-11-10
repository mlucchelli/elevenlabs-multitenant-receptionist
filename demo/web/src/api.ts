import type { Params, Tenant, ToolName, Vars } from "./types";

async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  const data = await res.json().catch(() => ({ error: res.statusText }));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data as T;
}

export const api = {
  tenants: () => request<Tenant[]>("/api/tenants"),
  preview: (tenant: string) => request<{ dynamic_variables: Vars }>("/api/preview", { tenant }),
  session: (tenant: string) => request<{ signed_url: string; dynamic_variables: Vars }>("/api/session", { tenant }),
  tool: (tenant: string, name: ToolName, params: Params) =>
    request<{ result: unknown; ok: boolean }>(`/api/tools/${name}`, { tenant, params }),
};
