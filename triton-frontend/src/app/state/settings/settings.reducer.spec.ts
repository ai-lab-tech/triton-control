import {
  oidcSettingsLoadFailed,
  oidcSettingsLoaded,
  oidcSettingsSaveFailed,
  oidcSettingsSaveRedirecting,
  oidcSettingsSaveRequested,
  oidcSettingsSaveSucceeded,
  settingsPageOpened,
} from "./settings.actions";
import { settingsFeature, SettingsState, settingsReducer } from "./settings.reducer";

describe("settingsReducer", () => {
  const initial = settingsReducer(undefined, { type: "@@init" } as never);

  it("SettingsPageOpened_DefaultState_SetsLoadingAndClearsMessage", () => {
    const next = settingsReducer(initial, settingsPageOpened());
    expect(next.loading).toBeTrue();
    expect(next.message).toBe("");
    expect(next.messageTone).toBe("info");
  });

  it("OidcSettingsLoaded_LoadInProgress_StopsLoading", () => {
    const next = settingsReducer(
      { ...initial, loading: true },
      oidcSettingsLoaded({ settings: {} as never }),
    );
    expect(next.loading).toBeFalse();
  });

  it("LoadFailed_ErrorReturned_SetsErrorMessageAndTone", () => {
    const next = settingsReducer(initial, oidcSettingsLoadFailed({ message: "load failed" }));
    expect(next.loading).toBeFalse();
    expect(next.message).toBe("load failed");
    expect(next.messageTone).toBe("error");
  });

  it("SaveRequested_PreviousMessageExists_SetsSavingAndResetsInfoMessage", () => {
    const next = settingsReducer(
      { ...initial, message: "old", messageTone: "error", saving: false },
      oidcSettingsSaveRequested({ settings: {} as never }),
    );
    expect(next.saving).toBeTrue();
    expect(next.message).toBe("");
    expect(next.messageTone).toBe("info");
  });

  it("SaveSucceeded_InProgress_ShowsSuccessMessageAndStopsSaving", () => {
    const next = settingsReducer(
      { ...initial, saving: true },
      oidcSettingsSaveSucceeded({ settings: {} as never }),
    );
    expect(next.saving).toBeFalse();
    expect(next.message).toBe("Settings saved.");
    expect(next.messageTone).toBe("success");
  });

  it("SaveRedirecting_PreflightRequired_ShowsRedirectNotice", () => {
    const next = settingsReducer({ ...initial, saving: true }, oidcSettingsSaveRedirecting());
    expect(next.saving).toBeFalse();
    expect(next.message).toContain("Redirecting");
    expect(next.messageTone).toBe("info");
  });

  it("SaveFailed_ApiError_ShowsErrorMessageAndStopsSaving", () => {
    const next = settingsReducer(
      { ...initial, saving: true },
      oidcSettingsSaveFailed({ message: "save failed" }),
    );
    expect(next.saving).toBeFalse();
    expect(next.message).toBe("save failed");
    expect(next.messageTone).toBe("error");
  });
});

describe("settingsSelectors", () => {
  it("FeatureSelectors_StateProvided_ReturnExpectedSlices", () => {
    const featureState: SettingsState = {
      loading: true,
      saving: true,
      message: "msg",
      messageTone: "success",
    };
    const root = { settings: featureState };

    expect(settingsFeature.selectLoading(root)).toBeTrue();
    expect(settingsFeature.selectSaving(root)).toBeTrue();
    expect(settingsFeature.selectMessage(root)).toBe("msg");
    expect(settingsFeature.selectMessageTone(root)).toBe("success");
  });
});
