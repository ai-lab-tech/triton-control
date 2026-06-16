/* eslint-disable @typescript-eslint/no-explicit-any */
import { TestBed } from "@angular/core/testing";
import { of, throwError } from "rxjs";

import { InstancesService } from "../../../api/generated/index";
import { AuthStore } from "../../../shared/auth/auth.store";
import { InstanceModelRepositoryConfigComponent } from "./instance-model-repository-config.component";

describe("InstanceModelRepositoryConfigComponent", () => {
  let instancesApiMock: jasmine.SpyObj<InstancesService>;

  beforeEach(async () => {
    instancesApiMock = jasmine.createSpyObj<InstancesService>("InstancesService", [
      "getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet",
      "getInstanceS3ContentApiInstancesInstanceIdS3ContentGet",
      "putInstanceS3ContentApiInstancesInstanceIdS3ContentPut",
    ]);
    instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet.and.returnValue(
      of({ name: "simple", max_batch_size: 8 } as any),
    );
    instancesApiMock.getInstanceS3ContentApiInstancesInstanceIdS3ContentGet.and.returnValue(
      of({ path: "/simple/config.pbtxt", content: 'name: "simple"\nbackend: "python"\n' } as any),
    );
    instancesApiMock.putInstanceS3ContentApiInstancesInstanceIdS3ContentPut.and.returnValue(
      of({ path: "/simple/config.pbtxt", size: 31 } as any),
    );

    await TestBed.configureTestingModule({
      imports: [InstanceModelRepositoryConfigComponent],
      providers: [{ provide: InstancesService, useValue: instancesApiMock }],
    }).compileComponents();
  });

  it("CreateComponent_NoS3Loaded_ShowsLoadingRepositorySettings", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelRepositoryConfigComponent);

    // Act
    fixture.componentRef.setInput("instanceId", "7");
    fixture.componentRef.setInput("modelName", "simple");
    fixture.componentRef.setInput("version", "1");
    fixture.detectChanges();

    // Assert
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain("Live API Config");
    expect(text).toContain("Loading instance repository settings");
    expect(fixture.componentInstance.canLoadConfig()).toBeFalse();
  });

  it("ToggleConfig_S3Configured_LoadsEditableConfigPbtxtFromS3", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelRepositoryConfigComponent);
    const component = fixture.componentInstance;
    fixture.componentRef.setInput("instanceId", "7");
    fixture.componentRef.setInput("modelName", "/simple/");
    fixture.componentRef.setInput("version", "1");
    fixture.componentRef.setInput("s3", {
      enabled: true,
      endpoint: "https://s3.local",
      bucket: "models",
      prefix: "/repo/",
    });
    fixture.detectChanges();

    // Act
    await component.toggleConfig();
    fixture.detectChanges();

    // Assert
    expect(component.title()).toBe("config.pbtxt");
    expect(component.editorLanguage()).toBe("proto");
    expect(component.effectivePath()).toBe("repo/simple/config.pbtxt");
    expect(component.content()).toContain('name: "simple"');
    expect(
      instancesApiMock.getInstanceS3ContentApiInstancesInstanceIdS3ContentGet,
    ).toHaveBeenCalledWith("7", "simple/config.pbtxt");
    expect(
      instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet,
    ).not.toHaveBeenCalled();
  });

  it("SaveConfig_S3ConfiguredAndMember_WritesConfigPbtxtToS3", async () => {
    // Arrange
    const auth = TestBed.inject(AuthStore);
    auth.setAuthenticatedUser({ name: "Member", role: "member" });
    const fixture = TestBed.createComponent(InstanceModelRepositoryConfigComponent);
    const component = fixture.componentInstance;
    fixture.componentRef.setInput("instanceId", "7");
    fixture.componentRef.setInput("modelName", "simple");
    fixture.componentRef.setInput("version", "1");
    fixture.componentRef.setInput("s3", {
      enabled: true,
      endpoint: "https://s3.local",
      bucket: "models",
      prefix: "",
    });
    component.content.set('name: "simple"\nbackend: "python"\n');

    // Act
    await component.saveConfig();

    // Assert
    expect(
      instancesApiMock.putInstanceS3ContentApiInstancesInstanceIdS3ContentPut,
    ).toHaveBeenCalledWith(
      'name: "simple"\nbackend: "python"\n',
      "simple/config.pbtxt",
      "7",
      "text/plain; charset=utf-8",
    );
    expect(component.savedMessage()).toBe("Saved config.pbtxt.");
  });

  it("ToggleConfig_NoS3Configured_LoadsReadonlyLiveApiConfig", async () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelRepositoryConfigComponent);
    const component = fixture.componentInstance;
    fixture.componentRef.setInput("instanceId", "7");
    fixture.componentRef.setInput("modelName", "simple");
    fixture.componentRef.setInput("version", "1");
    fixture.componentRef.setInput("s3", { enabled: false, endpoint: "", bucket: "", prefix: "" });
    fixture.detectChanges();

    // Act
    await component.toggleConfig();
    fixture.detectChanges();

    // Assert
    const text = fixture.nativeElement.textContent as string;
    expect(component.title()).toBe("Live API Config");
    expect(component.editorLanguage()).toBe("json");
    expect(component.effectivePath()).toBe("");
    expect(component.content()).toContain('"max_batch_size": 8');
    expect(text).toContain("Read-only Triton runtime config");
    expect(
      instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet,
    ).toHaveBeenCalledWith("7", "simple", "1");
    expect(
      instancesApiMock.getInstanceS3ContentApiInstancesInstanceIdS3ContentGet,
    ).not.toHaveBeenCalled();
  });

  it("LoadConfig_ApiFailure_ShowsModeSpecificError", async () => {
    // Arrange
    instancesApiMock.getInstanceModelConfigApiInstancesInstanceIdModelsModelNameVersionsVersionConfigGet.and.returnValue(
      throwError(() => new Error("down")),
    );
    const fixture = TestBed.createComponent(InstanceModelRepositoryConfigComponent);
    const component = fixture.componentInstance;
    fixture.componentRef.setInput("instanceId", "7");
    fixture.componentRef.setInput("modelName", "simple");
    fixture.componentRef.setInput("version", "1");
    fixture.componentRef.setInput("s3", { enabled: false, endpoint: "", bucket: "", prefix: "" });

    // Act
    await component.toggleConfig();

    // Assert
    expect(component.error()).toBe("Failed to load Live API Config.");
    expect(component.content()).toBe("");
    expect(component.loading()).toBeFalse();
  });

  it("SaveConfig_NoS3Configured_DoesNotWrite", async () => {
    // Arrange
    const auth = TestBed.inject(AuthStore);
    auth.setAuthenticatedUser({ name: "Member", role: "member" });
    const fixture = TestBed.createComponent(InstanceModelRepositoryConfigComponent);
    const component = fixture.componentInstance;
    fixture.componentRef.setInput("instanceId", "7");
    fixture.componentRef.setInput("modelName", "simple");
    fixture.componentRef.setInput("version", "1");
    fixture.componentRef.setInput("s3", { enabled: false, endpoint: "", bucket: "", prefix: "" });

    // Act
    await component.saveConfig();

    // Assert
    expect(
      instancesApiMock.putInstanceS3ContentApiInstancesInstanceIdS3ContentPut,
    ).not.toHaveBeenCalled();
  });
});
