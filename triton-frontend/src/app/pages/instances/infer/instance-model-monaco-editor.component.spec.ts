import { TestBed } from "@angular/core/testing";
import { InstanceModelMonacoEditorComponent } from "./instance-model-monaco-editor.component";

describe("InstanceModelMonacoEditorComponent", () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [InstanceModelMonacoEditorComponent],
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
