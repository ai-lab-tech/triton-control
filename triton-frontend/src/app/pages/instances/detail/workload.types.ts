// Explicit response types: the current Swagger generator emits `any` for OpenAPI 3.1 fields.
export interface Condition {
  type: string;
  status: string;
  reason?: string | null;
  message?: string | null;
}
export interface Container {
  name: string;
  kind: "regular" | "init" | "sidecar";
  ready: boolean;
  restart_count: number;
  state: string;
  reason?: string | null;
  message?: string | null;
  exit_code?: number | null;
  last_termination_reason?: string | null;
  image?: string | null;
  image_id?: string | null;
}
export interface Pod {
  uid: string;
  name: string;
  node?: string | null;
  phase: string;
  ready: boolean;
  restarts: number;
  created_at?: string | null;
  terminating: boolean;
  release?: string | null;
  revision?: string | null;
  current_revision: boolean;
  reason?: string | null;
  message?: string | null;
  conditions: Condition[];
  containers: Container[];
}
export interface InstancePods {
  managed: boolean;
  endpoint_live: boolean;
  endpoint_ready: boolean;
  endpoint_checked_at?: string | null;
  deployment?: {
    uid: string;
    name: string;
    generation: number;
    observed_generation?: number | null;
    stale: boolean;
    desired: number;
    updated: number;
    ready: number;
    available: number;
    release?: string | null;
    revision?: string | null;
    conditions: Condition[];
  } | null;
  pods: Pod[];
}
export interface PodLogs {
  streams: {
    pod_uid: string;
    pod_name: string;
    container: string;
    previous: boolean;
    text: string;
    error?: string | null;
    truncated: boolean;
  }[];
  truncated: boolean;
}
