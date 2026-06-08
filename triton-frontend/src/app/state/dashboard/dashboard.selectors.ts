import { createSelector } from "@ngrx/store";
import { dashboardFeature } from "./dashboard.reducer";

export const {
  selectStats: selectDashboardStats,
  selectInstances: selectDashboardInstances,
  selectAlerts: selectDashboardAlerts,
  selectLoading: selectDashboardLoading,
} = dashboardFeature;

export const selectDashboardFleetHealthPercentage = createSelector(
  selectDashboardStats,
  selectDashboardLoading,
  (stats, loading) => {
    if (loading) return null;
    const total = stats[0]?.value;
    const healthy = stats[1]?.value;
    if (typeof total !== "number" || total === 0) return null;
    if (typeof healthy !== "number") return null;
    return Math.round((healthy / total) * 100);
  },
);
