/* eslint-disable @typescript-eslint/no-explicit-any */
import { TestBed } from "@angular/core/testing";
import { ActivatedRoute, convertToParamMap } from "@angular/router";
import { of, Subject, throwError } from "rxjs";
import { Action } from "@ngrx/store";
import { MatSnackBar } from "@angular/material/snack-bar";
import { MockStore, provideMockStore } from "@ngrx/store/testing";
import { provideMockActions } from "@ngrx/effects/testing";
import { InstanceS3BrowserPageComponent } from "./instance-s3-browser-page.component";
import { InstancesService } from "../../../api/generated/index";
import {
  selectS3CurrentPath,
  selectS3EditorFilePath,
  selectS3EditorFileName,
  selectS3Entries,
  selectS3KnownFolderPaths,
} from "../../../state/instances-s3/instances-s3.selectors";
import {
  s3EditorClosed,
  s3EditorContentLoaded,
  s3EditorSaveRequested,
} from "../../../state/instances-s3/instances-s3.actions";
import { displayFailure } from "../../../state/shared/shared.actions";
import { AuthStore } from "../../../shared/auth/auth.store";

describe("InstanceS3BrowserPageComponent", () => {
  let routeMock: { snapshot: { paramMap: any } };
  let instancesApiMock: jasmine.SpyObj<InstancesService>;
  let snackBarMock: jasmine.SpyObj<MatSnackBar>;
  let mockStore: MockStore;
  let authState: InstanceType<typeof AuthStore>;
  let actionsSubject: Subject<Action>;

  const s3InitialState = {
    instancesS3: {
      instanceName: "",
      bucketName: "",
      currentPath: "/",
      entries: [],
      knownFolderPaths: ["/"],
      pageLoading: false,
      editorOpen: false,
      editorLoading: false,
      editorFileName: "",
      editorFilePath: "",
    },
  };

  beforeEach(async () => {
    routeMock = {
      snapshot: { paramMap: convertToParamMap({ id: "7" }) },
    };
    instancesApiMock = jasmine.createSpyObj<InstancesService>("InstancesService", [
      "getInstanceApiInstancesInstanceIdGet",
      "getInstanceS3ApiInstancesInstanceIdS3Get",
      "listInstanceS3ApiInstancesInstanceIdS3ListGet",
      "getInstanceS3ContentApiInstancesInstanceIdS3ContentGet",
      "getInstanceS3ContentRawApiInstancesInstanceIdS3ContentRawGet",
      "putInstanceS3ContentApiInstancesInstanceIdS3ContentPut",
    ]);
    snackBarMock = jasmine.createSpyObj<MatSnackBar>("MatSnackBar", ["open"]);
    actionsSubject = new Subject<Action>();

    instancesApiMock.getInstanceApiInstancesInstanceIdGet.and.returnValue(
      of({ name: "node-7" } as any),
    );
    instancesApiMock.getInstanceS3ApiInstancesInstanceIdS3Get.and.returnValue(
      of({ bucket: "bkt" } as any),
    );
    instancesApiMock.listInstanceS3ApiInstancesInstanceIdS3ListGet.and.returnValue(
      of({ entries: [] } as any),
    );
    instancesApiMock.getInstanceS3ContentApiInstancesInstanceIdS3ContentGet.and.returnValue(
      of({ content: "x=1" } as any),
    );
    instancesApiMock.getInstanceS3ContentRawApiInstancesInstanceIdS3ContentRawGet.and.returnValue(
      of({ body: new Blob(["x=1"], { type: "text/plain" }) } as any),
    );
    instancesApiMock.putInstanceS3ContentApiInstancesInstanceIdS3ContentPut.and.returnValue(
      of({} as any),
    );

    await TestBed.configureTestingModule({
      imports: [InstanceS3BrowserPageComponent],
      providers: [
        provideMockStore({ initialState: s3InitialState }),
        provideMockActions(() => actionsSubject),
        { provide: ActivatedRoute, useValue: routeMock },
        { provide: InstancesService, useValue: instancesApiMock },
        { provide: MatSnackBar, useValue: snackBarMock },
      ],
    }).compileComponents();

    mockStore = TestBed.inject(MockStore);
    authState = TestBed.inject(AuthStore);
    authState.setAuthenticatedUser({ name: "Member", role: "member", accessAllowed: true });
  });

  afterEach(() => {
    mockStore?.resetSelectors();
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    expect(fixture.componentInstance).toBeTruthy();
  });

  it("HasValidId_NumericRouteIdProvided_ReturnsTrue", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    expect(fixture.componentInstance.hasValidId()).toBeTrue();
  });

  it("HasValidId_NonNumericRouteIdProvided_ReturnsFalse", () => {
    routeMock.snapshot.paramMap = convertToParamMap({ id: "abc" });
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    expect(fixture.componentInstance.hasValidId()).toBeFalse();
  });

  it("Breadcrumbs_ComponentInitialized_ShowsRootEntry", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    expect(component.breadcrumbs[0].label).toBe("root");
  });

  it("TreeNodes_KnownFolderPathsSet_BuildsHierarchyNodes", () => {
    mockStore.overrideSelector(selectS3KnownFolderPaths, ["/", "/models", "/models/resnet"]);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    const nodes = component.treeNodes;
    expect(nodes.some((n) => n.path === "/models")).toBeTrue();
    expect(nodes.some((n) => n.path === "/models/resnet")).toBeTrue();
  });

  it("FilteredEntries_CurrentPathAndQuerySet_ReturnsMatchingEntries", () => {
    mockStore.overrideSelector(selectS3CurrentPath, "/models");
    mockStore.overrideSelector(selectS3Entries, [
      { name: "resnet", path: "/models", type: "folder", modified: "" },
      { name: "config.pbtxt", path: "/models/resnet", type: "file", modified: "" },
      { name: "bert", path: "/models", type: "folder", modified: "" },
    ] as any);
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    component.query = "res";

    expect(component.filteredEntries.length).toBe(1);
    expect(component.filteredEntries[0].name).toBe("resnet");
  });

  it("IsEditableEntry_DifferentFileExtensions_ReturnsExpectedBoolean", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;

    expect(
      component.isEditableEntry({ name: "config.pbtxt", path: "/", type: "file", modified: "" }),
    ).toBeTrue();
    expect(
      component.isEditableEntry({ name: "model.py", path: "/", type: "file", modified: "" }),
    ).toBeTrue();
    expect(
      component.isEditableEntry({ name: "README.md", path: "/", type: "file", modified: "" }),
    ).toBeFalse();
  });

  it("OpenEntry_FolderEntryProvided_DispatchesNavigateToAction", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");

    component.openEntry({ name: "models", path: "/", type: "folder", modified: "" });
    expect(mockStore.dispatch).toHaveBeenCalled();
  });

  it("OpenEntry_FileEntryProvided_DispatchesEditorOpenAction", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");

    component.openEntry({ name: "model.py", path: "/", type: "file", modified: "" });
    expect(mockStore.dispatch).toHaveBeenCalled();
  });

  it("GetBracketFallbackError_InvalidPbtxtPatterns_ReturnsHelpfulSyntaxHints", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance as any;

    expect(component.getBracketFallbackError('model_config: { name: "x"')).toContain(
      "Fehlendes '}'",
    );
    expect(component.getBracketFallbackError("model_config: }")).toContain("Unerwartetes '}'");
    expect(component.getBracketFallbackError('name: "abc')).toContain(
      "Nicht geschlossene Zeichenkette",
    );
  });

  it("CloseEditor_Called_DispatchesEditorClosedAction", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");

    component.closeEditor();

    expect(mockStore.dispatch).toHaveBeenCalledWith(s3EditorClosed());
  });

  it("GoUp_PathIsRoot_DoesNotDispatch", () => {
    mockStore.overrideSelector(selectS3CurrentPath, "/");
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    const goToSpy = spyOn(component, "goTo");

    component.goUp();
    expect(goToSpy).not.toHaveBeenCalled();
  });

  it("GoUp_NestedPath_NavigatesToParent", () => {
    mockStore.overrideSelector(selectS3CurrentPath, "/models/resnet");
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    const goToSpy = spyOn(component, "goTo");

    component.goUp();
    expect(goToSpy).toHaveBeenCalledWith("/models");
  });

  it("SaveAndCloseEditor_ValidContent_DispatchesSaveAction", () => {
    mockStore.overrideSelector(selectS3EditorFilePath, "/models/config.pbtxt");
    mockStore.overrideSelector(selectS3EditorFileName, "config.pbtxt");
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance as any;
    component.editorContent = 'name: "x"';
    spyOn(component, "getPbtxtSyntaxError").and.returnValue(null);
    spyOn(mockStore, "dispatch");

    component.saveAndCloseEditor();

    expect(mockStore.dispatch).toHaveBeenCalledWith(
      jasmine.objectContaining({ type: s3EditorSaveRequested.type }),
    );
  });

  it("SaveAndCloseEditor_InvalidPbtxtDetected_DispatchesDisplayFailureAndSkipsSave", () => {
    mockStore.overrideSelector(selectS3EditorFilePath, "/models/config.pbtxt");
    mockStore.overrideSelector(selectS3EditorFileName, "config.pbtxt");
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance as any;
    spyOn(component, "getPbtxtSyntaxError").and.returnValue("invalid pbtxt");
    spyOn(mockStore, "dispatch");

    component.saveAndCloseEditor();

    expect(mockStore.dispatch).toHaveBeenCalledWith(
      displayFailure({ title: "Syntax error", message: "invalid pbtxt" }),
    );
    expect(mockStore.dispatch).not.toHaveBeenCalledWith(
      jasmine.objectContaining({ type: s3EditorSaveRequested.type }),
    );
  });

  it("StartEdit_FileEntryProvided_DispatchesEditorOpenAction", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    spyOn(mockStore, "dispatch");

    component.startEdit({ name: "model.py", path: "/", type: "file", modified: "" });

    expect(mockStore.dispatch).toHaveBeenCalled();
  });

  it("S3EditorContentLoaded_ActionDispatched_UpdatesEditorContent", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;

    actionsSubject.next(s3EditorContentLoaded({ content: 'name: "test"' }));

    expect(component.editorContent).toBe('name: "test"');
  });

  it("HelperMethods_PathUtilitiesCalled_ReturnExpectedValues", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance as any;

    expect(component.detectLanguage("model.py")).toBe("python");
    expect(component.detectLanguage("x.unknown")).toBe("plaintext");
    expect(component.normalizePath("//a///b/")).toBe("/a/b");
    expect(component.joinPath("/a", "b")).toBe("/a/b");
  });

  it("EditorHelpers_MonacoMarkersAndInitCalled_ReturnSyntaxErrorAndSetValue", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance as any;
    const model = { uri: "file://model" };
    component.editorInstance = { getModel: () => model };
    (globalThis as any).monaco = {
      editor: {
        getModelMarkers: () => [
          { severity: 8, startLineNumber: 3, startColumn: 2, message: "bad syntax" },
        ],
      },
      MarkerSeverity: { Error: 8 },
    };

    const msg = component.getMonacoSyntaxError();
    expect(msg).toContain("Zeile 3");

    const editorMock = jasmine.createSpyObj("editor", ["setValue"]);
    component.editorContent = "abc";
    component.onEditorInit(editorMock);
    expect(editorMock.setValue).toHaveBeenCalledWith("abc");
  });

  it("Download_FileOrFolderEntryProvided_FetchesContentAndClicksOnlyForFiles", async () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    spyOn(URL, "createObjectURL").and.returnValue("blob:demo");
    spyOn(URL, "revokeObjectURL");
    const clickSpy = spyOn(HTMLAnchorElement.prototype, "click");

    await component.download({ name: "a.py", path: "/", type: "file", modified: "" });

    expect(
      instancesApiMock.getInstanceS3ContentRawApiInstancesInstanceIdS3ContentRawGet,
    ).toHaveBeenCalled();
    const rawCallArgs =
      instancesApiMock.getInstanceS3ContentRawApiInstancesInstanceIdS3ContentRawGet.calls.mostRecent()
        .args;
    expect(rawCallArgs[0]).toBe("7");
    expect(rawCallArgs[1]).toBe("/a.py");
    expect(String(rawCallArgs[2])).toBe("response");
    expect(clickSpy).toHaveBeenCalled();

    clickSpy.calls.reset();
    instancesApiMock.getInstanceS3ContentRawApiInstancesInstanceIdS3ContentRawGet.calls.reset();
    await component.download({ name: "dir", path: "/", type: "folder", modified: "" });
    expect(clickSpy).not.toHaveBeenCalled();
    expect(
      instancesApiMock.getInstanceS3ContentRawApiInstancesInstanceIdS3ContentRawGet,
    ).not.toHaveBeenCalled();
  });

  it("Download_ContentRequestFails_DispatchesFailureToast", async () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    instancesApiMock.getInstanceS3ContentRawApiInstancesInstanceIdS3ContentRawGet.and.returnValue(
      throwError(() => new Error("down")),
    );
    const dispatchSpy = spyOn(mockStore, "dispatch");

    await component.download({ name: "a.py", path: "/", type: "file", modified: "" });

    expect(dispatchSpy).toHaveBeenCalledWith(
      displayFailure({ title: "Download failed", message: "Failed to download a.py." }),
    );
  });

  it("OnUpload_FileSelected_SetsReadingStateAndDispatchesUploadAfterRead", async () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    const file = new File(["x=1"], "model.py", { type: "text/plain" });
    const input = document.createElement("input");
    Object.defineProperty(input, "files", { value: [file] });
    const dispatchSpy = spyOn(mockStore, "dispatch");

    component.onUpload({ target: input } as unknown as Event);

    expect(component.readingUploadFile).toBeTrue();
    expect(component.readingUploadFileName).toBe("model.py");

    await new Promise<void>((resolve) => {
      const check = () => {
        if (dispatchSpy.calls.count() > 0) {
          resolve();
          return;
        }
        setTimeout(check, 0);
      };
      check();
    });

    const uploadAction = dispatchSpy.calls
      .allArgs()
      .map((args) => args[0] as { fileName?: string; content?: unknown })
      .find((action) => action?.fileName === "model.py");
    expect(uploadAction).toBeDefined();
    expect(uploadAction?.content instanceof ArrayBuffer).toBeTrue();
    const uploadedText = new TextDecoder().decode(
      new Uint8Array(uploadAction?.content as ArrayBuffer),
    );
    expect(uploadedText).toBe("x=1");
    expect(component.readingUploadFile).toBeFalse();
    expect(component.readingUploadFileName).toBe("");
  });

  it("OnUploadFolder_FolderSelected_UploadsAllFilesWithRelativePaths", async () => {
    mockStore.overrideSelector(selectS3CurrentPath, "/models");
    mockStore.refreshState();
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;
    const config = new File(['name: "demo"'], "config.pbtxt", { type: "text/plain" });
    const weights = new File(["weights"], "model.plan", { type: "application/octet-stream" });
    Object.defineProperty(config, "webkitRelativePath", {
      value: "resnet/config.pbtxt",
    });
    Object.defineProperty(weights, "webkitRelativePath", {
      value: "resnet/1/model.plan",
    });
    const input = document.createElement("input");
    Object.defineProperty(input, "files", { value: [config, weights] });
    const dispatchSpy = spyOn(mockStore, "dispatch");

    await component.onUploadFolder({ target: input } as unknown as Event);

    expect(
      instancesApiMock.putInstanceS3ContentApiInstancesInstanceIdS3ContentPut,
    ).toHaveBeenCalledTimes(2);
    const uploadCalls =
      instancesApiMock.putInstanceS3ContentApiInstancesInstanceIdS3ContentPut.calls.allArgs();
    expect(uploadCalls[0][1]).toBe("/models/resnet/config.pbtxt");
    expect(uploadCalls[1][1]).toBe("/models/resnet/1/model.plan");
    expect(component.uploadFolderInProgress).toBeFalse();
    expect(component.uploadFolderCompleted).toBe(2);
    expect(dispatchSpy).toHaveBeenCalledWith(
      jasmine.objectContaining({ type: "[Instances S3] Navigate To", path: "/models" }),
    );
  });

  it("IsActivePath_CurrentPathIsRoot_ReturnsTrue", () => {
    const fixture = TestBed.createComponent(InstanceS3BrowserPageComponent);
    const component = fixture.componentInstance;

    expect(component.isActivePath("/")).toBeTrue();
    expect(component.isActivePath("/models")).toBeFalse();
  });
});
