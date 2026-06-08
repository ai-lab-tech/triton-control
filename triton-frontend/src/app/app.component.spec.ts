import { TestBed } from "@angular/core/testing";
import { AppComponent } from "./app.component";
import { AuthService } from "./shared/auth/auth.service";

describe("AppComponent", () => {
  let authServiceMock: jasmine.SpyObj<AuthService>;

  beforeEach(async () => {
    authServiceMock = jasmine.createSpyObj<AuthService>("AuthService", ["init"]);
    authServiceMock.init.and.resolveTo();

    await TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [{ provide: AuthService, useValue: authServiceMock }],
    }).compileComponents();
  });

  it("NgOnInit_AuthInitResolves_CallsAuthInitialization", async () => {
    // Arrange
    const fixture = TestBed.createComponent(AppComponent);
    const component = fixture.componentInstance;

    // Act
    component.ngOnInit();
    await Promise.resolve();

    // Assert
    expect(authServiceMock.init).toHaveBeenCalled();
  });

  it("NgOnInit_AuthInitRejects_LogsInitializationError", async () => {
    // Arrange
    const fixture = TestBed.createComponent(AppComponent);
    const component = fixture.componentInstance;
    authServiceMock.init.and.rejectWith(new Error("boom"));
    const errorSpy = spyOn(console, "error");

    // Act
    component.ngOnInit();
    await Promise.resolve();
    await Promise.resolve();

    // Assert
    expect(errorSpy).toHaveBeenCalled();
  });
});
