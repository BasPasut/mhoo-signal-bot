const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

export const api = {
  signals: (params?: Record<string, string | number | boolean>) => {
    const qs = params ? "?" + new URLSearchParams(params as any).toString() : "";
    return request<any[]>(`/signals${qs}`);
  },
  signalStats: () => request<any>("/signals/stats/summary"),
  config: () => request<any>("/config"),
  updateConfig: (body: object) =>
    request<any>("/config", { method: "PATCH", body: JSON.stringify(body) }),
  scanNow: () => request<any>("/scan/now", { method: "POST" }),
  price: (symbol: string) => request<any>(`/price/${symbol}`),
  health: () => request<any>("/health"),
  testDiscord: () => request<any>("/discord/test", { method: "POST" }),
  calibration: () => request<any[]>("/signals/calibration"),
  performance: () => request<any[]>("/performance"),
  mlStats: () => request<any>("/ml/stats"),
  mlExport: () => request<any[]>("/ml/export"),
  equityCurve: () => request<{ summary: any; curve: any[] }>("/performance/equity-curve"),
  analytics: () => request<any>("/signals/analytics"),
  orders: (params?: { signal_id?: number }) => {
    const qs = params?.signal_id ? `?signal_id=${params.signal_id}` : "";
    return request<any[]>(`/orders${qs}`);
  },
};
