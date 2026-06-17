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
  const modelName = detectedModelName || await promptForModelName(sourceFolder);
  if (!modelName) {
    return null;
  }
  const deploymentName = toKubernetesName(modelName);
  const initial = {
    sourceFolder,
    deploymentName,
    image: cfg.get("tritonImage") || "nvcr.io/nvidia/tritonserver:25.02-py3",
    endpoint: cfg.get("s3Endpoint") || s3x.get("endpointUrl") || process.env.AWS_ENDPOINT_URL || process.env.S3_ENDPOINT || "",
    bucket: cfg.get("s3Bucket") || process.env.S3_BUCKET || "",
    prefix: cfg.get("s3Prefix") || process.env.S3_PREFIX || "",
    accessKeyId: cfg.get("s3AccessKeyId") || s3x.get("accessKeyId") || process.env.AWS_ACCESS_KEY_ID || "",
    secretAccessKey: cfg.get("s3SecretAccessKey") || s3x.get("secretAccessKey") || process.env.AWS_SECRET_ACCESS_KEY || "",
    region: cfg.get("s3Region") || s3x.get("region") || process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || "us-east-1",
    forcePathStyle: cfg.get("s3ForcePathStyle") !== false && s3x.get("forcePathStyle") !== false,
    s3CaCertificate: cfg.get("s3CaCertificate") || "",
    modelControlMode: "explicit",
    repositoryPollSecs: 15,
    modelName,
  };
  return promptForMissingS3Settings(initial);
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

async function promptForMissingS3Settings(initial) {
  const prompts = [
    {
      key: "endpoint",
      label: "S3 endpoint",
      placeHolder: "https://s3.example.com",
      password: false,
    },
    {
      key: "bucket",
      label: "S3 bucket",
      placeHolder: "model-repository",
      password: false,
    },
    {
      key: "accessKeyId",
      label: "S3 access key",
      placeHolder: "access-key-id",
      password: false,
    },
    {
      key: "secretAccessKey",
      label: "S3 secret key",
      placeHolder: "",
      password: true,
    },
  ];
  const next = { ...initial };
  let changed = false;
  for (const prompt of prompts) {
    if (String(next[prompt.key] || "").trim()) {
      continue;
    }
    const value = await vscode.window.showInputBox({
      title: "Triton Control Deploy",
      prompt: `${prompt.label} is required to upload the model repository.`,
      placeHolder: prompt.placeHolder,
      password: prompt.password,
      ignoreFocusOut: true,
      validateInput: (input) => {
        const trimmed = input.trim();
        if (!trimmed) {
          return `${prompt.label} is required.`;
        }
        if (prompt.key === "endpoint" && !/^https?:\/\//i.test(trimmed)) {
          return "Endpoint must start with http:// or https://.";
        }
        return undefined;
      },
    });
    if (value === undefined) {
      return null;
    }
    next[prompt.key] = prompt.key === "secretAccessKey" ? value : value.trim();
    changed = true;
  }
  if (changed) {
    await saveS3Settings(next);
  }
  return next;
}

async function saveS3Settings(values) {
  const cfg = vscode.workspace.getConfiguration("tritonControlDeploy");
  const target = vscode.ConfigurationTarget.Global;
  await cfg.update("s3Endpoint", values.endpoint, target);
  await cfg.update("s3Bucket", values.bucket, target);
  await cfg.update("s3Prefix", values.prefix, target);
  await cfg.update("s3AccessKeyId", values.accessKeyId, target);
  await cfg.update("s3SecretAccessKey", values.secretAccessKey, target);
  await cfg.update("s3Region", values.region, target);
  await cfg.update("s3ForcePathStyle", values.forcePathStyle, target);
  await cfg.update("s3CaCertificate", values.s3CaCertificate || "", target);
  await cfg.update("tritonImage", values.image, target);
}

