import { createCliRenderer } from "@opentui/core";
import { createRoot } from "@opentui/react";
import { ActivityFeedApp } from "./App";

type CliArgs = {
  rootPath: string;
  packageRootPath: string;
  pythonPath: string;
  runId?: string;
};

function parseArgs(argv: string[]): CliArgs {
  let rootPath = "";
  let packageRootPath = "";
  let pythonPath = "";
  let runId: string | undefined;

  for (let index = 0; index < argv.length; index += 1) {
    const current = argv[index];
    if (current === "--root") {
      rootPath = argv[index + 1] ?? "";
      index += 1;
    } else if (current === "--package-root") {
      packageRootPath = argv[index + 1] ?? "";
      index += 1;
    } else if (current === "--python") {
      pythonPath = argv[index + 1] ?? "";
      index += 1;
    } else if (current === "--run-id") {
      runId = argv[index + 1] ?? "";
      index += 1;
    }
  }

  if (!rootPath) {
    throw new Error("missing required --root");
  }
  if (!pythonPath) {
    throw new Error("missing required --python");
  }
  return { rootPath, packageRootPath: packageRootPath || rootPath, pythonPath, runId };
}

const args = parseArgs(Bun.argv.slice(2));
const renderer = await createCliRenderer({
  exitOnCtrlC: false,
});

createRoot(renderer).render(
  <ActivityFeedApp
    rootPath={args.rootPath}
    packageRootPath={args.packageRootPath}
    pythonPath={args.pythonPath}
    runId={args.runId}
  />,
);
