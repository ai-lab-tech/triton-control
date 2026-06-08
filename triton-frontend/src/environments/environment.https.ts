export const environment = {
  production: false,
  appVersion: "v1.0.0",

  // HTTPS frontend mode should call the HTTPS backend to avoid mixed-content blocking.
  apiBaseUrl: "https://127.0.0.1:8000",
  instancePollingIntervalMs: 10000,
  deploymentLogPollingIntervalMs: 5000,
  inferenceRequestTimeoutMs: 120000,
};
