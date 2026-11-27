import { HttpErrorResponse } from "@angular/common/http";
import { TestBed } from "@angular/core/testing";
import { MAT_DIALOG_DATA, MatDialogRef } from "@angular/material/dialog";
import { of, throwError } from "rxjs";
import { AccessGroupsApiService } from "./access-groups-api.service";
import { AccessGroupsDialogComponent } from "./access-groups-dialog.component";

describe("AccessGroupsDialogComponent", () => {
  it("preserves an edited grant when the server rejects a stale revision", async () => {
    // Arrange
    const group = { id: "vision", name: "Vision", revision: 2, user_ids: [1], instance_ids: [3] };
    const api = jasmine.createSpyObj<AccessGroupsApiService>("AccessGroupsApiService", [
      "list",
      "instances",
      "save",
    ]);
    api.list.and.returnValue(of([group]));
    api.instances.and.returnValue(of([{ id: 3, name: "Triton" }]));
    api.save.and.returnValue(
      throwError(
        () =>
          new HttpErrorResponse({
            status: 409,
            error: { detail: "Access group changed. Reload it before editing." },
          }),
      ),
    );
    TestBed.configureTestingModule({
      imports: [AccessGroupsDialogComponent],
      providers: [
        { provide: AccessGroupsApiService, useValue: api },
        { provide: MAT_DIALOG_DATA, useValue: { users: [], groupId: group.id } },
        { provide: MatDialogRef, useValue: {} },
      ],
    });
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
  });
});
