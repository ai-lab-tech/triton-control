import { TritonInstanceDTO } from "../api/generated/index";
import { dtoToInstance, isSelfDeployedStarting, resolveStatus } from "./instances.utils";

describe("instances utils", () => {
  it("keeps measured zero and uses container telemetry independently of the Triton scrape", () => {
    const dto: TritonInstanceDTO = {
      id: 42,
      name: "demo",
      url: "http://demo",
      model_names: [],
      created_at: "2026-06-24T00:00:00Z",
      metrics_cpu: 90,
      metrics_error: "No host CPU sample",
      resource_usage: {
        cpu_millicores: 250,
        cpu_limit_millicores: 1000,
        gpu_percent: 0,
        desired_replicas: 2,
        running_triton_containers: 1,
        cpu_samples: 1,
        memory_samples: 0,
      },
    };
    const metrics = dtoToInstance(dto).resourceMetrics;
    expect(metrics?.[0].coverage).toBe("1 sampled · 1 running · 2 desired");
    expect(metrics?.[1].coverage).toBe("0 sampled · 1 running · 2 desired");
    expect(metrics?.map((metric) => metric.value)).toEqual([
      "250 mCPU / 1000 mCPU",
      "Unavailable",
      "0%",
      "Unavailable",
    ]);
  });

  it("shows known replica counts after a failed metrics read and rounds units consistently", () => {
    const metrics = dtoToInstance({
      id: 7,
      name: "iris",
      url: "http://iris",
      model_names: [],
      created_at: "",
      resource_usage: {
        desired_replicas: 2,
        running_triton_containers: 1,
        workload_error: "Permission denied",
        gpu_memory_used_bytes: 1073700000,
      },
    }).resourceMetrics!;
    expect(metrics[0].coverage).toBe("1 running · 2 desired");
    expect(metrics[0].description).toBe("Permission denied");
    expect(metrics[2].coverage).toBeUndefined();
    expect(metrics[3].coverage).toBe("1 server sampled");
    expect(metrics[3].value).toBe("1 GiB");
  });

  it("DtoToInstance_DeploymentLogContainsRuntimeFields_MapsDeploymentMetadata", () => {
    const dto: TritonInstanceDTO & Record<string, unknown> = {
      id: 42,
      name: "opt125m",
      url: "http://opt125m",
      model_names: [],
      created_at: "2026-06-24T00:00:00Z",
      health_live: true,
      health_ready: true,
      deployment_log: [
        "Namespace: opt125m",
        "Image: nvcr.io/nvidia/tritonserver:25.02-vllm-python-py3",
        "Model repository: s3://https://host.minikube.internal:9000/triton-models/opt125m",
      ].join("\n"),
      is_self_deployed: true,
    };
    const mapped = dtoToInstance(dto);

    expect(mapped.deploymentImage).toBe("nvcr.io/nvidia/tritonserver:25.02-vllm-python-py3");
    expect(mapped.deploymentRepository).toBe(
      "s3://https://host.minikube.internal:9000/triton-models/opt125m",
    );
    expect(mapped.deploymentBackend).toBe("vLLM");
  });

  it("DtoToInstance_ExplicitDeploymentFieldsPresent_PrefersExplicitFields", () => {
    const dto: TritonInstanceDTO & Record<string, unknown> = {
      id: 43,
      name: "manual",
      url: "http://manual",
      model_names: [],
      created_at: "2026-06-24T00:00:00Z",
      health_live: false,
      health_ready: false,
      deployment_image: "custom-image",
      deployment_repository: "s3://bucket/repository",
      deployment_backend: "python",
      deployment_log: "Image: stale-image\nModel repository: s3://stale",
    };
    const mapped = dtoToInstance(dto);

    expect(mapped.deploymentImage).toBe("custom-image");
    expect(mapped.deploymentRepository).toBe("s3://bucket/repository");
    expect(mapped.deploymentBackend).toBe("python");
  });

  it("DtoToInstance_MultipleDeploymentBackendsPresent_FormatsBackendList", () => {
    const dto: TritonInstanceDTO & Record<string, unknown> = {
      id: 46,
      name: "ensemble",
      url: "http://ensemble",
      model_names: [],
      created_at: "2026-06-24T00:00:00Z",
      health_live: true,
      health_ready: true,
      deployment_backend: "ensemble, python, vllm",
    };
    const mapped = dtoToInstance(dto);

    expect(mapped.deploymentBackend).toBe("ensemble, python, vLLM");
  });

  it("DtoToInstance_MetadataImageAndBackendPresent_UsesMetadataFallbacks", () => {
    const dto: TritonInstanceDTO & Record<string, unknown> = {
      id: 44,
      name: "metadata",
      url: "http://metadata",
      model_names: [],
      created_at: "2026-06-24T00:00:00Z",
      health_live: true,
      health_ready: false,
      server_metadata: {
        image: "metadata-image",
        backend: "vllm",
      },
    };
    const mapped = dtoToInstance(dto);

    expect(mapped.deploymentImage).toBe("metadata-image");
    expect(mapped.deploymentBackend).toBe("vLLM");
    expect(mapped.status).toBe("warning");
  });

  it("DtoToInstance_BackendMissing_ReportsConfigBackendMissing", () => {
    const dto: TritonInstanceDTO & Record<string, unknown> = {
      id: 45,
      name: "default-backend",
      url: "http://default-backend",
      model_names: [],
      created_at: "2026-06-24T00:00:00Z",
      health_live: true,
      health_ready: true,
      deployment_image: "nvcr.io/nvidia/tritonserver:26.06-py3",
    };
    const mapped = dtoToInstance(dto);

    expect(mapped.deploymentBackend).toBe("No backend in config.pbtxt");
  });

  it("ResolveStatus_HealthBooleans_MapHealthyWarningAndDown", () => {
    expect(resolveStatus({ health_live: true, health_ready: true })).toBe("healthy");
    expect(resolveStatus({ health_live: true, health_ready: false })).toBe("warning");
    expect(resolveStatus({ health_live: false, health_ready: true })).toBe("down");
  });

  it("IsSelfDeployedStarting_PendingHealthMessage_ReturnsTrueOnlyWhileStarting", () => {
    expect(
      isSelfDeployedStarting({
        isSelfDeployed: true,
        healthLive: false,
        healthReady: false,
        healthError: "Kubernetes resources applied; waiting for pod to become ready.",
      }),
    ).toBeTrue();
    expect(
      isSelfDeployedStarting({
        isSelfDeployed: true,
        healthLive: true,
        healthReady: false,
        healthError: "waiting for pod to become ready",
      }),
    ).toBeFalse();
    expect(
      isSelfDeployedStarting({
        isSelfDeployed: false,
        healthLive: false,
        healthReady: false,
        healthError: "deployment is starting",
      }),
    ).toBeFalse();
  });
});