function normalizeForm(form) {
  return {
    sourceFolder: String(form.sourceFolder || ""),
    deploymentName: String(form.deploymentName || "").trim(),
    image: String(form.image || "").trim(),
    endpoint: String(form.endpoint || "").trim().replace(/\/+$/, ""),
    bucket: String(form.bucket || "").trim(),
    prefix: cleanPrefix(String(form.prefix || "")),
    accessKeyId: String(form.accessKeyId || "").trim(),
    secretAccessKey: String(form.secretAccessKey || ""),
    region: String(form.region || "us-east-1").trim() || "us-east-1",
    forcePathStyle: !!form.forcePathStyle,
    s3CaCertificate: String(form.s3CaCertificate || "").trim(),
    modelControlMode: form.modelControlMode === "poll" ? "poll" : "explicit",
    repositoryPollSecs: Number(form.repositoryPollSecs || 15),
    modelName: String(form.modelName || "").trim(),
  };
}

function validateForm(form) {
  const required = [
    ["Deployment name", form.deploymentName],
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
    repository_poll_secs: form.repositoryPollSecs > 0 ? form.repositoryPollSecs : 15,
    allow_metrics: true,
  };
  if (form.endpoint.toLowerCase().startsWith("https://") && form.s3CaCertificate) {
    payload.s3_ca_certificate = form.s3CaCertificate;
  }
  if (form.modelControlMode === "explicit" && form.modelName) {
    payload.model_name = form.modelName;
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
    .checks { display: flex; gap: 18px; align-items: center; }
    .checks label { display: flex; flex-direction: row; align-items: center; gap: 8px; }
    button { width: max-content; border: 0; background: var(--vscode-button-background); color: var(--vscode-button-foreground); padding: 9px 14px; cursor: pointer; }
    button:disabled { opacity: 0.55; cursor: default; }
    .status { margin-top: 18px; white-space: pre-wrap; color: var(--vscode-descriptionForeground); }
    .error { color: var(--vscode-errorForeground); }
    .success { color: var(--vscode-testing-iconPassed); }
  </style>
</head>
<body>
  <h2>Deploy Triton Model Repository</h2>
  <form id="deploy-form">
    <label class="wide">Source folder<input name="sourceFolder" readonly></label>
    <label>Deployment name<input name="deploymentName" required></label>
    <label>Triton image<input name="image" required></label>
    <label>S3 endpoint<input name="endpoint" placeholder="https://s3.example.com" required></label>
    <label>S3 bucket<input name="bucket" required></label>
    <label>S3 repository parent prefix<input name="prefix"></label>
    <label>S3 region<input name="region" required></label>
    <label>S3 access key<input name="accessKeyId" required></label>
    <label>S3 secret key<input name="secretAccessKey" type="password" required></label>
    <label>Model control<select name="modelControlMode"><option value="explicit">explicit</option><option value="poll">poll</option></select></label>
    <label>Repository poll seconds<input name="repositoryPollSecs" type="number" min="1"></label>
    <label>Model name<input name="modelName" placeholder="from config.pbtxt or manual input"></label>
    <label class="wide">S3 CA certificate for Triton HTTPS access<textarea name="s3CaCertificate" rows="6" placeholder="-----BEGIN CERTIFICATE-----"></textarea></label>
    <div class="wide checks">
      <label><input name="forcePathStyle" type="checkbox"> Path-style S3</label>
    </div>
    <div class="wide"><button id="submit" type="submit">Upload and Deploy</button></div>
  </form>
  <div id="status" class="status"></div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const initial = ${state};
    const form = document.getElementById('deploy-form');
    const statusEl = document.getElementById('status');
    const submit = document.getElementById('submit');

    for (const [key, value] of Object.entries(initial)) {
      const input = form.elements[key];
      if (!input) continue;
      if (input.type === 'checkbox') input.checked = !!value;
      else input.value = value ?? '';
    }

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

    function readForm() {
      const data = {};
      for (const element of Array.from(form.elements)) {
        if (!element.name) continue;
        data[element.name] = element.type === 'checkbox' ? element.checked : element.value;
      }
      return data;
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
