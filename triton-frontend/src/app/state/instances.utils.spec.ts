import { TritonInstanceDTO } from "../api/generated/index";
import { dtoToInstance, isSelfDeployedStarting, resolveStatus } from "./instances.utils";

describe("instances utils", () => {
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
