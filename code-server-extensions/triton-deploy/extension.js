const fs = require("fs");
const crypto = require("crypto");
const http = require("http");
const https = require("https");
const path = require("path");
const vscode = require("vscode");

function activate(context) {
  const disposable = vscode.commands.registerCommand(
    "tritonControl.deployModelRepository",
    async (resource) => {
      const source = await resolveSourceFolder(resource);
      if (!source) {
        return;
      }
      const initial = await initialFormValues(source);
      if (!initial) {
        return;
      }
      openDeployPanel(context, initial);
    },
  );
  context.subscriptions.push(disposable);
}

async function resolveSourceFolder(resource) {
  if (resource?.fsPath && fs.existsSync(resource.fsPath) && fs.statSync(resource.fsPath).isDirectory()) {
    return resource.fsPath;
  }
  const selection = await vscode.window.showOpenDialog({
    canSelectFiles: false,
    canSelectFolders: true,
    canSelectMany: false,
    openLabel: "Select Triton model repository",
  });
  return selection?.[0]?.fsPath || "";
}

function openDeployPanel(context, initial) {
  const panel = vscode.window.createWebviewPanel(
    "tritonControlDeploy",
    "Deploy Triton Model Repository",
    vscode.ViewColumn.One,
    { enableScripts: true },
  );
  const nonce = String(Date.now());
  panel.webview.html = renderHtml(panel.webview, nonce, initial);

  panel.webview.onDidReceiveMessage(
    async (message) => {
      if (message?.type !== "upload") {
        return;
      }
      try {
        const form = normalizeForm(message.form || {});
        validateForm(form);
        await saveS3Settings(form);
        await vscode.window.withProgress(
          {
            location: vscode.ProgressLocation.Notification,
            title: `Uploading ${path.basename(form.sourceFolder)} to S3`,
            cancellable: false,
          },
          async (progress) => {
            await uploadRepository(form.sourceFolder, form, (text, increment) => {
              progress.report({ message: text, increment });
              panel.webview.postMessage({ type: "progress", text });
            });
          },
        );
        panel.webview.postMessage({
          type: "createDeployment",
          payload: deploymentPayload(form),
        });
      } catch (error) {
        const messageText = error instanceof Error ? error.message : String(error);
        panel.webview.postMessage({ type: "error", text: messageText });
        vscode.window.showErrorMessage(messageText);
      }
    },
    undefined,
    context.subscriptions,
  );
}

async function initialFormValues(sourceFolder) {
  const cfg = vscode.workspace.getConfiguration("tritonControlDeploy");
  const s3x = vscode.workspace.getConfiguration("s3x");
  const detectedModelName = detectModelName(sourceFolder);
  const detectedBackend = detectModelBackend(sourceFolder);
  const modelName = detectedModelName || await promptForModelName(sourceFolder);
  if (!modelName) {
    return null;
  }
  const deploymentName = toKubernetesName(modelName);
  const endpoint = cfg.get("s3Endpoint") || s3x.get("endpointUrl") || process.env.AWS_ENDPOINT_URL || process.env.S3_ENDPOINT || "";
  const initial = {
    sourceFolder,
    deploymentName,
    image: cfg.get("tritonImage") || "nvcr.io/nvidia/tritonserver:25.02-py3",
    endpoint,
    bucket: cfg.get("s3Bucket") || process.env.S3_BUCKET || "",
    prefix: cfg.get("s3Prefix") || process.env.S3_PREFIX || "",
    accessKeyId: cfg.get("s3AccessKeyId") || s3x.get("accessKeyId") || process.env.AWS_ACCESS_KEY_ID || "",
    secretAccessKey: cfg.get("s3SecretAccessKey") || s3x.get("secretAccessKey") || process.env.AWS_SECRET_ACCESS_KEY || "",
    region: cfg.get("s3Region") || s3x.get("region") || process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || "us-east-1",
    forcePathStyle: effectiveForcePathStyle(
      endpoint,
      cfg.get("s3ForcePathStyle") !== false && s3x.get("forcePathStyle") !== false,
    ),
    s3CaCertificate: cfg.get("s3CaCertificate") || "",
    detectedBackend: backendLabel(detectedBackend),
    modelControlMode: "explicit",
    repositorySyncMode: detectedBackend === "vllm" ? "sidecar" : "direct",
    repositoryPollSecs: 15,
    modelName,
    profileId: "",
    profileName: "",
    cpu: "2",
    memory: "4Gi",
    gpuCount: "1",
  };
  return initial;
}

