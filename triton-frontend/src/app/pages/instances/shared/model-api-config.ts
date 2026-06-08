import { WritableSignal } from "@angular/core";

export interface ModelApiConfigToggleState {
  open: WritableSignal<boolean>;
  loading: WritableSignal<boolean>;
  json: WritableSignal<string>;
  error: WritableSignal<string>;
}

export interface ToggleModelApiConfigParams {
  hasValidRoute: boolean;
  state: ModelApiConfigToggleState;
  loadConfig: () => Promise<unknown>;
  errorMessage?: string;
}

export async function toggleModelApiConfig(params: ToggleModelApiConfigParams): Promise<void> {
  const { hasValidRoute, state, loadConfig, errorMessage = "Failed to load API config." } = params;

  if (!hasValidRoute) {
    return;
  }

  if (state.open()) {
    state.open.set(false);
    return;
  }

  state.open.set(true);
  if (state.json() || state.loading()) {
    return;
  }

  state.loading.set(true);
  state.error.set("");

  try {
    const config = await loadConfig();
    state.json.set(JSON.stringify(config, null, 2));
  } catch {
    state.error.set(errorMessage);
  } finally {
    state.loading.set(false);
  }
}
