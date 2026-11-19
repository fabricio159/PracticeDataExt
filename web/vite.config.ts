/**
 * Vite config plus a tiny read-only API over job_watch's snapshot folders:
 *   GET /api/snapshots?dataset=<d>            -> ["jobs-...json", ...] newest first
 *   GET /api/snapshot?dataset=<d>&name=<file> -> { name, snapshot, diff } (latest if no name)
 * Datasets: banks -> ../data/jobs (or $JOBS_DIR), tech -> ../data/tech (or $TECH_DIR).
 * Works in `npm run dev` and `npm run preview`.
 */
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Connect, Plugin } from "vite";
import { defineConfig } from "vite";

const folder = (envValue: string | undefined, fallback: string) =>
  envValue ? path.resolve(envValue) : fileURLToPath(new URL(fallback, import.meta.url));

const DATASETS: Record<string, string> = {
  banks: folder(process.env.JOBS_DIR, "../data/jobs"),
  tech: folder(process.env.TECH_DIR, "../data/tech"),
};
const SNAPSHOT_NAME = /^jobs-\d{8}T\d{6}(_\d+)?Z\.json$/;

async function listSnapshots(dir: string): Promise<string[]> {
  try {
    return (await readdir(dir)).filter((n) => SNAPSHOT_NAME.test(n)).sort().reverse();
  } catch {
    return []; // folder doesn't exist yet: no runs
  }
}

async function readJson(dir: string, name: string): Promise<unknown> {
  try {
    return JSON.parse(await readFile(path.join(dir, name), "utf-8"));
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return null; // e.g. no diff on a first run
    throw e;
  }
}

function send(res: Parameters<Connect.NextHandleFunction>[1], status: number, body: unknown): void {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(body));
}

const handler: Connect.NextHandleFunction = async (req, res, next) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  if (!url.pathname.startsWith("/api/")) return next();
  const dataset = url.searchParams.get("dataset") ?? "banks";
  const dir = Object.hasOwn(DATASETS, dataset) ? DATASETS[dataset] : undefined;
  if (!dir) return send(res, 404, { error: `unknown dataset ${dataset}` });
  try {
    if (url.pathname === "/api/snapshots") {
      return send(res, 200, await listSnapshots(dir));
    }
    if (url.pathname === "/api/snapshot") {
      const names = await listSnapshots(dir);
      const name = url.searchParams.get("name") ?? names[0];
      // Only names from the listing are served, so no path can escape JOBS_DIR.
      if (!name || !names.includes(name)) {
        return send(res, 404, { error: names.length ? `unknown snapshot ${name}`
          : `no snapshots in ${dir}; run: python -m week01_http.job_watch --watch ${dataset}` });
      }
      const diffName = name.replace(/^jobs-/, "diff-");
      return send(res, 200, { name, snapshot: await readJson(dir, name), diff: await readJson(dir, diffName) });
    }
  } catch (e) {
    return send(res, 500, { error: String(e) });
  }
  next();
};

function jobsApi(): Plugin {
  return {
    name: "jobs-api",
    configureServer: (server) => void server.middlewares.use(handler),
    configurePreviewServer: (server) => void server.middlewares.use(handler),
  };
}

export default defineConfig({ plugins: [jobsApi()] });