function detectModelName(sourceFolder) {
  const configPath = findConfigPbtxt(sourceFolder);
  if (!configPath) {
    return "";
  }
  try {
    const config = fs.readFileSync(configPath, "utf8");
    const match = config.match(/(?:^|\n)\s*name\s*:\s*"([^"]+)"/);
    if (match?.[1]) {
      return match[1].trim();
    }
  } catch {
    return "";
  }
  return path.basename(path.dirname(configPath));
}

function detectModelBackend(sourceFolder) {
  const configPath = findConfigPbtxt(sourceFolder);
  if (!configPath) return "";
  try {
    const config = fs.readFileSync(configPath, "utf8");
    return config.match(/(?:^|\n)\s*backend\s*:\s*"([^"]+)"/)?.[1]?.trim().toLowerCase() || "";
  } catch {
    return "";
  }
}

function backendLabel(value) {
  const backend = String(value || "").trim().toLowerCase();
  if (backend === "vllm") {
    return "vLLM";
  }
  return backend || "Triton";
}

function findConfigPbtxt(sourceFolder) {
  const direct = path.join(sourceFolder, "config.pbtxt");
  if (fs.existsSync(direct)) {
    return direct;
  }
  const entries = fs.readdirSync(sourceFolder, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isDirectory()) {
      continue;
    }
    const candidate = path.join(sourceFolder, entry.name, "config.pbtxt");
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return "";
}

async function promptForModelName(sourceFolder) {
  const fallback = path.basename(sourceFolder);
  const value = await vscode.window.showInputBox({
    title: "Triton Control Deploy",
    prompt: "Model name was not found in config.pbtxt. Enter the Triton model name.",
    value: fallback,
    ignoreFocusOut: true,
    validateInput: (input) => input.trim() ? undefined : "Model name is required.",
  });
  return value?.trim() || "";
}

function toKubernetesName(value) {
  return String(value || "triton-model")
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63)
    .replace(/-$/g, "") || "triton-model";
}

async function saveS3Settings(values) {
  const cfg = vscode.workspace.getConfiguration("tritonControlDeploy");
  const target = vscode.ConfigurationTarget.Global;
  const forcePathStyle = effectiveForcePathStyle(values.endpoint, values.forcePathStyle);
  await cfg.update("s3Endpoint", values.endpoint, target);
  await cfg.update("s3Bucket", values.bucket, target);
  await cfg.update("s3Prefix", values.prefix, target);
  await cfg.update("s3AccessKeyId", values.accessKeyId, target);
  await cfg.update("s3SecretAccessKey", values.secretAccessKey, target);
  await cfg.update("s3Region", values.region, target);
  await cfg.update("s3ForcePathStyle", forcePathStyle, target);
  await cfg.update("s3CaCertificate", values.s3CaCertificate || "", target);
  await cfg.update("tritonImage", values.image, target);
  await saveS3xSettingsIfAvailable(values, forcePathStyle, target);
}

async function saveS3xSettingsIfAvailable(values, forcePathStyle, target) {
  const s3x = vscode.workspace.getConfiguration("s3x");
  try {
    await s3x.update("endpointUrl", values.endpoint, target);
    await s3x.update("accessKeyId", values.accessKeyId, target);
    await s3x.update("secretAccessKey", values.secretAccessKey, target);
    await s3x.update("region", values.region, target);
    await s3x.update("forcePathStyle", forcePathStyle, target);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (!/not a registered configuration/i.test(message)) {
      throw error;
    }
    console.warn(`Skipping optional S3X settings sync: ${message}`);
  }
}

