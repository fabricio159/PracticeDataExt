import "./style.css";
import type { Job, SnapshotResponse } from "./types";

const NOT_SPECIFIED = "Not Specified";
const COMPARED_FIELDS = ["title", "company", "location"] as const;

type ColumnKey = "published" | "title" | "company" | "location" | "source" | "url";
type SortKey = Exclude<ColumnKey, "url">;
type FilterKind = "published" | "text" | "select";

const COLUMNS: { key: ColumnKey; label: string; filter: FilterKind }[] = [
  { key: "published", label: "Published", filter: "published" },
  { key: "title", label: "Position", filter: "text" },
  { key: "company", label: "Company", filter: "select" },
  { key: "location", label: "Location", filter: "select" },
  { key: "source", label: "Source", filter: "select" },
  { key: "url", label: "Link", filter: "text" },
];

const PUBLISHED_OPTIONS: [string, string][] = [
  ["7", "Last 7 days"],
  ["30", "Last 30 days"],
  ["dated", "With date"],
  ["none", NOT_SPECIFIED],
];

const emptyFilters = (): Record<ColumnKey, string> =>
  ({ published: "", title: "", company: "", location: "", source: "", url: "" });

const DATASETS = {
  banks: { subtitle: "Open positions at Bolivian banks and financial entities",
           command: "python -m week01_http.job_watch" },
  tech: { subtitle: "International software companies hiring engineers in Latin America",
          command: "python -m week01_http.job_watch --watch tech" },
} as const;
type Dataset = keyof typeof DATASETS;

const state = {
  dataset: "banks" as Dataset,
  data: null as SnapshotResponse | null,
  query: "",
  columns: emptyFilters(),
  onlyNew: false,
  sortKey: "published" as SortKey,
  sortAsc: false, // newest first
};

// --- helpers -------------------------------------------------------------------------

const byId = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

/** Build an element. Children are text nodes, so scraped text is never parsed as HTML. */
function h<K extends keyof HTMLElementTagNameMap>(
  tag: K, attrs: Record<string, string> = {}, ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, value);
  el.append(...children);
  return el;
}

const jobKey = (j: Job) => `${j.source}\u0000${j.id || j.url}`;
const publishedOf = (j: Job) => j.published || NOT_SPECIFIED;
/** Case-insensitive comparison key for select filters ("La Paz" == "LA PAZ"). */
const norm = (s: string) => s.trim().toLocaleUpperCase("es");
/** Case- and accent-insensitive text for "contains" filters ("union" finds "Unión"). */
const fold = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase("es");

/** Only http(s) links become clickable; anything else in the data is shown as text. */
function link(url: string, text = "Open ↗"): Node {
  try {
    const { protocol } = new URL(url);
    if (protocol === "http:" || protocol === "https:") {
      return h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text);
    }
  } catch { /* not a URL */ }
  return document.createTextNode("—");
}

/** Run time from a snapshot name: "jobs-20261119T165651_123456Z.json" -> that instant (UTC). */
function runDate(name: string): Date | null {
  const m = /^(?:jobs|diff)-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/.exec(name);
  return m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6])) : null;
}

const formatTime = (d: Date) =>
  d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

/** Run time in the viewer's timezone, e.g. "Nov 19, 2026, 12:56 PM". */
function runLabel(name: string): string {
  const d = runDate(name);
  return d ? formatTime(d) : name;
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.error ?? `${url}: HTTP ${response.status}`);
  return body as T;
}

function showError(e: unknown): void {
  const box = byId("error");
  box.textContent = e instanceof Error ? e.message : String(e);
  box.hidden = false;
}

// --- rendering -----------------------------------------------------------------------

/** Distinct values of a column for its select filter, as [value, label] pairs. */
function columnOptions(key: "company" | "location" | "source"): [string, string][] {
  const { snapshot } = state.data!;
  if (key === "source") { // in the order job_watch lists its sources
    const present = new Set(snapshot.jobs.map((j) => j.source));
    const order = (snapshot.sources ?? []).filter((s) => present.has(s));
    for (const s of present) if (!order.includes(s)) order.push(s);
    return order.map((s) => [s, s]);
  }
  const values = new Map<string, string>();
  for (const j of snapshot.jobs) if (j[key]) values.set(norm(j[key]), norm(j[key]));
  return [...values.entries()].sort((a, b) => a[0].localeCompare(b[0], "es"));
}

