/* eslint-disable @typescript-eslint/no-explicit-any */
import { dashboardReducer } from "./dashboard.reducer";
import {
  dashboardPageOpened,
  dashboardReset,
  dashboardDataLoaded,
  dashboardDataLoadFailed,
} from "./dashboard.actions";

const INITIAL = dashboardReducer(undefined, { type: "__INIT__" } as any);

describe("dashboardReducer", () => {
  it("UnknownAction_DefaultState_ReturnsInitialState", () => {
    const state = dashboardReducer(undefined, { type: "__UNKNOWN__" } as any);
    expect(state.loading).toBeFalse();
    expect(state.instances).toEqual([]);
  });

  it("DashboardPageOpened_DefaultState_SetsLoadingAndResetsData", () => {
    const state = dashboardReducer(
      {
        ...INITIAL,
        instances: [{ id: "1", name: "n", models: 1, url: "http://x", createdAt: "" }],
      },
      dashboardPageOpened(),
    );
    expect(state.loading).toBeTrue();
    expect(state.instances).toEqual([]);
    expect(state.alerts).toEqual([]);
  });

  it("DashboardReset_DefaultState_SetsLoadingAndResetsData", () => {
    const state = dashboardReducer(
      {
        ...INITIAL,
        instances: [{ id: "1", name: "n", models: 1, url: "http://x", createdAt: "" }],
      },
      dashboardReset(),
    );
    expect(state.loading).toBeTrue();
    expect(state.instances).toEqual([]);
  });

  it("DashboardDataLoaded_LoadingState_SetsDataAndClearsLoading", () => {
    const stats = [
      { icon: "dns", label: "Configured Instances", value: 2, trend: "", tone: "teal" as const },
    ];
    const instances = [
      { id: "1", name: "node-1", models: 1, url: "http://n1", createdAt: "2024-01-01" },
    ];
    const alerts = [{ icon: "warning", label: "node-2 down", tone: "down" as const }];

    const state = dashboardReducer(
      { ...INITIAL, loading: true },
      dashboardDataLoaded({ instances, alerts, stats }),
    );
    expect(state.loading).toBeFalse();
    expect(state.instances.length).toBe(1);
    expect(state.alerts.length).toBe(1);
    expect(state.stats[0].value).toBe(2);
  });

  it("DashboardDataLoadFailed_LoadingState_ClearsLoadingAndResetsData", () => {
    const state = dashboardReducer(
      {
        ...INITIAL,
        loading: true,
        instances: [{ id: "1", name: "n", models: 1, url: "http://x", createdAt: "" }],
      },
      dashboardDataLoadFailed(),
    );
    expect(state.loading).toBeFalse();
    expect(state.instances).toEqual([]);
    expect(state.alerts).toEqual([]);
  });
});