function normalizeForm(form) {
  const endpoint = String(form.endpoint || "").trim().replace(/\/+$/, "");
  const repositorySyncMode = ["init", "sidecar"].includes(form.repositorySyncMode)
    ? form.repositorySyncMode
    : "direct";
  return {
    sourceFolder: String(form.sourceFolder || ""),
    profileId: String(form.profileId || "").trim(),
    profileName: String(form.profileName || "").trim(),
    deploymentName: String(form.deploymentName || "").trim(),
    image: String(form.image || "").trim(),
    endpoint,
    bucket: String(form.bucket || "").trim(),
    prefix: cleanPrefix(String(form.prefix || "")),
    accessKeyId: String(form.accessKeyId || "").trim(),
    secretAccessKey: String(form.secretAccessKey || ""),
    region: String(form.region || "us-east-1").trim() || "us-east-1",
    forcePathStyle: effectiveForcePathStyle(endpoint, !!form.forcePathStyle),
    s3CaCertificate: String(form.s3CaCertificate || "").trim(),
    detectedBackend: String(form.detectedBackend || "triton").trim().toLowerCase() || "triton",
    modelControlMode:
      repositorySyncMode === "init" ? "explicit" : form.modelControlMode === "poll" ? "poll" : "explicit",
    repositorySyncMode,
    repositoryPollSecs: Number(form.repositoryPollSecs || 15),
    modelName: String(form.modelName || "").trim(),
    cpu: String(form.cpu || "").trim(),
    memory: String(form.memory || "").trim(),
    gpuCount: String(form.gpuCount || "").trim(),
  };
}

function effectiveForcePathStyle(endpoint, configuredValue) {
  return requiresPathStyle(endpoint) || configuredValue !== false;
}

function requiresPathStyle(endpoint) {
  let host = "";
  try {
    host = new URL(endpoint).hostname.toLowerCase();
  } catch {
    host = String(endpoint || "").split("/")[0].split(":")[0].toLowerCase();
  }
  if (!host) {
    return false;
  }
  if (["localhost", "host.docker.internal", "host.minikube.internal", "minio"].includes(host)) {
    return true;
  }
  if (host.endsWith(".local") || host.endsWith(".internal")) {
    return true;
  }
  return /^(?:\d{1,3}\.){3}\d{1,3}$/.test(host) || host.includes(":");
}

function validateForm(form) {
  const required = [
    ["Model repository path", form.deploymentName],
    ["Triton image", form.image],
    ["S3 endpoint", form.endpoint],
    ["S3 bucket", form.bucket],
    ["S3 access key", form.accessKeyId],
    ["S3 secret key", form.secretAccessKey],
  ];
  const missing = required.filter(([, value]) => !value).map(([label]) => label);
  if (missing.length) {
    throw new Error(`Missing required fields: ${missing.join(", ")}`);
  }
  if (!fs.existsSync(form.sourceFolder) || !fs.statSync(form.sourceFolder).isDirectory()) {
    throw new Error("Selected source folder does not exist.");
  }
  if (!findConfigPbtxt(form.sourceFolder)) {
    throw new Error("Selected folder must contain config.pbtxt directly or in a child model folder.");
  }
  if (!/^https?:\/\//i.test(form.endpoint)) {
    throw new Error("S3 endpoint must start with http:// or https://.");
  }
  if (form.gpuCount && (!/^\d+$/.test(form.gpuCount) || Number(form.gpuCount) < 0)) {
    throw new Error("GPU count must be a non-negative whole number.");
  }
}

async function uploadRepository(sourceFolder, form, report) {
  const files = listFiles(sourceFolder);
  if (!files.length) {
    throw new Error("Selected folder does not contain files to upload.");
  }
  const basePrefix = targetPrefix(form);
  const layout = repositoryLayout(sourceFolder, form.modelName);
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    const relative = toS3Path(path.relative(layout.relativeRoot, file));
    const key = [basePrefix, layout.modelFolderName, relative].filter(Boolean).join("/");
    const displayPath = [layout.modelFolderName, relative].filter(Boolean).join("/");
    report(`${index + 1}/${files.length} ${displayPath}`, 100 / files.length);
    await putS3Object(form, key, file);
  }
}

