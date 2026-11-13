import { provideHttpClient } from "@angular/common/http";
import { HttpTestingController, provideHttpClientTesting } from "@angular/common/http/testing";
import { TestBed } from "@angular/core/testing";
import { BASE_PATH } from "../../api/generated";
import { TeamsApiService } from "./teams-api.service";

describe("TeamsApiService", () => {
  let api: TeamsApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: BASE_PATH, useValue: "/control/" },
      ],
    });
    api = TestBed.inject(TeamsApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it("creates a team through the configured API base", () => {
    // Arrange
    const input = { name: "engineering", is_active: true };
    // Act
    api.create(input).subscribe();
    // Assert
    const request = http.expectOne("/control/api/teams");
    expect(request.request.method).toBe("POST");
    expect(request.request.body).toEqual(input);
    request.flush({ id: "team", ...input });
  });

  it("uses idempotent membership routes for add and revoke", () => {
    // Arrange
    const teamId = "team";
    // Act
    api.setMember(teamId, 7, true).subscribe();
    api.setMember(teamId, 8, false).subscribe();
    // Assert
    const add = http.expectOne("/control/api/teams/team/members/7");
    const revoke = http.expectOne("/control/api/teams/team/members/8");
    expect(add.request.method).toBe("PUT");
    expect(revoke.request.method).toBe("DELETE");
    add.flush([]);
    revoke.flush([]);
  });

  it("loads retained history and members without mutating them", () => {
    // Arrange
    const requests = ["/control/api/teams/team/events", "/control/api/teams/team/members"];
    // Act
    api.history("team").subscribe();
    api.members("team").subscribe();
    // Assert
    for (const url of requests) {
      const request = http.expectOne(url);
      expect(request.request.method).toBe("GET");
      request.flush([]);
    }
  });
});
