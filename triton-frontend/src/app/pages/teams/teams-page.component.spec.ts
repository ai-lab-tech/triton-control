import { TestBed } from "@angular/core/testing";
import { HttpErrorResponse } from "@angular/common/http";
import { of, Subject, throwError } from "rxjs";
import { Team, TeamMember, TeamsApiService } from "./teams-api.service";
import { TeamsPageComponent } from "./teams-page.component";

describe("TeamsPageComponent", () => {
  let api: jasmine.SpyObj<TeamsApiService>;
  const team: Team = {
    id: "team",
    name: "engineering",
    is_active: true,
    created_at: "2026-11-13T12:00:00Z",
  };
  const member: TeamMember = {
    user_id: 2,
    name: "Engineer",
    email: "engineer@example.test",
    role: "member",
    is_active: true,
    user_is_active: true,
    eligible: true,
    updated_at: "2026-11-13T12:00:00Z",
  };

  beforeEach(async () => {
    api = jasmine.createSpyObj<TeamsApiService>("TeamsApiService", [
      "list",
      "users",
      "create",
      "update",
      "members",
      "history",
      "setMember",
    ]);
    api.list.and.returnValue(of([team]));
    api.users.and.returnValue(
      of([{ id: 2, name: member.name, email: member.email, role: "member", is_active: true }]),
    );
    api.members.and.returnValue(of([]));
    api.history.and.returnValue(of([]));
    api.create.and.returnValue(of(team));
    api.update.and.returnValue(of(team));
    api.setMember.and.returnValue(of([]));
    await TestBed.configureTestingModule({
      imports: [TeamsPageComponent],
      providers: [{ provide: TeamsApiService, useValue: api }],
    }).compileComponents();
  });

  async function loaded() {
    const fixture = TestBed.createComponent(TeamsPageComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  it("shows engineering responsibility separately from resource access", async () => {
    // Arrange
    const fixture = await loaded();
    // Act
    const text = (fixture.nativeElement as HTMLElement).textContent ?? "";
    // Assert
    expect(text).toContain("engineering");
    expect(text).toContain("Membership does not change roles");
    expect(fixture.componentInstance.availableUsers().map((user) => user.id)).toEqual([2]);
  });

  it("creates a team and opens its membership details", async () => {
    // Arrange
    const fixture = await loaded();
    fixture.componentInstance.newName = "platform";
    const created = { ...team, id: "new", name: "platform" };
    api.create.and.returnValue(of(created));
    api.list.and.returnValue(of([team, created]));
    // Act
    await fixture.componentInstance.createTeam();
    // Assert
    expect(api.create).toHaveBeenCalledOnceWith({ name: "platform", is_active: true });
    expect(fixture.componentInstance.selected()?.id).toBe("new");
    expect(fixture.componentInstance.newName).toBe("");
  });

  it("keeps the proposed name and shows the server error after a conflict", async () => {
    // Arrange
    const fixture = await loaded();
    fixture.componentInstance.newName = "duplicate";
    api.create.and.returnValue(
      throwError(
        () => new HttpErrorResponse({ status: 409, error: { detail: "Team name already exists" } }),
      ),
    );
    // Act
    await fixture.componentInstance.createTeam();
    fixture.detectChanges();
    // Assert
    expect(fixture.componentInstance.newName).toBe("duplicate");
    expect(fixture.componentInstance.busy()).toBeFalse();
    expect(fixture.nativeElement.querySelector('[role="alert"]').textContent).toContain(
      "Team name already exists",
    );
  });

  it("prevents duplicate mutation requests while an update is pending", async () => {
    // Arrange
    const fixture = await loaded();
    const response = new Subject<Team>();
    api.update.and.returnValue(response);
    // Act
    const first = fixture.componentInstance.saveTeam();
    await fixture.componentInstance.saveTeam();
    await fixture.componentInstance.setMember(2, true);
    // Assert
    expect(api.update).toHaveBeenCalledTimes(1);
    expect(api.setMember).not.toHaveBeenCalled();
    expect(fixture.componentInstance.busy()).toBeTrue();
    response.next(team);
    response.complete();
    await first;
    expect(fixture.componentInstance.busy()).toBeFalse();
  });

  it("deactivates a team and prevents new membership requests", async () => {
    // Arrange
    const fixture = await loaded();
    fixture.componentInstance.draftActive = false;
    api.update.and.returnValue(of({ ...team, is_active: false }));
    // Act
    await fixture.componentInstance.saveTeam();
    await fixture.componentInstance.setMember(2, true);
    fixture.detectChanges();
    // Assert
    expect(api.update).toHaveBeenCalledWith(team.id, { name: team.name, is_active: false });
    expect(api.setMember).not.toHaveBeenCalled();
    const add = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll("button")).find(
      (button) => button.textContent?.trim() === "Add member",
    );
    expect(add?.disabled).toBeTrue();
  });

  it("revokes a membership while retaining its visible history", async () => {
    // Arrange
    api.members.and.returnValue(of([member]));
    const fixture = await loaded();
    api.members.and.returnValue(of([{ ...member, is_active: false, eligible: false }]));
    api.history.and.returnValue(
      of([
        {
          id: 1,
          actor_user_id: 1,
          subject_user_id: 2,
          action: "member_removed",
          changes: {},
          created_at: team.created_at,
        },
      ]),
    );
    // Act
    await fixture.componentInstance.setMember(2, false);
    fixture.detectChanges();
    // Assert
    expect(api.setMember).toHaveBeenCalledWith(team.id, 2, false);
    expect(fixture.componentInstance.availableUsers().map((user) => user.id)).toEqual([2]);
    const text = (fixture.nativeElement as HTMLElement).textContent ?? "";
    expect(text).toContain("Revoked");
    expect(text).toContain("Membership revoked: Engineer");
  });

  it("clears old members when loading a different team fails", async () => {
    // Arrange
    api.members.and.returnValue(of([member]));
    const fixture = await loaded();
    api.members.and.returnValue(throwError(() => ({ status: 403 })));
    // Act
    await fixture.componentInstance.selectTeam({ ...team, id: "unavailable" });
    // Assert
    expect(fixture.componentInstance.members()).toEqual([]);
    expect(fixture.componentInstance.history()).toEqual([]);
    expect(fixture.componentInstance.error()).not.toBe("");
    expect(fixture.componentInstance.busy()).toBeFalse();
  });
});
