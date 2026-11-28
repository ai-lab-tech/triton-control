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

function formatResourceMetrics(dto: TritonInstanceDTO) {
  const usage = dto.resource_usage;
  const bytes = (value: number) =>
    value >= 1024 ** 3
      ? (value / 1024 ** 3).toLocaleString("en-US", { maximumFractionDigits: 1 }) + " GiB"
      : (value / 1024 ** 2).toLocaleString("en-US", { maximumFractionDigits: 1 }) + " MiB";
  const cpu = (value: number) => Math.round(value) + " mCPU";
  const quantity = (
    used: number | null | undefined,
    limit: number | null | undefined,
    format: (value: number) => string,
  ) => (used == null ? "Unavailable" : format(used) + (limit == null ? "" : " / " + format(limit)));
  const coverage = (samples: number | null | undefined) =>
    samples == null || usage?.desired_replicas == null || usage.running_triton_containers == null
      ? undefined
      : `${samples} sampled · ${usage.running_triton_containers} running · ${usage.desired_replicas} desired`;
  const workload =
    usage?.workload_error ||
    (dto.is_self_deployed
      ? "Running Triton containers across replicas. Usage / limit when configured."
      : "Container metrics require a managed Kubernetes deployment.");
  const gpu =
    usage?.gpu_error ||
    (dto.metrics_url ? "No sample reported by Triton." : "No Triton metrics endpoint configured.");
  return [
    {
      label: "CPU",
      value: quantity(usage?.cpu_millicores, usage?.cpu_limit_millicores, cpu),
      coverage: coverage(usage?.cpu_samples),
      description:
        usage?.cpu_millicores == null ? workload : "CPU usage across running Triton containers.",
    },
    {
      label: "RAM",
      value: quantity(usage?.memory_bytes, usage?.memory_limit_bytes, bytes),
      coverage: coverage(usage?.memory_samples),
      description:
        usage?.memory_bytes == null ? workload : "Memory usage across running Triton containers.",
    },
    {
      label: "GPU",
      value: usage?.gpu_percent == null ? "Unavailable" : usage.gpu_percent + "%",
      description:
        usage?.gpu_percent == null
          ? gpu
          : "Mean utilization across devices on the sampled Triton server.",
    },
    {
      label: "GPU memory",
      value: quantity(usage?.gpu_memory_used_bytes, usage?.gpu_memory_total_bytes, bytes),
      description:
        usage?.gpu_memory_used_bytes == null
          ? gpu
          : "Used / total memory across devices on the sampled Triton server.",
    },
  ];
}

export function dtoToInstance(dto: TritonInstanceDTO): Instance {
  const metadata = (dto.server_metadata ?? null) as Record<string, unknown> | null;
  const deployment = dto as TritonInstanceDTO & Record<string, unknown>;
  const deploymentLog = stringField(deployment["deployment_log"]);
  const deploymentImage =
    stringField(deployment["deployment_image"]) ||
    stringField(metadata?.["image"]) ||
    deploymentLogField(deploymentLog, "Image");
  const deploymentRepository =
    stringField(deployment["deployment_repository"]) ||
    deploymentLogField(deploymentLog, "Model repository");

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
    preparationTaskId: stringField(deployment["preparation_task_id"]),
    deploymentRuntime: stringField(deployment["deployment_runtime"]),
    deploymentNamespace: stringField(deployment["deployment_namespace"]),
    deploymentName: stringField(deployment["deployment_name"]),
    deploymentServiceName: stringField(deployment["deployment_service_name"]),
    deploymentSecretName: stringField(deployment["deployment_secret_name"]),
    deploymentImage,
    deploymentRepository,
    deploymentBackend: resolveDeploymentBackend(
      deployment,
      metadata,
      deploymentLog,
      deploymentImage,
    ),
    deploymentLog,
    isSelfDeployed: !!deployment["is_self_deployed"],
    podStatuses: Array.isArray(deployment["pod_statuses"])
      ? (deployment["pod_statuses"] as string[])
      : [],
    serverMetadata: metadata,
    qps: 0,
    resourceMetrics: formatResourceMetrics(dto),
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

function deploymentLogField(log: string, label: string): string {
  const line = log
    .split(/\r?\n/)
    .find((entry) => entry.toLowerCase().startsWith(`${label.toLowerCase()}:`));
  return line ? line.slice(line.indexOf(":") + 1).trim() : "";
}

function resolveDeploymentBackend(
  deployment: Record<string, unknown>,
  metadata: Record<string, unknown> | null,
  log: string,
  image: string,
): string {
  const explicitBackend =
    stringField(deployment["deployment_backend"]) ||
    stringField(metadata?.["backend"]) ||
    stringField(metadata?.["model_backend"]);
  if (explicitBackend) {
    return formatBackendList(explicitBackend);
  }
  if (/vllm/i.test([log, image].join("\n"))) {
    return "vLLM";
  }
  return "No backend in config.pbtxt";
}

function formatBackendList(value: string): string {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => (part.toLowerCase() === "vllm" ? "vLLM" : part))
    .join(", ");
}

function normalizePercent(value: unknown): number {
  const parsed = Number(value ?? 0);
  if (!Number.isFinite(parsed)) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round(parsed * 10) / 10));
}