function filterControl(key: ColumnKey, kind: FilterKind, label: string): HTMLElement {
  const aria = `Filter ${label}`;
  let control: HTMLInputElement | HTMLSelectElement;
  if (kind === "text") {
    control = h("input", { type: "search", placeholder: "contains…", "aria-label": aria });
  } else {
    const options = kind === "published" ? PUBLISHED_OPTIONS
      : columnOptions(key as "company" | "location" | "source");
    control = h("select", { "aria-label": aria }, h("option", { value: "" }, "All"),
      ...options.map(([value, text]) => h("option", { value }, text)));
    if (!options.some(([v]) => v === state.columns[key])) state.columns[key] = ""; // gone in this run
  }
  control.value = state.columns[key];
  control.addEventListener(kind === "text" ? "input" : "change", () => {
    state.columns[key] = control.value;
    renderTable();
  });
  return control;
}

/** Header: one row of sort buttons, one row of per-column filters. Rebuilt only when
 *  the snapshot (and so the select options) changes, so typing doesn't lose focus. */
function buildHeader(): void {
  const sortRow = h("tr", {}, ...COLUMNS.map(({ key, label }) => {
    if (key === "url") return h("th", {}, label);
    const button = h("button", { type: "button", "data-sort": key }, label);
    button.addEventListener("click", () => {
      state.sortAsc = key === state.sortKey ? !state.sortAsc : key !== "published";
      state.sortKey = key;
      renderTable();
    });
    return h("th", {}, button);
  }));
  const filterRow = h("tr", { class: "filter-row" },
    ...COLUMNS.map(({ key, label, filter }) => h("th", {}, filterControl(key, filter, label))));
  byId<HTMLTableElement>("jobs").tHead!.replaceChildren(sortRow, filterRow);
}

function updateSortIndicators(): void {
  for (const button of document.querySelectorAll<HTMLButtonElement>("#jobs [data-sort]")) {
    const active = button.dataset.sort === state.sortKey;
    button.dataset.arrow = active ? (state.sortAsc ? "▲" : "▼") : "";
    button.parentElement!.setAttribute("aria-sort",
      active ? (state.sortAsc ? "ascending" : "descending") : "none");
  }
}

/** Does this job pass the filter on one column? */
function matchesColumn(j: Job, key: ColumnKey, value: string): boolean {
  if (!value) return true;
  switch (key) {
    case "published": {
      const date = publishedOf(j);
      if (value === "none") return date === NOT_SPECIFIED;
      if (date === NOT_SPECIFIED) return false;
      if (value === "dated") return true;
      // "Last N days" counts back from the run's own date, so past runs read correctly.
      const cutoff = new Date(state.data!.snapshot.run_at);
      cutoff.setUTCDate(cutoff.getUTCDate() - Number(value));
      return date >= cutoff.toISOString().slice(0, 10);
    }
    case "title":
    case "url":
      return fold(j[key] ?? "").includes(fold(value));
    case "company":
    case "location":
      return norm(j[key] ?? "") === value;
    case "source":
      return j.source === value;
  }
}

function card(label: string, value: string | number, note = ""): HTMLElement {
  return h("div", { class: "card" }, h("span", { class: "card-label" }, label),
    h("strong", {}, String(value)), note ? h("span", { class: "muted small" }, note) : "");
}

function renderRuns(): void {
  const { snapshot, diff } = state.data!;
  const previous = diff ? runLabel(diff.previous) : "none (first run)";
  byId("runs").replaceChildren(
    h("span", {}, h("span", { class: "muted" }, "This run: "), formatTime(new Date(snapshot.run_at))),
    h("span", {}, h("span", { class: "muted" }, "Previous run: "), previous));
}

