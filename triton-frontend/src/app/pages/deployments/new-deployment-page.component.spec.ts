import { provideHttpClient } from "@angular/common/http";
import { provideHttpClientTesting, HttpTestingController } from "@angular/common/http/testing";
import { NO_ERRORS_SCHEMA } from "@angular/core";
import { TestBed } from "@angular/core/testing";
import { provideRouter, Router } from "@angular/router";
import { of } from "rxjs";

import { BASE_PATH, DeploymentsService } from "../../api/generated/index";
import { NewDeploymentPageComponent } from "./new-deployment-page.component";

describe("NewDeploymentPageComponent", () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [NewDeploymentPageComponent],
      schemas: [NO_ERRORS_SCHEMA],
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: DeploymentsService,
          useValue: jasmine.createSpyObj<DeploymentsService>("DeploymentsService", [
            "createDeploymentApiDeploymentsPost",
          ]),
        },
        { provide: BASE_PATH, useValue: "http://localhost:8000" },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    TestBed.inject(HttpTestingController).verify();
  });

  function createComponent(profiles: unknown[] = []) {
    const fixture = TestBed.createComponent(NewDeploymentPageComponent);
    TestBed.inject(HttpTestingController)
      .expectOne("http://localhost:8000/api/s3-profiles")
      .flush(profiles);
    fixture.componentInstance.s3Profiles.set([
      {
        id: 1,
        name: "admin",
        endpoint: "https://s3",
        bucket: "models",
        region: "us-east-1",
        access_key: "",
        secret_key: "",
        prefix: "",
        force_path_style: true,
        ca_certificate: "",
      },
    ]);
    fixture.componentInstance.selectedS3ProfileId = "1";
    return fixture;
  }

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = createComponent();

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
  });

  it("CanDeploy_MissingProfile_ReturnsFalse", () => {
    // Arrange
    const fixture = createComponent();
    const component = fixture.componentInstance;
    component.deploymentName = "test";
    component.selectedS3ProfileId = "";

    // Act
    const canDeploy = component.canDeploy();

    // Assert
    expect(canDeploy).toBeFalse();
  });

  it("CanDeploy_BaseImageAndRepositoryUrlProvided_ReturnsTrue", () => {
    // Arrange
    const fixture = createComponent();
    const component = fixture.componentInstance;
    component.s3Url = "s3://http://minio:9000/triton-models";
    component.deploymentName = "triton-minio";
    component.image = "nvcr.io/nvidia/tritonserver:26.06-py3";
    component.ingressHost = "triton.example.local";

    // Act
    const canDeploy = component.canDeploy();

    // Assert
    expect(canDeploy).toBeTrue();
  });

  it("CanDeploy_ImageMissing_ReturnsFalse", () => {
    // Arrange
    const fixture = createComponent();
    const component = fixture.componentInstance;
    component.s3Url = "s3://http://minio:9000/triton-models";
    component.deploymentName = "triton-minio";
    component.image = "";

    // Act
    const canDeploy = component.canDeploy();

    // Assert
    expect(canDeploy).toBeFalse();
  });

  it("CanDeploy_DecimalMemoryGi_ReturnsFalse", () => {
    // Arrange
    const fixture = createComponent();
    const component = fixture.componentInstance;
    component.s3Url = "s3://http://minio:9000/triton-models";
    component.deploymentName = "triton-minio";
    component.image = "nvcr.io/nvidia/tritonserver:26.06-py3";
    component.memoryGi = 1.5;

    // Act
    const canDeploy = component.canDeploy();

    // Assert
    expect(canDeploy).toBeFalse();
  });

  it("BackendChanged_VllmBackend_UsesSidecarAndKeepsPollMode", () => {
    const fixture = createComponent();
    const component = fixture.componentInstance;
    component.backend = "vllm";
    component.modelControlMode = "poll";
    component.gpuCount = 0;

    component.backendChanged();

    expect(component.modelControlMode).toBe("poll");
    expect(component.repositorySyncMode).toBe("sidecar");
    expect(component.gpuCount).toBe(1);
  });

  it("BackendChanged_TritonBackend_UsesDirectS3", () => {
    const fixture = createComponent();
    const component = fixture.componentInstance;
    component.backend = "triton";
    component.repositorySyncMode = "sidecar";

    component.backendChanged();

    expect(component.repositorySyncMode).toBe("direct");
  });

  it("BackendChanged_TensorRtLlmImage_UsesSidecar", () => {
    const fixture = createComponent();
    const component = fixture.componentInstance;
    component.image = "nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3";

    component.backendChanged();

    expect(component.repositorySyncMode).toBe("sidecar");
  });

  it("Deploy_RequirementsProvided_SendsRequirementsTxt", async () => {
    // Arrange
    const fixture = createComponent();
    const component = fixture.componentInstance;
    const deploymentsApi = TestBed.inject(DeploymentsService) as jasmine.SpyObj<DeploymentsService>;
    const router = TestBed.inject(Router);
    spyOn(router, "navigateByUrl").and.resolveTo(true);
    deploymentsApi.createDeploymentApiDeploymentsPost.and.returnValue(
      of({
        instance_id: 7,
        namespace: "triton-minio",
        deployment_name: "triton-minio",
        service_name: "triton-minio-service",
        secret_name: "triton-minio-s3-credentials",
        image: "nvcr.io/nvidia/tritonserver:26.06-py3",
        s3_url: "s3://http://minio:9000/triton-models",
        applied_resources: [],
      }) as unknown as ReturnType<DeploymentsService["createDeploymentApiDeploymentsPost"]>,
    );
    component.deploymentName = "triton-minio";
    component.image = "nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3";
    component.s3Url = "s3://http://minio:9000/triton-models";
    component.ingressHost = "triton.example.local";
    component.repositoryPollSecs = 9;
    component.modelName = "simple_identity";
    component.requirementsTxt.set("numpy\n");

    // Act
    await component.deploy();

    // Assert
    const payload = deploymentsApi.createDeploymentApiDeploymentsPost.calls.mostRecent().args[0];
    expect(payload.dockerconfigjson).toBeUndefined();
    expect(payload.s3_access_key).toBeUndefined();
    expect(payload.s3_secret_key).toBeUndefined();
    expect(deploymentsApi.createDeploymentApiDeploymentsPost).toHaveBeenCalledWith(
      jasmine.objectContaining({
        s3_profile_id: 1,
        ingress_host: "triton.example.local",
        model_control_mode: "poll",
        repository_sync_mode: "sidecar",
        repository_poll_secs: 9,
        model_name: "simple_identity",
        allow_metrics: true,
        requirements_txt: "numpy",
      }),
    );
    expect(router.navigateByUrl).toHaveBeenCalledWith("/instances/7", {
      state: { openLogsOnce: true },
    });
  });

  it("Deploy_ResourceRequestsProvided_SendsMatchingLimits", async () => {
    // Arrange
    const fixture = createComponent();
    const component = fixture.componentInstance;
    const deploymentsApi = TestBed.inject(DeploymentsService) as jasmine.SpyObj<DeploymentsService>;
    deploymentsApi.createDeploymentApiDeploymentsPost.and.returnValue(
      of({
        instance_id: 7,
        namespace: "triton",
        deployment_name: "triton",
        service_name: "triton-service",
        secret_name: "triton-s3-credentials",
        image: "nvcr.io/nvidia/tritonserver:26.06-py3",
        s3_url: "s3://https://object-store.example.com/triton-models",
        applied_resources: [],
      }) as unknown as ReturnType<DeploymentsService["createDeploymentApiDeploymentsPost"]>,
    );
    component.deploymentName = "triton";
    component.image = "nvcr.io/nvidia/tritonserver:26.06-py3";
    component.s3Url = "https://object-store.example.com/triton-models";
    component.cpu = "4";
    component.memoryGi = 10;

    // Act
    await component.deploy();

    // Assert
    expect(deploymentsApi.createDeploymentApiDeploymentsPost).toHaveBeenCalledWith(
      jasmine.objectContaining({
        cpu: "4",
        cpu_limit: "4",
        memory: "10Gi",
        memory_limit: "10Gi",
      }),
    );
  });

  it("Deploy_ProfileSettings_SendsNormalizedPrefix", async () => {
    // Arrange
    const fixture = createComponent();
    const component = fixture.componentInstance;
    const deploymentsApi = TestBed.inject(DeploymentsService) as jasmine.SpyObj<DeploymentsService>;
    deploymentsApi.createDeploymentApiDeploymentsPost.and.returnValue(
      of({ instance_id: 7 }) as unknown as ReturnType<
        DeploymentsService["createDeploymentApiDeploymentsPost"]
      >,
    );
    component.deploymentName = "triton";
    component.image = "nvcr.io/nvidia/tritonserver:26.06-py3";
    component.s3Url = "https://object-store.example.com/triton-models/";
    component.s3Prefix = "/team/model-repository/";

    // Act
    await component.deploy();

    // Assert
    expect(component.s3Destination()).toBe(
      "s3://https://object-store.example.com/triton-models/team/model-repository",
    );
    expect(deploymentsApi.createDeploymentApiDeploymentsPost).toHaveBeenCalledWith(
      jasmine.objectContaining({
        s3_profile_id: 1,
        repository_prefix: "team/model-repository",
      }),
    );
  });

  it("S3ProfileChanged_ProfileSelected_PopulatesConnectionFieldsOnly", async () => {
    // Arrange
    const fixture = createComponent([
      {
        id: 12,
        name: "team-minio",
        endpoint: "https://object-store.example.com",
        bucket: "triton-models",
        region: "eu-central-1",
        access_key: "profile-access",
        secret_key: "profile-secret",
        prefix: "legacy/profile-prefix",
        force_path_style: true,
        ca_certificate: "-----BEGIN CERTIFICATE-----",
      },
    ]);
    const component = fixture.componentInstance;
    const deploymentsApi = TestBed.inject(DeploymentsService) as jasmine.SpyObj<DeploymentsService>;
    deploymentsApi.createDeploymentApiDeploymentsPost.and.returnValue(
      of({ instance_id: 7 }) as unknown as ReturnType<
        DeploymentsService["createDeploymentApiDeploymentsPost"]
      >,
    );
    component.deploymentName = "opt-125m";
    component.image = "nvcr.io/nvidia/tritonserver:26.06-py3";
    component.s3Prefix = "serving/opt-125m";
    await fixture.whenStable();

    // Act
    component.selectedS3ProfileId = "12";
    component.s3ProfileChanged();
    await component.deploy();

    // Assert
    expect(component.s3Destination()).toBe(
      "s3://https://object-store.example.com/triton-models/serving/opt-125m",
    );
    expect(deploymentsApi.createDeploymentApiDeploymentsPost).toHaveBeenCalledWith(
      jasmine.objectContaining({
        s3_profile_id: 12,
        repository_prefix: "serving/opt-125m",
      }),
    );
  });
});
