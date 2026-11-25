import { request } from "./http.js";

export const api = {
  config: () => request("/workbench"),
  login: data => request("/auth/login", {method: "POST", data}),
  cases: () => request("/cases"),
  createCase: data => request("/cases", {method: "POST", data}),
  runs: () => request("/runs"),
  run: (id, signal) => request(`/runs/${id}`, {signal}),
  enqueue: (id, data) => request(`/cases/${id}/runs`, {method: "POST", data}),
  review: id => request(`/runs/${id}/review`),
  submitReview: (id, data) => request(`/runs/${id}/review`, {method: "POST", data}),
  export: id => request(`/runs/${id}/export`, {raw: true}),
  audit: () => request("/audit", {signal: AbortSignal.timeout(5000)}),
  migrations: () => request("/evidence-migrations"),
  backfillAll: () => request("/evidence-migrations/backfill", {method: "POST"}),
  backfillRun: id => request(`/evidence-migrations/runs/${id}/backfill`, {method: "POST"}),
  switchRun: id => request(`/evidence-migrations/runs/${id}/switch`, {method: "POST"}),
  reclaimRun: id => request(`/evidence-migrations/runs/${id}/reclaim`, {method: "POST"}),
  withdrawRun: id => request(`/evidence-migrations/runs/${id}/withdraw`, {method: "POST"}),
};