function renderSummary(): void {
  const { snapshot, diff } = state.data!;
  const dated = snapshot.jobs.filter((j) => publishedOf(j) !== NOT_SPECIFIED).length;
  const failed = Object.keys(snapshot.failed ?? {}).length;
  byId("summary").replaceChildren(
    card("Open positions", snapshot.jobs.length),
    card("With publication date", dated),
    card("New since previous run", diff ? diff.added.length : "—",
      diff ? `vs ${runLabel(diff.previous)}` : "baseline run"),
    card("Removed", diff ? diff.removed.length : "—"),
    card("Failed sources", failed, failed ? "previous results kept" : ""),
  );

  const box = byId("failed");
  const entries = Object.entries(snapshot.failed ?? {});
  box.hidden = entries.length === 0;
  box.replaceChildren(h("strong", {}, "Sources that failed this run"),
    h("ul", {}, ...entries.map(([name, err]) => h("li", {}, h("b", {}, name), `: ${err}`))));
}

/** Sort value of a column; "" for blanks and "Not Specified" dates. */
function sortValue(j: Job, key: SortKey): string {
  if (key === "published") return publishedOf(j) === NOT_SPECIFIED ? "" : publishedOf(j);
  return j[key] ?? "";
}

function compareBy(a: Job, b: Job, key: SortKey, asc: boolean): number {
  const va = sortValue(a, key), vb = sortValue(b, key);
  if (!va !== !vb) return va ? -1 : 1; // blanks / "Not Specified" always last
  const c = va.localeCompare(vb, "es", { sensitivity: "base", numeric: true });
  return asc ? c : -c;
}

/** The chosen column first; ties go newest first, then by position title. */
function compare(a: Job, b: Job): number {
  return compareBy(a, b, state.sortKey, state.sortAsc)
    || (state.sortKey !== "published" ? compareBy(a, b, "published", false) : 0)
    || (state.sortKey !== "title" ? compareBy(a, b, "title", true) : 0);
}

function renderTable(): void {
  const { snapshot, diff } = state.data!;
  const added = new Set((diff?.added ?? []).map(jobKey));
  const q = fold(state.query.trim());
  const active = Object.entries(state.columns).filter(([, v]) => v) as [ColumnKey, string][];

  const rows = snapshot.jobs.filter((j) =>
    (!state.onlyNew || added.has(jobKey(j))) &&
    active.every(([key, value]) => matchesColumn(j, key, value)) &&
    (!q || fold([publishedOf(j), j.title, j.company, j.location, j.source, j.url].join(" ")).includes(q)),
  ).sort(compare);

  const table = byId<HTMLTableElement>("jobs");
  updateSortIndicators();

  table.tBodies[0].replaceChildren(...(rows.length ? rows.map((j) => {
    const date = publishedOf(j);
    return h("tr", {},
      h("td", { class: date === NOT_SPECIFIED ? "nowrap muted" : "nowrap" }, date),
      h("td", {}, j.title, added.has(jobKey(j)) ? h("span", { class: "badge" }, "NEW") : ""),
      h("td", {}, j.company),
      h("td", {}, j.location || "—"),
      h("td", { class: "muted small" }, j.source),
      h("td", { class: "nowrap" }, link(j.url)));
  }) : [h("tr", {}, h("td", { colspan: String(COLUMNS.length), class: "empty" },
    "No positions match these filters."))]));

  byId("count").textContent = `Showing ${rows.length} of ${snapshot.jobs.length}`;
}

