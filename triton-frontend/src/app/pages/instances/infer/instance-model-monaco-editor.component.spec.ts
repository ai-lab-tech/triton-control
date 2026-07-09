import { TestBed } from "@angular/core/testing";
import { MonacoEditorModule } from "ngx-monaco-editor-v2";
import { InstanceModelMonacoEditorComponent } from "./instance-model-monaco-editor.component";
import { NO_ERRORS_SCHEMA } from "@angular/core";

describe("InstanceModelMonacoEditorComponent", () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [InstanceModelMonacoEditorComponent, MonacoEditorModule],
      schemas: [NO_ERRORS_SCHEMA],
    }).compileComponents();
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelMonacoEditorComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
  });

  it("EditorOptions_ComponentInitialized_ReturnsJsonEditorDefaults", () => {
    // Arrange
    const fixture = TestBed.createComponent(InstanceModelMonacoEditorComponent);
    const component = fixture.componentInstance;

    // Act
    const options = component.editorOptions();

    // Assert
    expect(options.language).toBe("json");
    expect(options.theme).toBe("vs-dark");
    expect(options.minimap.enabled).toBeFalse();
  });
});
