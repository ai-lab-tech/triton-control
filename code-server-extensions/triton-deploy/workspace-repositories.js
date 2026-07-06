const fs = require("fs");
const path = require("path");

function discoverWorkspaceRepositories(workspaceFolders) {
  const repositories = [];
  for (const root of workspaceFolders.map(workspaceFolderPath).filter(Boolean)) {
    for (const child of safeReadDirectory(root)) {
      const fullPath = path.join(root, child);
      if (isTritonRepositoryFolder(fullPath)) {
        const needsArtifacts = repositoryNeedsArtifacts(fullPath);
        repositories.push({
          name: child,
          folder: fullPath,
          workspaceFolder: root,
          setupFile: findRepositorySetupFile(fullPath),
          needsSetup: needsArtifacts || findScaffoldConfigFiles(fullPath).length > 0,
          setupLabel: needsArtifacts ? "Open artifact guidance" : "Open config template",
          setupIcon: needsArtifacts ? "book" : "file-code",
        });
      }
    }
  }
  return repositories.sort((a, b) => a.name.localeCompare(b.name));
}

function workspaceFolderPath(folder) {
  if (typeof folder === "string") {
    return folder;
  }
  return folder?.uri?.fsPath || folder?.fsPath || "";
}

function isTritonRepositoryFolder(folder) {
  if (!safeStat(folder)?.isDirectory()) {
    return false;
  }
  return safeReadDirectory(folder).some((entry) => (
    fs.existsSync(path.join(folder, entry, "config.pbtxt"))
  ));
}

function findRepositorySetupFile(folder) {
  const guidance = findPlaceholderGuidanceFiles(folder);
  if (guidance.length) {
    return guidance[0];
  }
  const scaffoldConfigs = findScaffoldConfigFiles(folder);
  if (scaffoldConfigs.length) {
    return scaffoldConfigs[0];
  }
  for (const modelFolder of safeReadDirectory(folder)) {
    const configPath = path.join(folder, modelFolder, "config.pbtxt");
    if (fs.existsSync(configPath)) {
      return configPath;
    }
  }
  return folder;
}

function repositoryNeedsArtifacts(folder) {
  return findPlaceholderGuidanceFiles(folder).length > 0;
}

function findPlaceholderGuidanceFiles(folder) {
  const files = [];
  for (const modelFolder of safeReadDirectory(folder)) {
    const fullModelFolder = path.join(folder, modelFolder);
    for (const versionFolder of safeReadDirectory(fullModelFolder)) {
      const readme = path.join(fullModelFolder, versionFolder, "README.md");
      if (safeReadFile(readme).includes("This scaffold is intentionally not a production model.")) {
        files.push(readme);
      }
    }
  }
  return files.sort();
}

function findScaffoldConfigFiles(folder) {
  const files = [];
  for (const modelFolder of safeReadDirectory(folder)) {
    const configPath = path.join(folder, modelFolder, "config.pbtxt");
    const content = safeReadFile(configPath);
    if (
      content.includes('name: "') &&
      content.includes("INPUT__0") &&
      content.includes("OUTPUT__0") &&
      content.includes("dims: [ 1 ]")
    ) {
      files.push(configPath);
    }
  }
  return files.sort();
}

function safeReadFile(filePath) {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch {
    return "";
  }
}

function safeReadDirectory(folder) {
  try {
    return fs.readdirSync(folder);
  } catch {
    return [];
  }
}

function safeStat(filePath) {
  try {
    return fs.statSync(filePath);
  } catch {
    return null;
  }
}

module.exports = {
  discoverWorkspaceRepositories,
  findRepositorySetupFile,
  findScaffoldConfigFiles,
  isTritonRepositoryFolder,
};
