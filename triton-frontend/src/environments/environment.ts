export const environment = {
  production: false,
  appVersion: "v1.0.0",

  // Use IPv4 explicitly on macOS to avoid localhost (::1) hitting gvproxy/docker port forwards.
  apiBaseUrl: "http://127.0.0.1:8000",
  instancePollingIntervalMs: 10000,
  deploymentLogPollingIntervalMs: 5000,
  inferenceRequestTimeoutMs: 120000,
};
