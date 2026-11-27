import { HttpErrorResponse } from "@angular/common/http";
import { TestBed } from "@angular/core/testing";
import { MAT_DIALOG_DATA, MatDialogRef } from "@angular/material/dialog";
import { of, throwError } from "rxjs";
import { AccessGroupsApiService } from "./access-groups-api.service";
import { AccessGroupsDialogComponent } from "./access-groups-dialog.component";

describe("AccessGroupsDialogComponent", () => {
  const group = { id: "vision", name: "Vision", revision: 2, user_ids: [1], instance_ids: [3] };
  let api: jasmine.SpyObj<AccessGroupsApiService>;
  let dialog: jasmine.SpyObj<MatDialogRef<AccessGroupsDialogComponent>>;

  beforeEach(() => {
    api = jasmine.createSpyObj<AccessGroupsApiService>("AccessGroupsApiService", [
      "list",
      "instances",
      "save",
    ]);
    api.list.and.returnValue(of([group]));
    api.instances.and.returnValue(of([{ id: 3, name: "Triton" }]));
    dialog = jasmine.createSpyObj("MatDialogRef", ["close"]);
    TestBed.configureTestingModule({
      imports: [AccessGroupsDialogComponent],
      providers: [
        { provide: AccessGroupsApiService, useValue: api },
        { provide: MAT_DIALOG_DATA, useValue: { users: [], groupId: group.id } },
        { provide: MatDialogRef, useValue: dialog },
      ],
    });
  });

  it("preserves an edited grant when the server rejects a stale revision", async () => {
    // Arrange
    api.save.and.returnValue(
      throwError(
        () =>
          new HttpErrorResponse({
            status: 409,
            error: { detail: "Access group changed. Reload it before editing." },
          }),
      ),
    );
    const fixture = TestBed.createComponent(AccessGroupsDialogComponent);
    await fixture.whenStable();
    const component = fixture.componentInstance;
    component.instanceIds = [];

    // Act
    await component.save();

    // Assert
    expect(api.save).toHaveBeenCalledWith(
      { name: "Vision", user_ids: [1], instance_ids: [] },
      group,
    );
    expect(component.instanceIds).toEqual([]);
    expect(component.groups()).toEqual([group]);
    expect(component.error()).toContain("Reload it before editing.");
    expect(dialog.close).not.toHaveBeenCalled();
  });

  it("keeps edits on implicit close and offers a primary save action", async () => {
    // Arrange
    api.save.and.returnValue(of({ ...group, name: "Vision team", revision: 3 }));
    const fixture = TestBed.createComponent(AccessGroupsDialogComponent);
    await fixture.whenStable();
    const component = fixture.componentInstance;
    expect(component.canClose(undefined)).toBeTrue();

    // Act
    component.name = "Vision team";
    fixture.detectChanges();

    // Assert
    const footer = fixture.nativeElement.querySelector("mat-dialog-actions") as HTMLElement;
    expect(footer.textContent).toContain("Discard changes");
    expect(footer.textContent).not.toContain("Done");
    expect(component.canClose(undefined)).toBeFalse();
    expect(component.canClose(false)).toBeTrue();
    const save = [...footer.querySelectorAll("button")].find(
      (button) => button.textContent?.trim() === "Save changes",
    )!;
    expect(save.disabled).toBeFalse();
    save.click();
    await fixture.whenStable();
    expect(dialog.close).toHaveBeenCalledOnceWith(true);
    expect(api.save).toHaveBeenCalledOnceWith(
      { name: "Vision team", user_ids: [1], instance_ids: [3] },
      group,
    );
  });
});
