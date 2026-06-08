/* eslint-disable @typescript-eslint/no-explicit-any */
import { selectDashboardFleetHealthPercentage } from "./dashboard.selectors";

describe("dashboardSelectors", () => {
  describe("selectDashboardFleetHealthPercentage", () => {
    function project(stats: any[], loading: boolean): number | null {
      return (selectDashboardFleetHealthPercentage.projector as any)(stats, loading);
    }

    it("Loading_ReturnsNull", () => {
      const result = project([], true);
      expect(result).toBeNull();
    });

    it("TotalIsZero_ReturnsNull", () => {
      const stats = [{ value: 0 }, { value: 0 }];
      const result = project(stats, false);
      expect(result).toBeNull();
    });

    it("TotalIsString_ReturnsNull", () => {
      const stats = [{ value: "Loading..." }, { value: "Loading..." }];
      const result = project(stats, false);
      expect(result).toBeNull();
    });

    it("HealthyIsString_ReturnsNull", () => {
      const stats = [{ value: 5 }, { value: "Loading..." }];
      const result = project(stats, false);
      expect(result).toBeNull();
    });

    it("ValidNumbers_ReturnsPercentageRounded", () => {
      const stats = [{ value: 4 }, { value: 3 }];
      const result = project(stats, false);
      expect(result).toBe(75);
    });

    it("AllHealthy_Returns100", () => {
      const stats = [{ value: 2 }, { value: 2 }];
      const result = project(stats, false);
      expect(result).toBe(100);
    });

    it("NoneHealthy_Returns0", () => {
      const stats = [{ value: 3 }, { value: 0 }];
      const result = project(stats, false);
      expect(result).toBe(0);
    });

    it("EmptyStats_ReturnsNull", () => {
      const result = project([], false);
      expect(result).toBeNull();
    });
  });
});
