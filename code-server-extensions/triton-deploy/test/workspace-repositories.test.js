const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");

const {
  discoverWorkspaceRepositories,
  isTritonRepositoryFolder,
} = require("../workspace-repositories");

function makeTempWorkspace() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "triton-control-workspace-"));
}

function writeConfig(repositoryRoot, modelName, content = 'name: "model"\nbackend: "python"\n') {
  const modelFolder = path.join(repositoryRoot, modelName);
  fs.mkdirSync(modelFolder, { recursive: true });
  fs.writeFileSync(path.join(modelFolder, "config.pbtxt"), content, "utf8");
}

test("repository discovery removes deleted repository folders", () => {
  const workspace = makeTempWorkspace();
  const repo = path.join(workspace, "triton-model-repository");
  writeConfig(repo, "model");

  assert.deepEqual(
    discoverWorkspaceRepositories([workspace]).map((entry) => entry.name),
    ["triton-model-repository"],
  );

  fs.rmSync(repo, { recursive: true, force: true });

  assert.deepEqual(discoverWorkspaceRepositories([workspace]), []);
});

test("repository discovery ignores folders after config is deleted", () => {
  const workspace = makeTempWorkspace();
  const repo = path.join(workspace, "triton-model-repository");
  writeConfig(repo, "model");

  assert.equal(isTritonRepositoryFolder(repo), true);

  fs.rmSync(path.join(repo, "model", "config.pbtxt"), { force: true });

  assert.equal(isTritonRepositoryFolder(repo), false);
  assert.deepEqual(discoverWorkspaceRepositories([workspace]), []);
});

test("scaffold configs are marked for setup review", () => {
  const workspace = makeTempWorkspace();
  const repo = path.join(workspace, "triton-model-repository");
  writeConfig(repo, "model", 'name: "model"\ninput [ { name: "INPUT__0" dims: [ 1 ] } ]\noutput [ { name: "OUTPUT__0" dims: [ 1 ] } ]\n');

  const [repository] = discoverWorkspaceRepositories([workspace]);

  assert.equal(repository.needsSetup, true);
  assert.match(repository.setupFile, /config\.pbtxt$/);
});
