/* eslint-disable @typescript-eslint/no-explicit-any */
import { instancesInferReducer, initialInstancesInferState } from "./instances-infer.reducer";
import {
  inferResultHydrated,
  inferRequestStarted,
  inferRequestSucceeded,
  inferRequestFailed,
} from "./instances-infer.actions";

describe("instancesInferReducer", () => {
  it("UnknownAction_DefaultState_ReturnsInitialState", () => {
    const state = instancesInferReducer(undefined, { type: "__UNKNOWN__" } as any);
    expect(state).toEqual(initialInstancesInferState);
  });

  it("InferRequestStarted_DefaultState_SetsSubmittingAndResetsFields", () => {
    const state = instancesInferReducer(
      { ...initialInstancesInferState, responseJson: '{"old": 1}', error: "prev error" },
      inferRequestStarted({ payload: {}, instanceId: "1", modelName: "model", version: "1" }),
    );
    expect(state.submitting).toBeTrue();
    expect(state.responseJson).toBe("");
    expect(state.error).toBe("");
  });

  it("InferRequestSucceeded_SubmittingState_StoresJsonAndLatency", () => {
    const response = { outputs: [{ name: "output", data: [1] }] };
    const inferenceMetrics = {
      available: true,
      error: null,
      models: [
        {
          model: "model",
          version: "1",
          requestCount: 1,
          totalMs: 5,
          queueMs: 1,
          withoutQueueMs: 4,
          computeInputMs: 0.5,
          computeInferMs: 3,
          computeOutputMs: 0.5,
        },
      ],
    };
    const state = instancesInferReducer(
      { ...initialInstancesInferState, submitting: true },
      inferRequestSucceeded({ response, requestLatencyMs: 123, inferenceMetrics }),
    );
    expect(state.submitting).toBeFalse();
    expect(state.responseJson).toContain('"outputs"');
    expect(state.requestLatencyMs).toBe(123);
    expect(state.inferenceMetrics).toEqual(inferenceMetrics);
    expect(state.error).toBe("");
  });

  it("InferRequestFailed_SubmittingState_SetsErrorAndLatency", () => {
    const state = instancesInferReducer(
      { ...initialInstancesInferState, submitting: true },
      inferRequestFailed({ message: "timeout", requestLatencyMs: 5000 }),
    );
    expect(state.submitting).toBeFalse();
    expect(state.error).toBe("timeout");
    expect(state.responseJson).toBe("");
    expect(state.requestLatencyMs).toBe(5000);
  });

  it("InferResultHydrated_StoresPersistedResult", () => {
    const inferenceMetrics = {
      available: true,
      error: null,
      models: [],
    };
    const state = instancesInferReducer(
      initialInstancesInferState,
      inferResultHydrated({
        responseJson: '{"outputs":[]}',
        requestLatencyMs: 42,
        inferenceMetrics,
      }),
    );
    expect(state.submitting).toBeFalse();
    expect(state.error).toBe("");
    expect(state.responseJson).toBe('{"outputs":[]}');
    expect(state.requestLatencyMs).toBe(42);
    expect(state.inferenceMetrics).toEqual(inferenceMetrics);
  });
});
