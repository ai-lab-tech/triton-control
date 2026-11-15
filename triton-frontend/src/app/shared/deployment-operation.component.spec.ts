import { provideHttpClient } from "@angular/common/http";
import { HttpTestingController, provideHttpClientTesting } from "@angular/common/http/testing";
import { fakeAsync, TestBed, tick } from "@angular/core/testing";
import { DeploymentOperationComponent } from "./deployment-operation.component";
import { AuthStore } from "./auth/auth.store";

describe("DeploymentOperationComponent", () => {
  beforeEach(() =>
    TestBed.configureTestingModule({
      imports: [DeploymentOperationComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }),
  );

  it("reloads current status, distinguishes accepted work from readiness and supports retry/cancel", fakeAsync(() => {
    TestBed.inject(AuthStore).setAuthenticatedUser({ name: "Anna", role: "member" });
    const fixture = TestBed.createComponent(DeploymentOperationComponent);
    fixture.componentRef.setInput("instanceId", "7");
    fixture.detectChanges();
    const http = TestBed.inject(HttpTestingController);
    tick(0);
    http
      .expectOne("/api/deployments/7/operation")
      .flush({ id: "op", state: "pending", cancel_requested: false, message: "" });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("Queued");
    expect(fixture.nativeElement.textContent).not.toContain("Completed");
    tick(3000);
    http.expectOne("/api/deployments/7/operation").flush({
      id: "op",
      state: "pending",
      attempts: 1,
      cancel_requested: false,
      message: "Resources created; waiting for configured serving readiness",
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("In progress");
    tick(3000);
    http.expectOne("/api/deployments/7/operation").flush({
      id: "op",
      state: "failed",
      cancel_requested: false,
      message: "Readiness deadline exceeded",
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("Retry creation");
    void fixture.componentInstance.act("retry");
    http
      .expectOne("/api/operations/op/retry")
      .flush({ id: "op", state: "pending", cancel_requested: false });
    tick();
    void fixture.componentInstance.act("cancel");
    http
      .expectOne("/api/operations/op/cancel")
      .flush({ id: "op", state: "pending", cancel_requested: true });
    tick();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("Cancellation requested");
    tick(3000);
    http
      .expectOne("/api/deployments/7/operation")
      .flush(null, { status: 403, statusText: "Forbidden" });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector("[role=alert]")).toBeTruthy();
    fixture.destroy();
    http.verify();
  }));
  it("retains the last status, disables actions during an outage and recovers automatically", fakeAsync(() => {
    TestBed.inject(AuthStore).setAuthenticatedUser({ name: "Anna", role: "member" });
    const fixture = TestBed.createComponent(DeploymentOperationComponent);
    fixture.componentRef.setInput("instanceId", "7");
    fixture.detectChanges();
    const http = TestBed.inject(HttpTestingController);
    tick(0);
    http
      .expectOne("/api/deployments/7/operation")
      .flush({ id: "op", state: "running", cancel_requested: false });
    tick(3000);
    http
      .expectOne("/api/deployments/7/operation")
      .flush("<html>Bad Gateway</html>", { status: 502, statusText: "Bad Gateway" });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("In progress");
    expect(fixture.nativeElement.textContent).toContain("Last known state");
    expect(fixture.nativeElement.textContent).not.toContain("<html>");
    expect(fixture.nativeElement.querySelector("button").disabled).toBeTrue();
    void fixture.componentInstance.act("cancel");
    http.expectNone("/api/operations/op/cancel");
    tick(3000);
    http
      .expectOne("/api/deployments/7/operation")
      .flush({ id: "op", state: "succeeded", cancel_requested: false });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("Completed");
    expect(fixture.nativeElement.textContent).not.toContain("Last known state");
    expect(fixture.componentInstance.error()).toBe("");
    fixture.destroy();
    http.verify();
  }));
});
