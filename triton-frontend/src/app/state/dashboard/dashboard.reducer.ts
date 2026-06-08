import { createFeature, createReducer, on } from "@ngrx/store";
import {
  dashboardDataLoadFailed,
  dashboardDataLoaded,
  dashboardPageOpened,
  dashboardReset,
} from "./dashboard.actions";

export const DASHBOARD_FEATURE_KEY = "dashboard";

export type StatTone = "teal" | "amber" | "sky" | "rose";

export interface StatCard {
  icon: string;
  label: string;
  value: string | number;
  trend: string;
  tone: StatTone;
}

export interface InstanceRow {
  id: string;
  name: string;
  models: number;
  url: string;
  createdAt: string;
}

export interface AlertItem {
  icon: string;
  label: string;
  tone: "warn" | "down";
}

export interface DashboardState {
  stats: StatCard[];
  instances: InstanceRow[];
  alerts: AlertItem[];
  loading: boolean;
}

const initialStats: StatCard[] = [
  { icon: "dns", label: "Configured Instances", value: "Loading...", trend: "", tone: "teal" },
  {
    icon: "verified",
    label: "Healthy Instances",
    value: "Loading...",
    trend: "live and ready Tritons",
    tone: "sky",
  },
  {
    icon: "shield",
    label: "Alerts",
    value: "Loading...",
    trend: "down Triton instances",
    tone: "rose",
  },
];

const initialState: DashboardState = {
  stats: initialStats,
  instances: [],
  alerts: [],
  loading: false,
};

export const dashboardReducer = createReducer(
  initialState,

  on(dashboardPageOpened, dashboardReset, (state) => ({
    ...state,
    loading: true,
    instances: [],
    alerts: [],
    stats: initialStats,
  })),

  on(dashboardDataLoaded, (state, { instances, alerts, stats }) => ({
    ...state,
    loading: false,
    instances,
    alerts,
    stats,
  })),

  on(dashboardDataLoadFailed, (state) => ({
    ...state,
    loading: false,
    instances: [],
    alerts: [],
    stats: initialStats,
  })),
);

export const dashboardFeature = createFeature({
  name: DASHBOARD_FEATURE_KEY,
  reducer: dashboardReducer,
});