function repositoryLayout(sourceFolder, modelName) {
  const directConfig = path.join(sourceFolder, "config.pbtxt");
  if (fs.existsSync(directConfig)) {
    return {
      relativeRoot: sourceFolder,
      modelFolderName: sanitizeS3ModelFolderName(modelName || path.basename(sourceFolder)),
    };
  }
  return {
    relativeRoot: sourceFolder,
    modelFolderName: "",
  };
}

async function putS3Object(form, key, filePath) {
  const endpoint = new URL(form.endpoint);
  const body = fs.readFileSync(filePath);
  const payloadHash = sha256Hex(body);
  const encodedKey = key.split("/").map(encodeURIComponent).join("/");
  const pathName = form.forcePathStyle
    ? `/${encodeURIComponent(form.bucket)}/${encodedKey}`
    : `/${encodedKey}`;
  const host = form.forcePathStyle ? endpoint.host : `${form.bucket}.${endpoint.host}`;
  const now = new Date();
  const amzDate = toAmzDate(now);
  const dateStamp = amzDate.slice(0, 8);
  const headers = {
    host,
    "x-amz-content-sha256": payloadHash,
    "x-amz-date": amzDate,
  };
  const authorization = s3Authorization({
    accessKeyId: form.accessKeyId,
    secretAccessKey: form.secretAccessKey,
    region: form.region,
    dateStamp,
    amzDate,
    method: "PUT",
    pathName,
    headers,
    payloadHash,
  });
  await sendHttpRequest(endpoint, {
    method: "PUT",
    host,
    pathName,
    headers: {
      ...headers,
      authorization,
      "content-length": String(body.length),
    },
    body,
    tlsRejectUnauthorized: false,
  });
}

function s3Authorization({
  accessKeyId,
  secretAccessKey,
  region,
  dateStamp,
  amzDate,
  method,
  pathName,
  headers,
  payloadHash,
}) {
  const signedHeaders = Object.keys(headers).sort().join(";");
  const canonicalHeaders = Object.keys(headers)
    .sort()
    .map((name) => `${name}:${headers[name]}\n`)
    .join("");
  const canonicalRequest = [
    method,
    pathName,
    "",
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join("\n");
  const credentialScope = `${dateStamp}/${region}/s3/aws4_request`;
  const stringToSign = [
    "AWS4-HMAC-SHA256",
    amzDate,
    credentialScope,
    sha256Hex(canonicalRequest),
  ].join("\n");
  const signingKey = hmac(
    hmac(hmac(hmac(`AWS4${secretAccessKey}`, dateStamp), region), "s3"),
    "aws4_request",
  );
  const signature = hmacHex(signingKey, stringToSign);
  return `AWS4-HMAC-SHA256 Credential=${accessKeyId}/${credentialScope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;
}

function sendHttpRequest(endpoint, request) {
  const client = endpoint.protocol === "https:" ? https : http;
  return new Promise((resolve, reject) => {
    const req = client.request(
      {
        protocol: endpoint.protocol,
        hostname: request.host.split(":")[0],
        port: endpoint.port || (endpoint.protocol === "https:" ? 443 : 80),
        method: request.method,
        path: request.pathName,
        headers: request.headers,
        rejectUnauthorized: request.tlsRejectUnauthorized,
      },
      (res) => {
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => {
          const body = Buffer.concat(chunks).toString("utf8");
          if (res.statusCode >= 200 && res.statusCode < 300) {
            resolve();
            return;
          }
          reject(new Error(`S3 upload failed with HTTP ${res.statusCode}: ${body}`));
        });
      },
    );
    req.on("error", reject);
    req.end(request.body);
  });
}

function toAmzDate(date) {
  return date.toISOString().replace(/[:-]|\.\d{3}/g, "");
}

function sha256Hex(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function hmac(key, value) {
  return crypto.createHmac("sha256", key).update(value).digest();
}

function hmacHex(key, value) {
  return crypto.createHmac("sha256", key).update(value).digest("hex");
}

function listFiles(root) {
  const output = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (![".git", ".vscode", "__pycache__"].includes(entry.name)) {
          stack.push(fullPath);
        }
        continue;
      }
      if (entry.isFile()) {
        output.push(fullPath);
      }
    }
  }
  return output.sort();
}

function deploymentPayload(form) {
  const payload = {
    deployment_name: form.deploymentName,
    image: form.image,
    s3_url: deploymentS3Url(form),
    s3_access_key: form.accessKeyId,
    s3_secret_key: form.secretAccessKey,
    s3_region: form.region,
    model_control_mode: form.modelControlMode,
    repository_sync_mode: form.repositorySyncMode,
    repository_poll_secs: form.repositoryPollSecs > 0 ? form.repositoryPollSecs : 15,
    allow_metrics: true,
  };
  if (form.endpoint.toLowerCase().startsWith("https://") && form.s3CaCertificate) {
    payload.s3_ca_certificate = form.s3CaCertificate;
  }
  if (form.modelControlMode === "explicit" && form.modelName) {
    payload.model_name = form.modelName;
  }
  if (form.cpu) {
    payload.cpu = form.cpu;
  }
  if (form.memory) {
    payload.memory = form.memory;
  }
  if (form.gpuCount && Number(form.gpuCount) > 0) {
    payload.gpu_count = Number(form.gpuCount);
  }
  return payload;
}

function deploymentS3Url(form) {
  const parts = [form.bucket, targetPrefix(form)].filter(Boolean).map(encodePathSegment);
  return `s3://${form.endpoint}/${parts.join("/")}`;
}

function targetPrefix(form) {
  return cleanPrefix([form.prefix, form.deploymentName].filter(Boolean).join("/"));
}

function cleanPrefix(value) {
  return value.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "").replace(/\/+/g, "/");
}

