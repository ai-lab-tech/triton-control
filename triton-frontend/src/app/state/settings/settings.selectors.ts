import { settingsFeature } from "./settings.reducer";

export const {
  selectLoading: selectSettingsLoading,
  selectSaving: selectSettingsSaving,
  selectMessage: selectSettingsMessage,
  selectMessageTone: selectSettingsMessageTone,
} = settingsFeature;
