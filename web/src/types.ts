/** Shapes written by src/week01_http/job_watch.py. Older snapshots may lack some fields. */

export interface Job {
  source: string;
  company: string;
  title: string;
  location: string;
  published?: string; // YYYY-MM-DD or "Not Specified"; absent in older snapshots
  url: string;
  id?: string;
}

export interface Snapshot {
  run_at: string;
  sources?: string[];
  failed?: Record<string, string>;
  jobs: Job[];
}

export interface Diff {
  run_at: string;
  previous: string;
  new_sources: string[];
  failed: Record<string, string>;
  added: Job[];
  removed: Job[];
  changed: { before: Job; after: Job }[];
}

export interface SnapshotResponse {
  name: string;
  snapshot: Snapshot;
  diff: Diff | null; // null on a first (baseline) run
}