function toS3Path(value) {
  return value.replace(/\\/g, "/");
}

function sanitizeS3ModelFolderName(value) {
  const cleaned = cleanPrefix(String(value || ""));
  if (!cleaned || cleaned.includes("/")) {
    throw new Error("Model name must be a single folder name for Triton repository upload.");
  }
  return cleaned;
}

function encodePathSegment(value) {
  return String(value)
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

function renderHtml(webview, nonce, initial) {
  const state = JSON.stringify(initial).replace(/</g, "\\u003c");
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}'; connect-src 'self' http: https:;">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Triton Control Deploy</title>
  <style>
    body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); background: var(--vscode-editor-background); padding: 20px; }
    form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; max-width: 980px; }
    label { display: grid; gap: 5px; font-size: 12px; color: var(--vscode-descriptionForeground); }
    input, select, textarea { box-sizing: border-box; width: 100%; border: 1px solid var(--vscode-input-border); background: var(--vscode-input-background); color: var(--vscode-input-foreground); padding: 8px; }
    .wide { grid-column: 1 / -1; }
    .hidden { display: none; }
    .checks { display: flex; gap: 18px; align-items: center; }
    .checks label { display: flex; flex-direction: row; align-items: center; gap: 8px; }
    details { border: 1px solid var(--vscode-input-border); background: var(--vscode-editorWidget-background); }
    summary { cursor: pointer; padding: 9px 10px; color: var(--vscode-foreground); }
    .details-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; padding: 0 10px 10px; }
    button { width: max-content; border: 0; background: var(--vscode-button-background); color: var(--vscode-button-foreground); padding: 9px 14px; cursor: pointer; }
    button:disabled { opacity: 0.55; cursor: default; }
    .status { margin-top: 18px; white-space: pre-wrap; color: var(--vscode-descriptionForeground); }
    .error { color: var(--vscode-errorForeground); }
    .success { color: var(--vscode-testing-iconPassed); }
    .preview { border: 1px solid var(--vscode-input-border); background: var(--vscode-editorWidget-background); padding: 10px; color: var(--vscode-descriptionForeground); }
    .preview strong { display: block; margin-bottom: 5px; color: var(--vscode-foreground); }
    .preview code { color: var(--vscode-textLink-foreground); word-break: break-all; }
    @media (max-width: 720px) {
      form, .details-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <h2>Deploy Triton Model Repository</h2>
  <form id="deploy-form">
    <label class="wide">Source folder<input name="sourceFolder" readonly></label>
    <label>Model Repository Path<input name="deploymentName" required></label>
    <label>Triton image<input name="image" required></label>
    <label>Detected backend<input name="detectedBackend" readonly></label>
    <label class="wide">S3 profile<select name="profileId"><option value="">Manual S3 settings</option></select></label>
    <label class="wide">Repository prefix<input name="prefix" placeholder="team/model-repository"></label>
    <div class="wide preview">
      <strong>Serving repository</strong>
      <code id="destination-path">s3://bucket/model-repository-path</code>
    </div>
    <select class="hidden" name="modelControlMode"><option value="explicit">explicit</option></select>
    <select class="hidden" name="repositorySyncMode"><option value="direct">direct</option><option value="sidecar">sidecar</option></select>
    <input class="hidden" name="repositoryPollSecs" type="number" min="1">
    <label>Model name<input name="modelName" placeholder="from config.pbtxt or manual input"></label>
    <details class="wide">
      <summary>Manual S3 settings</summary>
      <div class="details-grid">
        <label>S3 profile name<input name="profileName" placeholder="team-minio"></label>
        <label>S3 endpoint<input name="endpoint" placeholder="https://s3.example.com"></label>
        <label>S3 bucket<input name="bucket"></label>
        <label>S3 region<input name="region"></label>
        <label>S3 access key<input name="accessKeyId"></label>
        <label>S3 secret key<input name="secretAccessKey" type="password"></label>
        <label class="wide">S3 CA certificate for Triton HTTPS access<textarea name="s3CaCertificate" rows="6" placeholder="-----BEGIN CERTIFICATE-----"></textarea></label>
        <div class="wide checks">
          <label><input name="forcePathStyle" type="checkbox"> Path-style S3</label>
        </div>
        <div class="wide"><button id="save-profile" type="button">Save S3 Profile</button></div>
      </div>
    </details>
    <details class="wide">
      <summary>Resources</summary>
      <div class="details-grid">
        <label>CPU<input name="cpu"></label>
        <label>RAM<input name="memory"></label>
        <label>GPU count<input name="gpuCount" type="number" min="0" step="1"></label>
      </div>
    </details>
    <div class="wide"><button id="submit" type="submit">Upload and Deploy</button></div>
  </form>
  <div id="status" class="status"></div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const initial = ${state};
    const form = document.getElementById('deploy-form');
    const statusEl = document.getElementById('status');
    const submit = document.getElementById('submit');
    const saveProfile = document.getElementById('save-profile');
    let profiles = [];

    for (const [key, value] of Object.entries(initial)) {
      const input = form.elements[key];
      if (!input) continue;
      if (input.type === 'checkbox') input.checked = !!value;
      else input.value = value ?? '';
    }
    updateDestinationPath();
    loadS3Profiles();

    form.addEventListener('input', updateDestinationPath);
    form.addEventListener('change', updateDestinationPath);
    form.elements.profileId.addEventListener('change', () => {
      const selected = profiles.find((profile) => String(profile.id) === form.elements.profileId.value);
      if (selected) applyS3Profile(selected);
      updateDestinationPath();
    });
    saveProfile.addEventListener('click', saveCurrentS3Profile);

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      submit.disabled = true;
      statusEl.className = 'status';
      statusEl.textContent = 'Uploading model repository to S3...';
      vscode.postMessage({ type: 'upload', form: readForm() });
    });

    window.addEventListener('message', async (event) => {
      const message = event.data || {};
      if (message.type === 'progress') {
        statusEl.textContent = message.text;
        return;
      }
      if (message.type === 'error') {
        submit.disabled = false;
        statusEl.className = 'status error';
        statusEl.textContent = message.text;
        return;
      }
      if (message.type === 'createDeployment') {
        statusEl.textContent = 'Creating Triton Control deployment...';
        try {
          const response = await fetch('/api/deployments', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(message.payload),
          });
          const body = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(body.detail || 'Failed to create deployment.');
          }
          statusEl.className = 'status success';
          statusEl.textContent = 'Deployment created. Instance id: ' + body.instance_id;
          await notifyDeploymentNavigation(body.instance_id);
          const navigationMessage = {
            source: 'triton-control-deploy',
            type: 'deploymentCreated',
            instanceId: body.instance_id,
          };
          postToHostFrames(navigationMessage);
        } catch (error) {
          submit.disabled = false;
          statusEl.className = 'status error';
          statusEl.textContent = error.message || String(error);
        }
      }
    });

    async function notifyDeploymentNavigation(instanceId) {
      try {
        await fetch('/api/development/deployment-navigation', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ instance_id: instanceId }),
        });
      } catch {}
    }

    async function loadS3Profiles() {
      try {
        const response = await fetch('/api/s3-profiles', { credentials: 'include' });
        if (!response.ok) return;
        profiles = await response.json();
        const select = form.elements.profileId;
        select.innerHTML = '<option value="">Manual S3 settings</option>';
        for (const profile of profiles) {
          const option = document.createElement('option');
          option.value = String(profile.id);
          option.textContent = profile.name;
          select.appendChild(option);
        }
        if (profiles.length) {
          select.value = String(profiles[0].id);
          applyS3Profile(profiles[0]);
        }
      } catch {}
    }

    function applyS3Profile(profile) {
      form.elements.profileName.value = profile.name || '';
      form.elements.endpoint.value = profile.endpoint || '';
      form.elements.bucket.value = profile.bucket || '';
      form.elements.region.value = profile.region || 'us-east-1';
      form.elements.accessKeyId.value = profile.access_key || '';
      form.elements.secretAccessKey.value = profile.secret_key || '';
      form.elements.forcePathStyle.checked = profile.force_path_style !== false;
      form.elements.s3CaCertificate.value = profile.ca_certificate || '';
    }

    async function saveCurrentS3Profile() {
      const data = readForm();
      const payload = {
        name: data.profileName,
        endpoint: data.endpoint,
        bucket: data.bucket,
        region: data.region || 'us-east-1',
        access_key: data.accessKeyId,
        secret_key: data.secretAccessKey,
        prefix: data.prefix || '',
        force_path_style: !!data.forcePathStyle,
        ca_certificate: data.s3CaCertificate || '',
      };
      if (!payload.name) {
        statusEl.className = 'status error';
        statusEl.textContent = 'S3 profile name is required.';
        return;
      }
      try {
        const response = await fetch('/api/s3-profiles', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(body.detail || 'Failed to save S3 profile.');
        }
        statusEl.className = 'status success';
        statusEl.textContent = 'S3 profile saved.';
        await loadS3Profiles();
        form.elements.profileId.value = String(body.id);
      } catch (error) {
        statusEl.className = 'status error';
        statusEl.textContent = error.message || String(error);
      }
    }

    function readForm() {
      const data = {};
      for (const element of Array.from(form.elements)) {
        if (!element.name) continue;
        data[element.name] = element.type === 'checkbox' ? element.checked : element.value;
      }
      return data;
    }

    function updateDestinationPath() {
      const destination = document.getElementById('destination-path');
      const data = readForm();
      const endpoint = String(data.endpoint || '').trim().replace(/^s3:\\/\\//i, '').replace(/\\/+$/g, '');
      const bucket = cleanS3Path(data.bucket || 'bucket');
      const target = cleanS3Path([data.prefix, data.deploymentName || 'model-repository-path'].filter(Boolean).join('/'));
      const root = endpoint ? 's3://' + endpoint + '/' + bucket : 's3://' + bucket;
      destination.textContent = [root, target].filter(Boolean).join('/');
    }

    function cleanS3Path(value) {
      return String(value || '').replace(/\\\\/g, '/').replace(/^\\/+|\\/+$/g, '').replace(/\\/+/g, '/');
    }

    function postToHostFrames(message) {
      let target = window;
      for (let index = 0; index < 8; index += 1) {
        try {
          target.postMessage(message, '*');
        } catch {}
        if (!target.parent || target.parent === target) {
          break;
        }
        target = target.parent;
      }
      try {
        window.top.postMessage(message, '*');
      } catch {}
    }
  </script>
</body>
</html>`;
}

function deactivate() {}

module.exports = { activate, deactivate };
