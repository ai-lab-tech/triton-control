/* eslint-disable @typescript-eslint/no-explicit-any */
import {
  profileLastResultLoadSkipped,
  profileLastResultLoadSucceeded,
  profilePageOpened,
  profileRunFailed,
  profileRunStarted,
  profileRunSucceeded,
} from "./instances-profile.actions";
import { initialInstancesProfileState, instancesProfileReducer } from "./instances-profile.reducer";

describe("instancesProfileReducer", () => {
  it("UnknownAction_ReturnsInitialState", () => {
    // Arrange / Act
    const state = instancesProfileReducer(undefined, { type: "__UNKNOWN__" } as any);

    // Assert
    expect(state).toEqual(initialInstancesProfileState);
  });

  it("ProfileRunStarted_SetsRunningAndClearsOutput", () => {
    // Arrange / Act
    const state = instancesProfileReducer(
      {
        running: false,
        activeKey: "7:resnet:1",
        activeRunKey: "",
        entries: {
          "7:resnet:1": {
            error: "old",
            output: "old output",
            command: ["old"],
          },
        },
      },
      profileRunStarted({
        key: "7:resnet:1",
        instanceId: "7",
        modelName: "resnet",
        version: "1",
        batchSize: 1,
        concurrencyRange: "1",
        measurementRequestCount: 50,
      }),
    );

    // Assert
    expect(state.running).toBeTrue();
    expect(state.activeKey).toBe("7:resnet:1");
    expect(state.activeRunKey).toBe("7:resnet:1");
    expect(state.entries["7:resnet:1"]).toEqual({ error: "", output: "", command: [] });
  });

  it("ProfileRunSucceeded_StoresCommandAndOutput", () => {
    // Arrange / Act
    const state = instancesProfileReducer(
      { ...initialInstancesProfileState, running: true, activeRunKey: "7:resnet:1" },
      profileRunSucceeded({ key: "7:resnet:1", command: ["perf_analyzer"], output: "done" }),
    );

    // Assert
    expect(state.running).toBeFalse();
    expect(state.entries["7:resnet:1"].command).toEqual(["perf_analyzer"]);
    expect(state.entries["7:resnet:1"].output).toBe("done");
  });

  it("ProfileRunFailed_StoresError", () => {
    // Arrange / Act
    const state = instancesProfileReducer(
      { ...initialInstancesProfileState, running: true, activeRunKey: "7:resnet:1" },
      profileRunFailed({ key: "7:resnet:1", message: "failed" }),
    );

    // Assert
    expect(state.running).toBeFalse();
    expect(state.entries["7:resnet:1"].error).toBe("failed");
  });

  it("ProfilePageOpened_SwitchesActiveEntryWithoutCopyingOutput", () => {
    // Arrange / Act
    const state = instancesProfileReducer(
      {
        ...initialInstancesProfileState,
        activeKey: "7:resnet:1",
        entries: {
          "7:resnet:1": { error: "", output: "node 7 output", command: ["perf_analyzer"] },
        },
      },
      profilePageOpened({ key: "8:resnet:1" }),
    );

    // Assert
    expect(state.activeKey).toBe("8:resnet:1");
    expect(state.entries["7:resnet:1"].output).toBe("node 7 output");
    expect(state.entries["8:resnet:1"]).toBeUndefined();
  });

  it("ProfileLastResultLoadSucceeded_StoresTargetSpecificResult", () => {
    // Arrange / Act
    const state = instancesProfileReducer(
      initialInstancesProfileState,
      profileLastResultLoadSucceeded({
        key: "8:resnet:1",
        batchSize: 2,
        concurrencyRange: "1:4:1",
        measurementRequestCount: 80,
        inputData: "{}",
        command: ["perf_analyzer"],
        output: "previous output",
      }),
    );

    // Assert
    expect(state.activeKey).toBe("8:resnet:1");
    expect(state.entries["8:resnet:1"]).toEqual({
      error: "",
      output: "previous output",
      command: ["perf_analyzer"],
      batchSize: 2,
      concurrencyRange: "1:4:1",
      measurementRequestCount: 80,
      inputData: "{}",
    });
  });

  it("ProfileLastResultLoadSkipped_ClearsUnknownTargetOutput", () => {
    // Arrange / Act
    const state = instancesProfileReducer(
      {
        ...initialInstancesProfileState,
        activeKey: "7:resnet:1",
        entries: {
          "7:resnet:1": { error: "", output: "node 7 output", command: ["perf_analyzer"] },
        },
      },
      profileLastResultLoadSkipped({ key: "8:resnet:1" }),
    );

    // Assert
    expect(state.activeKey).toBe("8:resnet:1");
    expect(state.entries["8:resnet:1"]).toEqual({ error: "", output: "", command: [] });
  });
});
