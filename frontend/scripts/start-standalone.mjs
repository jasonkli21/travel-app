import { spawn } from "node:child_process";
import { cp, mkdir, access } from "node:fs/promises";
import { constants } from "node:fs";
import { constants as osConstants } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const buildDirectory = path.join(frontendDirectory, ".next");
const standaloneDirectory = path.join(buildDirectory, "standalone");

try {
  await access(path.join(standaloneDirectory, "server.js"), constants.R_OK);
} catch {
  console.error("Standalone build not found. Run `pnpm build` before `pnpm start`.");
  process.exit(1);
}

for (const [source, destination] of [
  [path.join(buildDirectory, "static"), path.join(standaloneDirectory, ".next", "static")],
  [path.join(frontendDirectory, "public"), path.join(standaloneDirectory, "public")],
]) {
  try {
    await access(source, constants.R_OK);
    await mkdir(path.dirname(destination), { recursive: true });
    await cp(source, destination, { recursive: true, force: true });
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

const localEnvironmentFile = path.join(frontendDirectory, ".env.local");
try {
  process.loadEnvFile(localEnvironmentFile);
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
process.env.HOSTNAME ??= "127.0.0.1";
process.env.PORT ??= "3000";

const server = spawn(process.execPath, ["server.js"], {
  cwd: standaloneDirectory,
  env: process.env,
  stdio: "inherit",
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.kill(signal));
}

server.on("error", (error) => {
  console.error(`Could not start standalone server: ${error.message}`);
  process.exitCode = 1;
});

server.on("exit", (code, signal) => {
  process.exitCode = code ?? (signal ? 128 + osConstants.signals[signal] : 0);
});
