import { provideHttpClient } from "@angular/common/http";
import { provideHttpClientTesting, HttpTestingController } from "@angular/common/http/testing";
import { TestBed } from "@angular/core/testing";
import { provideRouter, Router } from "@angular/router";
import { of } from "rxjs";

import { BASE_PATH, DeploymentsService } from "../../api/generated/index";
import { NewDeploymentPageComponent } from "./new-deployment-page.component";

describe("NewDeploymentPageComponent", () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [NewDeploymentPageComponent],
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

  it("CanDeploy_MissingRepositoryUrl_ReturnsFalse", () => {
    // Arrange
    const fixture = createComponent();
    const component = fixture.componentInstance;
    component.s3Url = "";

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
    component.image = "nvcr.io/nvidia/tritonserver:25.02-py3";
    component.ingressHost = "triton.example.local";
    component.s3AccessKey = "minioadmin";
    component.s3SecretKey = "secret";

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
    component.s3AccessKey = "minioadmin";
    component.s3SecretKey = "secret";

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
        image: "nvcr.io/nvidia/tritonserver:25.02-py3",
        s3_url: "s3://http://minio:9000/triton-models",
        applied_resources: [],
      }) as unknown as ReturnType<DeploymentsService["createDeploymentApiDeploymentsPost"]>,
    );
    component.deploymentName = "triton-minio";
    component.image = "nvcr.io/nvidia/tritonserver:25.02-py3";
    component.s3Url = "s3://http://minio:9000/triton-models";
    component.ingressHost = "triton.example.local";
    component.s3AccessKey = "minioadmin";
    component.s3SecretKey = "secret";
    component.repositoryPollSecs = 9;
    component.modelName = "simple_identity";
    component.dockerconfigjson.set('{"auths":{"registry.example":{"auth":"token"}}}');
    component.requirementsTxt.set("numpy\n");

    // Act
    await component.deploy();

    // Assert
    expect(deploymentsApi.createDeploymentApiDeploymentsPost).toHaveBeenCalledWith(
      jasmine.objectContaining({
        dockerconfigjson: '{"auths":{"registry.example":{"auth":"token"}}}',
        ingress_host: "triton.example.local",
        model_control_mode: "poll",
        repository_sync_mode: "direct",
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
        image: "nvcr.io/nvidia/tritonserver:25.02-py3",
        s3_url: "s3://https://object-store.example.com/triton-models",
        applied_resources: [],
      }) as unknown as ReturnType<DeploymentsService["createDeploymentApiDeploymentsPost"]>,
    );
    component.deploymentName = "triton";
    component.image = "nvcr.io/nvidia/tritonserver:25.02-py3";
    component.s3Url = "https://object-store.example.com/triton-models";
    component.s3AccessKey = "access";
    component.s3SecretKey = "secret";
    component.cpu = "4";
    component.memory = "10Gi";

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

  it("Deploy_RepositoryPrefixProvided_AppendsNormalizedPrefixToS3Url", async () => {
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
    component.image = "nvcr.io/nvidia/tritonserver:25.02-py3";
    component.s3Url = "https://object-store.example.com/triton-models/";
    component.s3Prefix = "/team/model-repository/";
    component.s3AccessKey = "access";
    component.s3SecretKey = "secret";

    // Act
    await component.deploy();

    // Assert
    expect(deploymentsApi.createDeploymentApiDeploymentsPost).toHaveBeenCalledWith(
      jasmine.objectContaining({
        s3_url: "s3://https://object-store.example.com/triton-models/team/model-repository",
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
    component.image = "nvcr.io/nvidia/tritonserver:25.02-py3";
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
        s3_url: "s3://https://object-store.example.com/triton-models/serving/opt-125m",
        s3_access_key: "profile-access",
        s3_secret_key: "profile-secret",
        s3_region: "eu-central-1",
      }),
    );
  });

  it("UsesManualS3Settings_ProfileSelected_ReturnsFalse", async () => {
    const fixture = createComponent([
      {
        id: 12,
        name: "team-minio",
        endpoint: "https://object-store.example.com",
        bucket: "triton-models",
        region: "eu-central-1",
        access_key: "profile-access",
        secret_key: "profile-secret",
        prefix: "",
        force_path_style: true,
        ca_certificate: "-----BEGIN CERTIFICATE-----",
      },
    ]);
    const component = fixture.componentInstance;
    await fixture.whenStable();

    component.selectedS3ProfileId = "12";
    component.s3ProfileChanged();

    expect(component.usesManualS3Settings()).toBeFalse();
    expect(component.usesHttpsS3()).toBeTrue();
  });
});
