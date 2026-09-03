import { APP_ROUTES } from "./app.routes";

describe("APP_ROUTES", () => {
  it("RoutesConfiguration_LoginAndWildcardExist_DefinesExpectedPaths", () => {
    // Arrange
    const paths = APP_ROUTES.map((route) => route.path);

    // Act
    const hasLogin = paths.includes("signin");
    const hasForgotPassword = paths.includes("forgot-password");
    const hasActivation = paths.includes("activate-account");
    const hasReset = paths.includes("reset-password");
    const hasWildcard = paths.includes("**");

    // Assert
    expect(hasLogin).toBeTrue();
    expect(hasForgotPassword).toBeTrue();
    expect(hasActivation).toBeTrue();
    expect(hasReset).toBeTrue();
    expect(hasWildcard).toBeTrue();
  });

  it("RoutesConfiguration_ShellRouteDefined_ContainsExpectedChildPaths", () => {
    // Arrange
    const shellRoute = APP_ROUTES.find((route) => route.path === "");

    // Act
    const childPaths = (shellRoute?.children ?? []).map((child) => child.path);

    // Assert
    expect(shellRoute).toBeDefined();
    expect(childPaths).toContain("dashboard");
    expect(childPaths).toContain("instances");
    expect(childPaths).toContain("instances/:id/models/:modelName/versions/:version/profile");
    expect(childPaths).toContain("deployments/new");
    expect(childPaths).toContain("perf-analyzers");
    expect(childPaths).toContain("users");
    expect(childPaths).toContain("settings");
  });
});
