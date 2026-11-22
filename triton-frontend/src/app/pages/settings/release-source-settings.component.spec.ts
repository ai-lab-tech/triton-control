import { ComponentFixture, TestBed } from "@angular/core/testing";
import { of } from "rxjs";
import { ReleaseSourcesService } from "../../api/generated";
import { InstallationService } from "../../shared/installation/installation.service";
import { ReleaseSourceSettingsComponent } from "./release-source-settings.component";

describe("ReleaseSourceSettingsComponent", () => {
  const source = {
    apiVersion: "serving.triton-control/v1alpha1",
    kind: "MetadataSource",
    metadata: { name: "catalog" },
    spec: {
      location: { type: "s3", bucket: "metadata-bucket", key: "targets/model.json" },
      readCredential: "read-metadata",
      allowedTargets: ["model"],
      intervalSeconds: 86400,
      approval: { mode: "manual" },
      enabled: false,
    },
  };
  const configured = { source, provenance: "file", revision: 2, editable: false };
  const database = { source, provenance: "database", revision: 2, editable: true };
  let fixture: ComponentFixture<ReleaseSourceSettingsComponent>;
  let api: {
    listReleaseSourcesApiReleaseSourcesGet: jasmine.Spy;
    updateReleaseSourceApiReleaseSourcesNamePut: jasmine.Spy;
    deleteReleaseSourceApiReleaseSourcesNameDelete: jasmine.Spy;
  };
  let installation: InstallationService;

  beforeEach(() => {
    api = jasmine.createSpyObj("release sources", [
      "listReleaseSourcesApiReleaseSourcesGet",
      "updateReleaseSourceApiReleaseSourcesNamePut",
      "deleteReleaseSourceApiReleaseSourcesNameDelete",
    ]);
    TestBed.configureTestingModule({
      imports: [ReleaseSourceSettingsComponent],
      providers: [{ provide: ReleaseSourcesService, useValue: api }],
    });
    installation = TestBed.inject(InstallationService);
    installation.snapshot.set({
      mode: "development",
      capabilities: { trust_admin: true },
      explanation: "Development",
    });
  });

  async function load(registrations: unknown[]): Promise<void> {
    api.listReleaseSourcesApiReleaseSourcesGet.and.returnValue(of(registrations));
    fixture = TestBed.createComponent(ReleaseSourceSettingsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  it("shows file provenance, binding and interval without mutation controls", async () => {
    // Arrange
    await load([configured]);

    // Act
    const section = (fixture.nativeElement as HTMLElement).querySelector("section");

    // Assert
    expect(section?.textContent).toContain("Mounted configuration file");
    expect(section?.textContent).toContain("Disabled");
    expect(section?.textContent).toContain("read-metadata");
    expect(section?.textContent).toContain("86400 seconds");
    expect(section?.querySelector("button")).toBeNull();
  });

  it("sends the original revision when disabling and removing a source", async () => {
    // Arrange
    await load([database]);
    const component = fixture.componentInstance;
    const original = component.sources()![0];
    api.updateReleaseSourceApiReleaseSourcesNamePut.and.returnValue(
      of({ ...database, revision: 3 }),
    );
    api.deleteReleaseSourceApiReleaseSourcesNameDelete.and.returnValue(of(undefined));
    component.edit(original);
    const draft = component.draft()!;
    draft.enabled = false;

    // Act
    await component.save(draft);
    const saved = component.sources()![0];
    component.requestRemoval(saved);
    await component.remove(saved);

    // Assert
    expect(api.updateReleaseSourceApiReleaseSourcesNamePut).toHaveBeenCalledOnceWith(
      { source, expected_revision: 2 },
      "catalog",
    );
    expect(api.deleteReleaseSourceApiReleaseSourcesNameDelete).toHaveBeenCalledOnceWith(
      "catalog",
      3,
    );
    expect(component.sources()).toEqual([]);
  });

  it("keeps database sources read-only in production", async () => {
    // Arrange
    installation.snapshot.set({
      mode: "production",
      capabilities: { credential_admin: true },
      explanation: "Production",
    });
    await load([{ ...database, editable: false }]);

    // Act
    const page = fixture.nativeElement as HTMLElement;
    fixture.componentInstance.edit();

    // Assert
    expect(page.textContent).toContain("independent review");
    expect(page.textContent).not.toContain("Add source");
    expect(fixture.componentInstance.draft()).toBeNull();
  });
});