function renderChanges(): void {
  const { diff } = state.data!;
  const section = byId("changes");
  if (!diff) {
    section.replaceChildren(h("h2", {}, "Changes"),
      h("p", { class: "muted" }, "First run: no previous snapshot to compare against."));
    return;
  }
  const parts: Node[] = [h("h2", {}, "Changes"),
    h("p", { class: "muted" }, `Compared with ${runLabel(diff.previous)}.`)];
  if (diff.new_sources.length) {
    parts.push(h("p", { class: "muted" },
      `New sources (baseline, compared from the next run): ${diff.new_sources.join(", ")}`));
  }
  if (diff.removed.length) {
    parts.push(h("h3", {}, `Removed (${diff.removed.length})`), h("div", { class: "table-wrap" },
      h("table", {}, h("thead", {}, h("tr", {}, ...["Position", "Company", "Source", "Link"].map((t) => h("th", {}, t)))),
        h("tbody", {}, ...diff.removed.map((j) => h("tr", {}, h("td", {}, j.title),
          h("td", {}, j.company), h("td", { class: "muted small" }, j.source),
          h("td", {}, link(j.url))))))));
  }
  if (diff.changed.length) {
    parts.push(h("h3", {}, `Changed (${diff.changed.length})`), h("ul", { class: "changed" },
      ...diff.changed.map(({ before, after }) => h("li", {}, h("b", {}, after.title), ` (${after.source}): `,
        COMPARED_FIELDS.filter((f) => before[f] !== after[f])
          .map((f) => `${f}: "${before[f]}" → "${after[f]}"`).join("; "), " ", link(after.url)))));
  }
  if (!diff.added.length && !diff.removed.length && !diff.changed.length) {
    parts.push(h("p", {}, "No changes."));
  } else if (diff.added.length) {
    parts.push(h("p", { class: "muted" },
      `${diff.added.length} new position(s) are marked NEW in the table above.`));
  }
  section.replaceChildren(...parts);
}

function render(): void {
  renderRuns();
  renderSummary();
  renderTable();
  renderChanges();
}

// --- startup -------------------------------------------------------------------------

const api = (path: string, params: Record<string, string> = {}) =>
  `/api/${path}?${new URLSearchParams({ dataset: state.dataset, ...params })}`;

async function loadSnapshot(name: string): Promise<void> {
  state.data = await getJson<SnapshotResponse>(api("snapshot", { name }));
  buildHeader();
  render();
}

function wireFilters(): void {
  const q = byId<HTMLInputElement>("q");
  q.addEventListener("input", () => { state.query = q.value; renderTable(); });
  const onlyNew = byId<HTMLInputElement>("only-new");
  onlyNew.addEventListener("change", () => { state.onlyNew = onlyNew.checked; renderTable(); });
  byId("clear").addEventListener("click", () => {
    resetFilters();
    if (!state.data) return;
    buildHeader();
    renderTable();
  });
}

function resetFilters(): void {
  state.query = byId<HTMLInputElement>("q").value = "";
  state.onlyNew = byId<HTMLInputElement>("only-new").checked = false;
  state.columns = emptyFilters();
}

function clearPage(): void {
  state.data = null;
  for (const id of ["runs", "summary", "changes", "count"]) byId(id).replaceChildren();
  byId("failed").hidden = true;
  const table = byId<HTMLTableElement>("jobs");
  table.tHead!.replaceChildren();
  table.tBodies[0].replaceChildren();
}

/** Show one watch list: its runs in the picker and its latest snapshot. */
async function openDataset(dataset: Dataset): Promise<void> {
  state.dataset = dataset;
  history.replaceState(null, "", `#${dataset}`);
  for (const tab of document.querySelectorAll<HTMLButtonElement>(".tabs [data-dataset]")) {
    tab.setAttribute("aria-selected", String(tab.dataset.dataset === dataset));
  }
  byId("subtitle").textContent = DATASETS[dataset].subtitle;
  byId("error").hidden = true;
  resetFilters();
  clearPage();

  const picker = byId<HTMLSelectElement>("snapshot");
  picker.replaceChildren();
  const names = await getJson<string[]>(api("snapshots"));
  if (!names.length) {
    throw new Error(`No snapshots yet for this list: run \`${DATASETS[dataset].command}\` first.`);
  }
  picker.replaceChildren(...names.map((n, i) =>
    h("option", { value: n }, runLabel(n) + (i === 0 ? " (latest)" : ""))));
  await loadSnapshot(names[0]);
}

function main(): void {
  wireFilters();
  const picker = byId<HTMLSelectElement>("snapshot");
  picker.addEventListener("change", () => {
    byId("error").hidden = true;
    loadSnapshot(picker.value).catch(showError);
  });
  for (const tab of document.querySelectorAll<HTMLButtonElement>(".tabs [data-dataset]")) {
    tab.addEventListener("click", () => openDataset(tab.dataset.dataset as Dataset).catch(showError));
  }
  const fromHash = location.hash.slice(1);
  openDataset(fromHash in DATASETS ? fromHash as Dataset : "banks").catch(showError);
}

main();
