import { TestBed } from "@angular/core/testing";
import { MatDialogRef } from "@angular/material/dialog";
import { MockStore, provideMockStore } from "@ngrx/store/testing";
import { NewInstanceDialogComponent } from "./new-instance-dialog.component";

describe("NewInstanceDialogComponent", () => {
  let dialogRefMock: jasmine.SpyObj<MatDialogRef<NewInstanceDialogComponent>>;
  let mockStore: MockStore;

  beforeEach(async () => {
    dialogRefMock = jasmine.createSpyObj<MatDialogRef<NewInstanceDialogComponent>>("MatDialogRef", [
      "updateSize",
      "close",
    ]);

    await TestBed.configureTestingModule({
      imports: [NewInstanceDialogComponent],
      providers: [provideMockStore(), { provide: MatDialogRef, useValue: dialogRefMock }],
    }).compileComponents();

    mockStore = TestBed.inject(MockStore);
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = TestBed.createComponent(NewInstanceDialogComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
  });

  it("Close_MethodInvoked_ClosesDialogWithoutPayload", () => {
    // Arrange
    const fixture = TestBed.createComponent(NewInstanceDialogComponent);
    const component = fixture.componentInstance;

    // Act
    component.close();

    // Assert
    expect(dialogRefMock.close).toHaveBeenCalledWith();
  });

  it("Save_NameAndEndpointContainWhitespace_DispatchesCreateActionAndClosesDialog", () => {
    // Arrange
    const fixture = TestBed.createComponent(NewInstanceDialogComponent);
    const component = fixture.componentInstance;
    component.name = "  demo-instance  ";
    component.endpoint = "  http://localhost:8000  ";
    spyOn(mockStore, "dispatch");

    // Act
    component.save();

    // Assert
    expect(mockStore.dispatch).toHaveBeenCalledWith(
      jasmine.objectContaining({ url: "http://localhost:8000", name: "demo-instance" }),
    );
    expect(dialogRefMock.close).toHaveBeenCalledWith();
  });
});
