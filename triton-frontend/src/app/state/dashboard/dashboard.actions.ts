import { createAction, props } from "@ngrx/store";
import { type StatCard, type InstanceRow, type AlertItem } from "./dashboard.reducer";

export const dashboardPageOpened = createAction("[Dashboard] Page Opened");

export const dashboardRefreshRequested = createAction("[Dashboard] Refresh Requested");

export const dashboardDataLoaded = createAction(
  "[Dashboard] Data Loaded",
  props<{ instances: InstanceRow[]; alerts: AlertItem[]; stats: StatCard[] }>(),
);

export const dashboardDataLoadFailed = createAction("[Dashboard] Data Load Failed");

export const dashboardReset = createAction("[Dashboard] Reset");
