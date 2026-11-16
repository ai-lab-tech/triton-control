import { provideHttpClient } from "@angular/common/http";
import { HttpTestingController, provideHttpClientTesting } from "@angular/common/http/testing";
import { TestBed } from "@angular/core/testing";
import { provideRouter } from "@angular/router";
import { AuthStore } from "../../shared/auth/auth.store";
import { HandoffNotificationsComponent } from "./handoff-notifications.component";

describe("HandoffNotificationsComponent", () => {
  let http: HttpTestingController;
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HandoffNotificationsComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());
  it("counts unread notifications and marks a recipient notification read", async () => {
    TestBed.inject(AuthStore).setAuthenticatedUser({
      name: "Engineer",
      email: "engineer@test.invalid",
      role: "member",
    });
    const fixture = TestBed.createComponent(HandoffNotificationsComponent);
    const page = fixture.componentInstance;
    const loading = page.refresh();
    const item = {
      id: "n1",
      handoff_id: "h1",
      message: "A model handoff is ready for engineering review.",
      created_at: "2026-11-15T12:00:00Z",
      read_at: null,
    };
    http.expectOne("/api/preparation/notifications?limit=100").flush([item]);
    await loading;
    expect(page.unread()).toBe(1);
    const reading = page.markRead(item);
    const request = http.expectOne("/api/preparation/notifications/n1/read");
    expect(request.request.method).toBe("PUT");
    request.flush({ ...item, read_at: "2026-11-15T12:01:00Z" });
    await reading;
    expect(page.unread()).toBe(0);
    fixture.destroy();
  });
  it("does not fetch notifications for viewers", async () => {
    TestBed.inject(AuthStore).setAuthenticatedUser({ name: "Viewer", role: "viewer" });
    const fixture = TestBed.createComponent(HandoffNotificationsComponent);
    await fixture.componentInstance.refresh();
    expect(fixture.componentInstance.allowed()).toBeFalse();
    fixture.destroy();
  });
});
