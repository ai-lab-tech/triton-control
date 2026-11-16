import { provideHttpClient } from "@angular/common/http";
import { HttpTestingController, provideHttpClientTesting } from "@angular/common/http/testing";
import { TestBed } from "@angular/core/testing";
import { ActivatedRoute, convertToParamMap, provideRouter, Router } from "@angular/router";
import { of } from "rxjs";
import { AuthStore } from "../../shared/auth/auth.store";
import { HandoffPageComponent } from "./handoff-page.component";

const receipt = {
  id: "receipt-1",
  task: {
    id: "task-1",
    state: "queued",
    missing_inputs: ["evaluation_dataset"],
    blocked_reasons: [],
  },
  request: { title: "Iris", revision: "2", previous_handoff_id: "previous-receipt" },
  source: { key: "iris.onnx", version_id: "v1", file: { digest: "sha256:abc", size: 5 } },
  dataset: null,
  baseline: null,
  created_at: "2026-11-15T12:00:00Z",
};

describe("HandoffPageComponent", () => {
  let http: HttpTestingController;
  async function setup(id: string | null = null, role = "member") {
    await TestBed.configureTestingModule({
      imports: [HandoffPageComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            paramMap: of(convertToParamMap(id ? { handoffId: id } : {})),
            snapshot: { queryParamMap: convertToParamMap({}) },
          },
        },
      ],
    }).compileComponents();
    TestBed.inject(AuthStore).setAuthenticatedUser({
      name: "Scientist",
      email: "scientist@test.invalid",
      role,
    });
    http = TestBed.inject(HttpTestingController);
    return TestBed.createComponent(HandoffPageComponent);
  }
  afterEach(() => http.verify());

  it("shows the source form with no team picker", async () => {
    const fixture = await setup();
    http
      .expectOne("/api/s3-profiles")
      .flush([{ id: 1, name: "Lab", bucket: "models", prefix: "test" }]);
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("Submit model for preparation");
    expect(fixture.nativeElement.textContent).toContain("installation’s engineering group");
    expect(fixture.nativeElement.querySelector('[name="team"]')).toBeNull();
  });
  it("submits explicit inputs and keeps the same identity after a retry", async () => {
    const fixture = await setup();
    http
      .expectOne("/api/s3-profiles")
      .flush([{ id: 1, name: "Lab", bucket: "models", prefix: "" }]);
    const page = fixture.componentInstance;
    page.title = "Iris";
    page.profileId = 1;
    page.sourcePath = "iris.onnx";
    page.previousHandoffId = "previous-receipt";
    const navigate = spyOn(TestBed.inject(Router), "navigate").and.resolveTo(true);
    let pending = page.submit();
    const first = http.expectOne("/api/preparation/handoffs");
    const identity = first.request.body.origin;
    expect(first.request.body.dataset).toBeNull();
    expect(first.request.body.previous_handoff_id).toBe("previous-receipt");
    first.flush({ detail: "Please retry" }, { status: 503, statusText: "Unavailable" });
    await pending;
    expect(page.busy()).toBeFalse();
    pending = page.submit();
    const retry = http.expectOne("/api/preparation/handoffs");
    expect(retry.request.body.origin).toEqual(identity);
    expect(retry.request.body.revision).toBe("1");
    expect(retry.request.body.previous_handoff_id).toBe("previous-receipt");
    retry.flush(receipt);
    await pending;
    expect(navigate).toHaveBeenCalledWith(["/preparation/handoffs", "receipt-1"]);
  });
  it("renders missing inputs and the exact pinned object version", async () => {
    const fixture = await setup("receipt-1");
    http.expectOne("/api/preparation/handoffs/receipt-1").flush(receipt);
    await fixture.whenStable();
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain("Evaluation inputs incomplete");
    expect(text).toContain("Evaluation dataset");
    expect(text).toContain("v1");
    expect(text).toContain("task-1");
    expect(
      fixture.nativeElement.querySelector('a[href="/preparation/handoffs/previous-receipt"]'),
    ).not.toBeNull();
  });
  it("does not load source profiles or submit for viewers", async () => {
    const fixture = await setup(null, "viewer");
    await fixture.componentInstance.submit();
    fixture.detectChanges();
    http.expectNone("/api/s3-profiles");
    http.expectNone("/api/preparation/handoffs");
    expect(fixture.nativeElement.textContent).toContain("requires member or administrator access");
  });
  it("keeps access errors visible without rendering receipt content", async () => {
    const fixture = await setup("private");
    http
      .expectOne("/api/preparation/handoffs/private")
      .flush(
        { detail: "Preparation task access denied" },
        { status: 403, statusText: "Forbidden" },
      );
    await fixture.whenStable();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[role="alert"]').textContent).toContain(
      "access denied",
    );
    expect(fixture.componentInstance.receipt()).toBeNull();
  });
});
