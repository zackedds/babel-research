import type { ActivitySnapshot } from "./types";

const decoder = new TextDecoder();

export function loadActivitySnapshot(
  pythonPath: string,
  rootPath: string,
  packageRootPath: string,
  runId?: string,
): ActivitySnapshot {
  const cmd = [pythonPath, "-m", "src.runtime.activity", "--root", rootPath];
  if (runId) {
    cmd.push("--run-id", runId);
  }
  const proc = Bun.spawnSync({
    cmd,
    cwd: packageRootPath,
    stdout: "pipe",
    stderr: "pipe",
  });
  const stdout = decoder.decode(proc.stdout);
  const stderr = decoder.decode(proc.stderr).trim();
  if (!proc.success) {
    throw new Error(stderr || `snapshot command failed with exit code ${proc.exitCode}`);
  }
  return JSON.parse(stdout) as ActivitySnapshot;
}
