import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { downloadWithRetry } from "./download-with-retry.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const root = resolve(__dirname, "..");
const spec = process.env.OPENAPI_SPEC || join(root, "openapi", "triton-backend", "openapi.json");
const out = join(root, "src", "app", "api", "generated");
const jarDir = join(root, "tools");
const version = "3.0.52";
const jar = join(jarDir, `swagger-codegen-cli-${version}.jar`);
const javaExecutable =
  process.env.JAVA_HOME && existsSync(join(process.env.JAVA_HOME, "bin", "java.exe"))
    ? join(process.env.JAVA_HOME, "bin", "java.exe")
    : "java";
const defaultJarPath = `io/swagger/codegen/v3/swagger-codegen-cli/${version}/swagger-codegen-cli-${version}.jar`;
const jarUrls = [
  process.env.SWAGGER_CODEGEN_JAR_URL,
  `https://repo.maven.apache.org/maven2/${defaultJarPath}`,
  `https://repo1.maven.org/maven2/${defaultJarPath}`,
  `https://maven-central.storage-download.googleapis.com/maven2/${defaultJarPath}`,
].filter(Boolean);

if (!existsSync(spec)) {
  throw new Error(`OpenAPI spec not found: ${spec}`);
}

mkdirSync(jarDir, { recursive: true });

if (process.argv.includes("--fresh") || !existsSync(jar)) {
  // Maven mirrors can temporarily return 404 for an already published artifact.
  const buffer = await downloadWithRetry(jarUrls, { retryNotFound: true });
  writeFileSync(jar, buffer);
}

rmSync(out, { recursive: true, force: true });

try {
  execFileSync(
    javaExecutable,
    [
      "-jar",
      jar,
      "generate",
      "-i",
      spec,
      "-l",
      "typescript-angular",
      "-o",
      out,
      "--additional-properties=ngVersion=21.1.2,providedInRoot=true,modelPropertyNaming=original",
    ],
    { stdio: "inherit" },
  );
} catch (error) {
  throw new Error(
    `Failed to run swagger-codegen with ${javaExecutable}. Use Java 11 or newer. ${
      error instanceof Error ? error.message : String(error)
    }`,
  );
}

const encoderPath = join(out, "encoder.ts");
if (existsSync(encoderPath)) {
  let text = readFileSync(encoderPath, "utf-8");
  text = text.replace("    encodeKey(", "    override encodeKey(");
  text = text.replace("    encodeValue(", "    override encodeValue(");
  writeFileSync(encoderPath, text, "utf-8");
}

const instancesServicePath = join(out, "api", "instances.service.ts");
if (existsSync(instancesServicePath)) {
  let text = readFileSync(instancesServicePath, "utf-8");
  const pattern =
    /(return this\.httpClient\.request<any>\('get',`\$\{this\.basePath\}\/api\/instances\/\$\{encodeURIComponent\(String\(instance_id\)\)\}\/s3\/content\/raw`,\s*\{\s*params: queryParameters,\s*withCredentials: this\.configuration\.withCredentials,\s*headers: headers,\s*observe: observe,\s*)(reportProgress: reportProgress\s*\}\s*\);)/m;
  text = text.replace(pattern, "$1responseType: 'blob' as 'json',\n                $2");
  writeFileSync(instancesServicePath, text, "utf-8");
}
