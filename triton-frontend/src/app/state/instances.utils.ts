import { TritonInstanceDTO } from "../api/generated/index";
import { type Instance } from "../pages/instances/instances.data";

export function resolveStatus(dto: {
  health_live?: unknown;
  health_ready?: unknown;
}): Instance["status"] {
  const live = !!dto.health_live;
  const ready = !!dto.health_ready;
  if (live && ready) return "healthy";
  if (live) return "warning";
  return "down";
}

export function isSelfDeployedStarting(instance: {
  isSelfDeployed?: boolean;
  healthLive?: boolean;
  healthReady?: boolean;
  healthError?: string;
}): boolean {
  if (!instance.isSelfDeployed || instance.healthLive || instance.healthReady) {
    return false;
  }
  const healthError = (instance.healthError ?? "").toLowerCase();
  return [
    "deployment is starting",
    "kubernetes resources applied",
    "waiting for pod to become ready",
  ].some((message) => healthError.includes(message));
}

export function resolveVersion(dto: { server_metadata?: unknown }): string {
  const metadata = dto.server_metadata as Record<string, unknown> | undefined;
  const version = metadata?.["version"];
  return typeof version === "string" ? version : "";
}

export function dtoToInstance(dto: TritonInstanceDTO): Instance {
  const metadata = (dto.server_metadata ?? null) as Record<string, unknown> | null;
  const deployment = dto as TritonInstanceDTO & Record<string, unknown>;

  return {
    id: String(dto.id),
    name: dto.name,
    url: dto.url,
    status: resolveStatus(dto),
    version: resolveVersion(dto),
    region: "Unknown",
    models: (dto.model_names ?? []).length,
    healthLive: !!dto.health_live,
    healthReady: !!dto.health_ready,
    healthLastCheckedAt: dto.health_last_checked_at ? String(dto.health_last_checked_at) : "",
    healthError: dto.health_error ? String(dto.health_error) : "",
    tritonVerifySsl: !!dto.triton_verify_ssl,
    tritonCaCertificate: dto.triton_ca_certificate ?? "",
    metricsUrl: dto.metrics_url ?? "",
    metricsLastCheckedAt: dto.metrics_last_checked_at ? String(dto.metrics_last_checked_at) : "",
    metricsError: dto.metrics_error ? String(dto.metrics_error) : "",
    deploymentRuntime: stringField(deployment["deployment_runtime"]),
    deploymentNamespace: stringField(deployment["deployment_namespace"]),
    deploymentName: stringField(deployment["deployment_name"]),
    deploymentServiceName: stringField(deployment["deployment_service_name"]),
    deploymentSecretName: stringField(deployment["deployment_secret_name"]),
    deploymentLog: stringField(deployment["deployment_log"]),
    isSelfDeployed: !!deployment["is_self_deployed"],
    podStatuses: Array.isArray(deployment["pod_statuses"])
      ? (deployment["pod_statuses"] as string[])
      : [],
    serverMetadata: metadata,
    qps: 0,
    cpu: normalizePercent(dto.metrics_cpu),
    ram: normalizePercent(dto.metrics_ram),
    gpu: normalizePercent(dto.metrics_gpu),
    assignedUsers: [],
    s3: {
      enabled: !!dto.s3?.enabled,
      bucket: dto.s3?.bucket ?? "",
      region: dto.s3?.region ?? "",
      endpoint: dto.s3?.endpoint ?? "",
      prefix: dto.s3?.prefix ?? "",
      accessKey: dto.s3?.access_key ?? "",
      secretConfigured: !!dto.s3?.secret_configured,
      useHttps: !!dto.s3?.use_https,
      verifySsl: !!dto.s3?.verify_ssl,
      caCertificate: dto.s3?.ca_certificate ?? "",
    },
    modelFiles: [],
    repositoryModels: repositoryModelsFromDto(dto),
  };
}

function repositoryModelsFromDto(dto: TritonInstanceDTO): Instance["repositoryModels"] {
  const rows = Array.isArray(dto.repository_models) ? dto.repository_models : [];
  return rows
    .filter((row) => row && typeof row.name === "string" && row.name.trim().length > 0)
    .map((row) => ({
      name: String(row.name),
      version: row.version != null ? String(row.version) : "",
      state: row.state != null ? String(row.state) : "",
      reason: row.reason != null ? String(row.reason) : "",
    }));
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function normalizePercent(value: unknown): number {
  const parsed = Number(value ?? 0);
  if (!Number.isFinite(parsed)) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round(parsed * 10) / 10));
}
