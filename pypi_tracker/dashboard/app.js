// Draws the dashboard from the JSON reports `pypi-tracker build` writes. No dependencies or build step.
//
// The default view loads one small aggregate report. Anything else (a subset of packages, a long
// range, one line per package) fetches each package's yearly files and combines them here.

const config = JSON.parse(document.body.dataset.config);
const byId = (id) => document.getElementById(id);

const RANGES = [
  ["7d", "7D"],
  ["30d", "30D"],
  ["90d", "90D"],
  ["180d", "180D"],
  ["365d", "1Y"],
  ["all", "All"],
  ["custom", "Custom"],
];
const MAX_SERIES = 8; // the palette has eight validated colors
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const THEME_KEY = "pypi-tracker-theme";

const state = readUrl();
const hiddenSeries = new Set();
const sort = { key: "downloads", descending: true };
const jsonCache = new Map();
let view = null; // the data currently on screen
let drawn = null; // the chart currently on screen
let renderCount = 0;
let controlUpdates = [];

// ----- URL state -----

function readUrl() {
  const query = new URLSearchParams(location.search);
  const pick = (name, allowed, fallback) => (allowed.includes(query.get(name)) ? query.get(name) : fallback);
  const chosen = (query.get("packages") ?? "").split(",").filter((name) => config.packages.includes(name));
  const range = pick("range", RANGES.map(([key]) => key), "30d");
  const start = query.get("start");
  const end = query.get("end");
  const validCustom = isDate(start) && isDate(end) && start <= end;
  return {
    range: range === "custom" && !validCustom ? "30d" : range,
    start: validCustom ? start : null,
    end: validCustom ? end : null,
    granularity: pick("granularity", ["daily", "weekly", "monthly"], null),
    view: pick("view", ["combined", "packages"], "combined"),
    packages: chosen.length ? chosen : [...config.packages],
  };
}

function writeUrl() {
  const query = new URLSearchParams();
  if (state.range !== "30d") query.set("range", state.range);
  if (state.range === "custom") {
    query.set("start", state.start);
    query.set("end", state.end);
  }
  if (!allSelected()) query.set("packages", state.packages.join(","));
  if (state.granularity) query.set("granularity", state.granularity);
  if (state.view !== "combined") query.set("view", state.view);
  const text = query.toString().replaceAll("%2C", ",");
  history.replaceState(null, "", text ? `?${text}` : location.pathname);
}

function allSelected() {
  return state.packages.length === config.packages.length;
}

// ----- Dates (always UTC, as plain YYYY-MM-DD strings) -----

function isDate(text) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text ?? "")) return false;
  const parsed = new Date(`${text}T00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().startsWith(text);
}

function addDays(day, count) {
  const moved = new Date(`${day}T00:00Z`);
  moved.setUTCDate(moved.getUTCDate() + count);
  return moved.toISOString().slice(0, 10);
}

function datesBetween(start, end) {
  const dates = [];
  for (let day = start; day <= end; day = addDays(day, 1)) dates.push(day);
  return dates;
}

function formatDate(day, options = { month: "short", day: "numeric", year: "numeric" }) {
  return new Date(`${day}T00:00Z`).toLocaleDateString(undefined, { timeZone: "UTC", ...options });
}

// ----- Loading -----

function loadJson(path) {
  if (!jsonCache.has(path)) {
    const request = fetch(path)
      .then((response) => (response.ok ? response.json() : null))
      .catch(() => null); // a missing or malformed file is handled the same way as a failed request
    jsonCache.set(path, request);
  }
  return jsonCache.get(path);
}

function packagePath(name, file) {
  return `packages/${encodeURIComponent(name)}/${file}.json`;
}

function rangeDays() {
  return state.range.endsWith("d") ? Number(state.range.slice(0, -1)) : null;
}

async function loadView() {
  const days = rangeDays();
  const canUseAggregate =
    allSelected() && config.packages.length > 1 && state.view === "combined" && config.rolling_days.includes(days);
  if (canUseAggregate) {
    const report = await loadJson(`aggregate/last-${days}-days.json`);
    if (report?.as_of && report.breakdown) return viewFromAggregate(report);
  }
  return viewFromPackages();
}

function viewFromAggregate(report) {
  const known = report.observations.filter((day) => day.downloads !== null);
  return {
    dates: report.observations.map((day) => day.date),
    asOf: report.as_of,
    combined: new Map(known.map((day) => [day.date, day.downloads])),
    histories: null,
    total: {
      downloads: report.downloads,
      recorded: report.recorded_days,
      missing: report.missing_days,
      complete: report.complete,
    },
    previous: report.previous?.complete ? report.previous.downloads : null,
    compareLabel: `previous ${report.period.days} days`,
    rows: report.breakdown.map((row) => ({
      name: row.package,
      downloads: row.downloads,
      recorded: row.recorded_days,
      missing: row.missing_days,
      previous: row.previous_downloads,
    })),
    failed: [],
    summary: null,
  };
}

async function viewFromPackages() {
  const summaries = new Map(
    await Promise.all(state.packages.map(async (name) => [name, await loadJson(packagePath(name, "summary"))])),
  );
  const failed = state.packages.filter((name) => !summaries.get(name));
  const usable = [...summaries.values()].filter(Boolean);
  const withData = usable.filter((summary) => summary.latest_date);
  const empty = { dates: [], asOf: null, rows: [], failed, combined: new Map(), histories: null, summary: null };
  if (!withData.length) return { ...empty, total: tally(new Map(), []), previous: null };

  // Every report ends on the latest date any selected package has, matching aggregation.py.
  const anchor = withData.map((summary) => summary.latest_date).sort().at(-1);
  const earliest = withData.map((summary) => summary.earliest_date).sort()[0];
  let start = earliest;
  let end = anchor;
  if (state.range === "custom") {
    start = state.start > earliest ? state.start : earliest;
    end = state.end < anchor ? state.end : anchor;
  } else if (rangeDays()) {
    start = addDays(anchor, 1 - rangeDays());
  }
  const dates = start <= end ? datesBetween(start, end) : [];
  if (!dates.length) return { ...empty, asOf: anchor, total: tally(new Map(), []), previous: null };

  const compare = state.range !== "all";
  const previousDates = compare ? datesBetween(addDays(start, -dates.length), addDays(start, -1)) : [];
  const firstNeeded = previousDates[0] ?? start;

  const histories = new Map();
  await Promise.all(
    usable.map(async (summary) => {
      const name = summary.package;
      const history = await loadHistory(name, summary, firstNeeded, end);
      if (history) histories.set(name, history);
      else failed.push(name);
    }),
  );

  const names = state.packages.filter((name) => histories.has(name));
  const combined = combine(names.map((name) => histories.get(name)));
  const previousTotal = tally(combined, previousDates);
  return {
    dates,
    asOf: anchor,
    combined,
    histories,
    total: tally(combined, dates),
    previous: compare && previousTotal.complete ? previousTotal.downloads : null,
    compareLabel: `previous ${dates.length} days`,
    rows: names.map((name) => {
      const now = tally(histories.get(name), dates);
      const before = tally(histories.get(name), previousDates);
      return {
        name,
        downloads: now.downloads,
        recorded: now.recorded,
        missing: now.missing,
        previous: compare && before.complete ? before.downloads : null,
      };
    }),
    failed,
    summary: state.packages.length === 1 ? usable[0] ?? null : null,
  };
}

// One package's observations between two dates, fetching only the years they cover.
async function loadHistory(name, summary, start, end) {
  const history = new Map();
  if (!summary.latest_date) return history;
  const firstYear = Number((start > summary.earliest_date ? start : summary.earliest_date).slice(0, 4));
  const lastYear = Number((end < summary.latest_date ? end : summary.latest_date).slice(0, 4));
  for (let year = firstYear; year <= lastYear; year += 1) {
    const report = await loadJson(packagePath(name, `daily/${year}`));
    if (!report) return null;
    for (const day of report.observations) history.set(day.date, day.downloads);
  }
  return history;
}

// ----- Calculations (these mirror aggregation.py; a missing day is never treated as zero) -----

function tally(history, dates) {
  let downloads = 0;
  let recorded = 0;
  for (const day of dates) {
    if (history.has(day)) {
      downloads += history.get(day);
      recorded += 1;
    }
  }
  return {
    downloads: recorded ? downloads : null,
    recorded,
    missing: dates.length - recorded,
    complete: recorded === dates.length,
  };
}

// A date counts when any package has it; absent packages add nothing.
function combine(histories) {
  const combined = new Map();
  for (const history of histories) {
    for (const [day, downloads] of history) combined.set(day, (combined.get(day) ?? 0) + downloads);
  }
  return combined;
}

function changeBetween(now, before) {
  return now === null || before === null || before === 0 ? null : (now - before) / before;
}

function effectiveGranularity(dateCount) {
  if (state.granularity) return state.granularity;
  if (dateCount > 730) return "monthly";
  return dateCount > 180 ? "weekly" : "daily";
}

function mondayOf(day) {
  const weekday = new Date(`${day}T00:00Z`).getUTCDay();
  return addDays(day, -((weekday + 6) % 7));
}

function daysInMonth(day) {
  const [year, month] = day.split("-").map(Number);
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function groupDates(dates, granularity) {
  const groups = new Map();
  for (const day of dates) {
    const key = { daily: day, weekly: mondayOf(day), monthly: day.slice(0, 7) }[granularity];
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(day);
  }
  return [...groups.entries()].map(([key, days]) => {
    if (granularity === "daily") {
      return { days, whole: true, short: formatDate(key, { month: "short", day: "numeric" }), long: formatDate(key) };
    }
    if (granularity === "weekly") {
      return { days, whole: days.length === 7, short: formatDate(key, { month: "short", day: "numeric" }), long: `Week of ${formatDate(key)}` };
    }
    const label = formatDate(days[0], { month: "short", year: "numeric" });
    return { days, whole: days.length === daysInMonth(days[0]), short: label, long: label };
  });
}

function pointsFor(history, groups) {
  return groups.map((group) => {
    const count = tally(history, group.days);
    return { downloads: count.downloads, partial: count.downloads !== null && (!count.complete || !group.whole), recorded: count.recorded };
  });
}

// ----- Formatting -----

const formatNumber = (value) => (value === null ? "—" : Math.round(value).toLocaleString());

function formatChange(change) {
  if (change === null) return "—";
  const percent = (Math.abs(change) * 100).toFixed(1);
  if (Number(percent) === 0) return "0.0%"; // never "−0.0%"
  return `${change > 0 ? "+" : "−"}${percent}%`;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ----- Controls -----

function addSegments(parent, label, options, current, choose) {
  const group = element("div", "segments");
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", label);
  for (const [value, text] of options) {
    const button = element("button", "", text);
    button.type = "button";
    button.dataset.value = value;
    button.addEventListener("click", () => choose(value));
    group.append(button);
  }
  parent.append(group);
  controlUpdates.push(() => {
    for (const button of group.children) button.setAttribute("aria-pressed", button.dataset.value === current());
  });
  return group;
}

function buildControls(meta) {
  const rangeOptions = RANGES.filter(([key]) => {
    if (key !== "365d" || !meta?.first_observed_date || !meta?.latest_date) return true;
    return meta.first_observed_date <= addDays(meta.latest_date, -364); // only when there's a year of history
  });
  addSegments(byId("controls"), "Date range", rangeOptions, () => state.range, chooseRange);
  byId("controls").append(buildCustomRange(meta), buildPackageMenu());

  const chartControls = byId("chart-controls");
  addSegments(
    chartControls,
    "Granularity",
    [["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"]],
    () => effectiveGranularity(view?.dates.length ?? 30),
    (value) => {
      state.granularity = value;
      render();
    },
  );
  const viewSwitch = addSegments(
    chartControls,
    "Display",
    [["combined", "Combined"], ["packages", "By package"]],
    () => state.view,
    (value) => {
      state.view = value;
      render();
    },
  );
  controlUpdates.push(() => {
    viewSwitch.hidden = state.packages.length === 1;
  });
}

function chooseRange(value) {
  if (value === "custom") {
    state.start = view?.dates[0] ?? state.start;
    state.end = view?.dates.at(-1) ?? state.end;
    if (!state.start) return; // no data to base a custom range on
  }
  state.range = value;
  render();
}

function buildCustomRange(meta) {
  const wrapper = element("div", "custom-range");
  const inputs = ["start", "end"].map((key) => {
    const label = element("label", "", key === "start" ? "From" : "To");
    const input = element("input");
    input.type = "date";
    if (meta?.first_observed_date) input.min = meta.first_observed_date;
    if (meta?.latest_date) input.max = meta.latest_date;
    input.addEventListener("change", () => {
      if (!isDate(input.value)) return;
      state[key] = input.value;
      if (state.start > state.end) [state.start, state.end] = [state.end, state.start];
      render();
    });
    label.append(input);
    wrapper.append(label);
    return [key, input];
  });
  controlUpdates.push(() => {
    wrapper.hidden = state.range !== "custom";
    for (const [key, input] of inputs) input.value = state[key] ?? "";
  });
  return wrapper;
}

function buildPackageMenu() {
  const menu = element("details", "menu");
  const summary = element("summary");
  const panel = element("div", "menu-panel");
  const everything = element("button", "link-button", "Select all");
  everything.type = "button";
  everything.addEventListener("click", () => selectPackages([...config.packages]));
  panel.append(everything);

  const boxes = config.packages.map((name) => {
    const label = element("label");
    const box = element("input");
    box.type = "checkbox";
    box.addEventListener("change", () => {
      const next = config.packages.filter((other, index) => boxes[index].checked);
      selectPackages(next.length ? next : [...config.packages]);
    });
    label.append(box, element("span", "", name));
    panel.append(label);
    return box;
  });
  menu.append(summary, panel);

  document.addEventListener("click", (event) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
  menu.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.open) {
      menu.open = false;
      summary.focus();
    }
  });
  controlUpdates.push(() => {
    summary.textContent = allSelected()
      ? `All packages (${config.packages.length})`
      : state.packages.length === 1 ? state.packages[0] : `${state.packages.length} of ${config.packages.length} packages`;
    boxes.forEach((box, index) => {
      box.checked = state.packages.includes(config.packages[index]);
    });
  });
  return menu;
}

function selectPackages(packages) {
  state.packages = packages;
  render();
}

function setupThemeToggle() {
  const button = byId("theme-toggle");
  const current = () => document.documentElement.dataset.theme
    ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const update = () => {
    const other = current() === "dark" ? "light" : "dark";
    button.setAttribute("aria-label", `Switch to ${other} theme`);
    button.title = `Switch to ${other} theme`;
  };
  button.addEventListener("click", () => {
    const next = current() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // the choice just won't be remembered next visit
    }
    update();
  });
  update();
}

// ----- Page sections -----

function showMessage(text) {
  byId("message").hidden = !text;
  byId("message").textContent = text;
}

function renderMessages() {
  const notes = [];
  if (view.failed.length) notes.push(`Could not load ${view.failed.join(", ")}, so the figures below leave them out.`);
  if (!view.dates.length && !view.failed.length) notes.push("No download data for this selection. Run pypi-tracker sync to collect some.");
  showMessage(notes.join(" "));
}

function renderDetail() {
  const section = byId("detail");
  const name = state.packages.length === 1 ? state.packages[0] : null;
  section.hidden = !name;
  if (!name) return;
  const back = element("button", "link-button", "← All packages");
  back.type = "button";
  back.addEventListener("click", () => selectPackages([...config.packages]));
  const title = element("h2");
  const link = element("a", "", name);
  link.href = `https://pypi.org/project/${encodeURIComponent(name)}/`;
  title.append(link);
  const facts = element("p", "note");
  if (view.summary?.earliest_date) {
    facts.textContent = `Recorded from ${formatDate(view.summary.earliest_date)} to ${formatDate(view.summary.latest_date)}`;
  }
  section.replaceChildren(back, title, facts);
}

function metric(label, value, note) {
  const box = element("div", "metric");
  box.append(element("div", "metric-label", label), element("div", "metric-value", value), element("div", "metric-note", note));
  return box;
}

function renderOverview() {
  const { dates, total, previous, asOf } = view;
  const period = dates.length ? `${formatDate(dates[0])} – ${formatDate(dates.at(-1))}` : "No data";
  const change = total.complete ? changeBetween(total.downloads, previous) : null;
  const average = total.recorded ? total.downloads / total.recorded : null;
  const lastMetric = view.summary
    ? metric("Total recorded", formatNumber(view.summary.recorded_downloads), "All collected history")
    : metric("Packages tracked", formatNumber(view.rows.length || state.packages.length), allSelected() ? "In this collection" : `Of ${config.packages.length} configured`);
  const boxes = [
    metric("Downloads", formatNumber(total.downloads), period),
    metric("Daily average", formatNumber(average), asOf ? `Data through ${formatDate(asOf)}` : "No data"),
    metric("Change", formatChange(change), change === null ? "No reliable comparison" : `vs ${view.compareLabel}`),
    lastMetric,
  ];
  byId("overview").replaceChildren(...boxes);
  if (dates.length && !total.complete) {
    const scope = state.packages.length > 1 ? "any selected package" : "this package";
    const warning = element("p", "incomplete", `Incomplete: ${total.missing} of ${dates.length} days have no data for ${scope}. They are left out of these figures, not counted as zero.`);
    byId("overview").append(warning);
  }
}

// ----- Chart -----

function seriesForChart() {
  if (state.view === "packages" && view.histories && state.packages.length > 1) {
    const largest = [...view.rows].sort((a, b) => (b.downloads ?? -1) - (a.downloads ?? -1)).slice(0, MAX_SERIES);
    const names = config.packages.filter((name) => largest.some((row) => row.name === name));
    // Colors follow the package, so filtering doesn't repaint the others (up to the palette's size).
    const colorOrder = config.packages.length <= MAX_SERIES ? config.packages : names;
    return names.map((name) => ({
      name,
      history: view.histories.get(name),
      color: `var(--series-${colorOrder.indexOf(name) + 1})`,
    }));
  }
  const name = state.packages.length === 1 ? state.packages[0] : "Combined";
  return [{ name, history: view.combined, color: "var(--accent)" }];
}

function renderChart() {
  const groups = groupDates(view.dates, effectiveGranularity(view.dates.length));
  const series = seriesForChart().map((item) => ({ ...item, points: pointsFor(item.history, groups) }));
  drawn = { groups, series };
  renderLegend(series);
  drawChart();
  renderChartTable();
  renderChartNote();
}

function visibleSeries() {
  const { series } = drawn;
  return series.length === 1 ? series : series.filter((item) => !hiddenSeries.has(item.name));
}

function renderLegend(series) {
  const legend = byId("legend");
  legend.hidden = series.length < 2;
  legend.replaceChildren(
    ...series.map((item) => {
      const button = element("button");
      button.type = "button";
      button.setAttribute("aria-pressed", !hiddenSeries.has(item.name));
      const swatch = element("span", "swatch");
      swatch.style.background = item.color;
      button.append(swatch, item.name);
      button.addEventListener("click", () => {
        if (hiddenSeries.has(item.name)) hiddenSeries.delete(item.name);
        else hiddenSeries.add(item.name);
        button.setAttribute("aria-pressed", !hiddenSeries.has(item.name));
        drawChart();
        renderChartTable();
      });
      return button;
    }),
  );
}

function renderChartNote() {
  const notes = [];
  const points = drawn.series.flatMap((item) => item.points);
  if (points.some((point) => point.partial)) notes.push("Hollow points are partial periods.");
  if (points.some((point) => point.downloads === null)) notes.push("Gaps have no data, which isn't the same as zero downloads.");
  if (state.view === "packages" && state.packages.length > MAX_SERIES) {
    notes.push(`Showing the ${MAX_SERIES} largest packages. Choose packages above to see others.`);
  }
  byId("chart-note").textContent = notes.join(" ");
}

function renderChartTable() {
  const series = visibleSeries();
  const table = element("table");
  const head = element("tr");
  head.append(scopedHeader("Period"), ...series.map((item) => scopedHeader(item.name, "number")), scopedHeader("Note"));
  const thead = element("thead");
  thead.append(head);
  const body = element("tbody");
  for (let index = drawn.groups.length - 1; index >= 0; index -= 1) {
    const row = element("tr");
    const rowHeader = element("th", "", drawn.groups[index].long);
    rowHeader.scope = "row";
    const points = series.map((item) => item.points[index]);
    row.append(
      rowHeader,
      ...points.map((point) => element("td", "number", formatNumber(point.downloads))),
      element("td", "", points.some((point) => point.partial) ? "Partial" : points.some((point) => point.downloads === null) ? "Missing data" : ""),
    );
    body.append(row);
  }
  table.append(thead, body);
  byId("chart-table").replaceChildren(table);
}

function scopedHeader(text, className) {
  const header = element("th", className, text);
  header.scope = "col";
  return header;
}

function svgElement(parent, tag, attributes) {
  const node = document.createElementNS(SVG_NAMESPACE, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  parent.append(node);
  return node;
}

// Round axis steps of 1, 2 or 5 times a power of ten.
function niceTicks(maximum) {
  const roughStep = Math.max(maximum, 1) / 4;
  const power = 10 ** Math.floor(Math.log10(roughStep));
  const niceStep = [1, 2, 5, 10].map((factor) => factor * power).find((candidate) => candidate >= roughStep);
  const step = Math.max(niceStep, 1); // downloads are whole numbers
  const ticks = [0];
  while (ticks.at(-1) < maximum || ticks.length === 1) ticks.push(ticks.at(-1) + step);
  return ticks;
}

function formatTick(value, axisMaximum) {
  if (value === 0) return "0";
  if (axisMaximum >= 1e9) return `${value / 1e9}B`;
  if (axisMaximum >= 1e6) return `${value / 1e6}M`;
  if (axisMaximum >= 1e4) return `${value / 1e3}K`;
  return value.toLocaleString();
}

function drawChart() {
  const container = byId("chart");
  const width = container.clientWidth;
  if (!drawn || width === 0) return; // not laid out right now; the ResizeObserver redraws when it is
  hideTooltip();
  const { groups } = drawn;
  const series = visibleSeries();
  if (!groups.length || !series.length) {
    const text = groups.length ? "All series are hidden. Choose one in the legend." : "No data for this selection.";
    container.replaceChildren(element("p", "state", text));
    return;
  }

  const height = width < 560 ? 240 : 340;
  const left = 48;
  const right = width - 12;
  const top = 12;
  const bottom = height - 28;
  const known = series.flatMap((item) => item.points.map((point) => point.downloads)).filter((value) => value !== null);
  if (!known.length) {
    container.replaceChildren(element("p", "state", "No date in this range has data for every selected package."));
    return;
  }
  const ticks = niceTicks(Math.max(0, ...known));
  const axisMaximum = ticks.at(-1);
  const y = (value) => bottom - (value / axisMaximum) * (bottom - top);
  const lastIndex = groups.length - 1;
  const x = (index) => (lastIndex ? left + ((right - left) * index) / lastIndex : (left + right) / 2);

  const svg = svgElement(container, "svg", {
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    tabindex: 0,
    "aria-label": `Downloads from ${groups[0].long} to ${groups.at(-1).long}. Use the arrow keys to read values, or view the table below.`,
  });
  container.replaceChildren(svg);

  for (const tick of ticks) {
    svgElement(svg, "line", { class: tick ? "grid" : "axis-line", x1: left, x2: right, y1: y(tick), y2: y(tick) });
    const label = svgElement(svg, "text", { class: "axis-label", x: left - 8, y: y(tick) + 4, "text-anchor": "end" });
    label.textContent = formatTick(tick, axisMaximum);
  }

  // As many evenly spaced date labels as fit, with the outer ones aligned to the chart's edges.
  const labelCount = Math.min(Math.max(Math.floor(width / 110), 2), groups.length);
  const labelled = new Set(Array.from({ length: labelCount }, (_, i) => (labelCount > 1 ? Math.round((lastIndex * i) / (labelCount - 1)) : 0)));
  for (const index of labelled) {
    const anchor = lastIndex === 0 ? "middle" : index === 0 ? "start" : index === lastIndex ? "end" : "middle";
    const label = svgElement(svg, "text", { class: "axis-label", x: x(index), y: height - 8, "text-anchor": anchor });
    label.textContent = groups[index].short;
  }

  const crosshair = svgElement(svg, "line", { class: "crosshair", y1: top, y2: bottom, visibility: "hidden" });
  series.forEach((item) => drawSeries(svg, item, x, y, bottom, series.length === 1));
  const hoverDots = series.map((item) => svgElement(svg, "circle", { class: "dot", r: 4, style: `fill: ${item.color}`, visibility: "hidden" }));

  let active = lastIndex;

  function show(index) {
    active = Math.min(Math.max(index, 0), lastIndex);
    crosshair.setAttribute("x1", x(active));
    crosshair.setAttribute("x2", x(active));
    crosshair.setAttribute("visibility", "visible");
    series.forEach((item, position) => {
      const { downloads } = item.points[active];
      hoverDots[position].setAttribute("visibility", downloads === null ? "hidden" : "visible");
      if (downloads !== null) {
        hoverDots[position].setAttribute("cx", x(active));
        hoverDots[position].setAttribute("cy", y(downloads));
      }
    });
    showTooltip(active, series, svg.getBoundingClientRect(), x(active), top);
  }

  function hide() {
    crosshair.setAttribute("visibility", "hidden");
    hoverDots.forEach((dot) => dot.setAttribute("visibility", "hidden"));
    hideTooltip();
  }

  const showAtPointer = (event) => {
    const position = event.clientX - svg.getBoundingClientRect().left;
    show(lastIndex ? Math.round(((position - left) / (right - left)) * lastIndex) : 0);
  };
  svg.addEventListener("pointermove", showAtPointer);
  svg.addEventListener("pointerdown", showAtPointer);
  // A finger lifting isn't a pointer leaving: a tap's tooltip stays until the next tap elsewhere.
  svg.addEventListener("pointerleave", (event) => {
    if (event.pointerType !== "touch") hide();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!svg.contains(event.target)) hide();
  }, { signal: replaceChartListeners() });
  svg.addEventListener("focus", () => {
    if (svg.matches(":focus-visible")) show(active);
  });
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (event) => {
    const move = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (!move) return;
    event.preventDefault();
    show(active + move);
  });
}

// Each redraw makes a new chart, so the previous chart's page-level listener has to go.
let chartListeners = null;
function replaceChartListeners() {
  chartListeners?.abort();
  chartListeners = new AbortController();
  return chartListeners.signal;
}

// Days with no data split the line into runs: a missing day is a gap, never a zero.
function drawSeries(svg, item, x, y, bottom, showArea) {
  const { points, color } = item;
  const runs = [];
  points.forEach((point, index) => {
    if (point.downloads === null) return;
    if (index && points[index - 1].downloads !== null) runs.at(-1).push(index);
    else runs.push([index]);
  });
  for (const run of runs) {
    if (run.length > 1) {
      const path = run.map((index, position) => `${position ? "L" : "M"}${x(index)} ${y(points[index].downloads)}`).join(" ");
      if (showArea) svgElement(svg, "path", { class: "area", style: `fill: ${color}`, d: `${path} L${x(run.at(-1))} ${bottom} L${x(run[0])} ${bottom} Z` });
      svgElement(svg, "path", { class: "line", style: `stroke: ${color}`, d: path });
    } else {
      svgElement(svg, "circle", { class: "dot", r: 4, style: `fill: ${color}`, cx: x(run[0]), cy: y(points[run[0]].downloads) });
    }
  }
  points.forEach((point, index) => {
    if (point.partial) svgElement(svg, "circle", { class: "hollow", r: 4, style: `stroke: ${color}`, cx: x(index), cy: y(point.downloads) });
  });
}

function showTooltip(index, series, chartBox, pointX, top) {
  const tooltip = byId("tooltip");
  const group = drawn.groups[index];
  const rows = series
    .map((item) => ({ item, point: item.points[index] }))
    .sort((a, b) => (b.point.downloads ?? -1) - (a.point.downloads ?? -1))
    .map(({ item, point }) => {
      const row = element("div", "tooltip-row");
      const name = element("span", "tooltip-name");
      if (series.length > 1) {
        const swatch = element("span", "swatch");
        swatch.style.background = item.color;
        name.append(swatch, item.name);
      } else {
        name.textContent = "Downloads";
      }
      row.append(name, element("strong", "", point.downloads === null ? "No data" : point.downloads.toLocaleString()));
      return row;
    });
  tooltip.replaceChildren(element("div", "tooltip-title", group.long), ...rows);
  const partial = series.find((item) => item.points[index].partial)?.points[index];
  if (partial) tooltip.append(element("div", "tooltip-note", `Partial: ${partial.recorded} of ${group.whole ? group.days.length : `${group.days.length} selected`} days recorded`));
  tooltip.hidden = false;

  const widest = document.documentElement.clientWidth - tooltip.offsetWidth - 8;
  const beside = chartBox.left + pointX + 12;
  const flipped = chartBox.left + pointX - tooltip.offsetWidth - 12;
  tooltip.style.left = `${Math.max(beside > widest ? flipped : beside, 8)}px`;
  tooltip.style.top = `${Math.max(chartBox.top + top, 8)}px`;
}

function hideTooltip() {
  byId("tooltip").hidden = true;
}

// ----- Breakdown table -----

function renderBreakdown() {
  byId("breakdown-panel").hidden = state.packages.length === 1; // the package's own page already says it all
  const total = view.rows.reduce((sum, row) => sum + (row.downloads ?? 0), 0);
  const rows = view.rows.map((row) => ({
    ...row,
    share: row.downloads === null || !total ? null : row.downloads / total,
    average: row.recorded ? row.downloads / row.recorded : null,
    change: row.missing ? null : changeBetween(row.downloads, row.previous),
  }));
  rows.sort((a, b) => {
    if (sort.key === "name") return sort.descending ? b.name.localeCompare(a.name) : a.name.localeCompare(b.name);
    if (a[sort.key] === null) return b[sort.key] === null ? 0 : 1; // no value always sorts last
    if (b[sort.key] === null) return -1;
    return sort.descending ? b[sort.key] - a[sort.key] : a[sort.key] - b[sort.key];
  });

  const headers = [
    ["name", "Package", ""],
    ["downloads", "Downloads", "number"],
    ["share", "Share", "number"],
    ["average", "Daily average", "number"],
    ["change", "Change", "number"],
  ];
  const head = element("tr");
  for (const [key, text, className] of headers) {
    const header = scopedHeader("", className);
    const button = element("button", "sort", text);
    button.type = "button";
    if (sort.key === key) {
      header.setAttribute("aria-sort", sort.descending ? "descending" : "ascending");
      button.append(element("span", "sort-mark", sort.descending ? " ↓" : " ↑"));
    }
    button.addEventListener("click", () => {
      sort.descending = sort.key === key ? !sort.descending : key !== "name";
      sort.key = key;
      renderBreakdown();
      byId("breakdown").querySelector(`[data-sort="${key}"]`).focus();
    });
    button.dataset.sort = key;
    header.append(button);
    head.append(header);
  }

  const body = element("tbody");
  for (const row of rows) body.append(breakdownRow(row));
  const thead = element("thead");
  thead.append(head);
  const table = element("table", "breakdown");
  table.append(thead, body);
  byId("breakdown").replaceChildren(rows.length ? table : element("p", "state", "No packages to show."));

  const incomplete = rows.some((row) => row.missing) || !view.total.complete;
  byId("breakdown-note").textContent = incomplete
    ? "Rows count each package's own recorded days. Change is shown only when both periods are complete."
    : "";
}

function breakdownRow(row) {
  const tr = element("tr");
  const nameCell = element("th");
  nameCell.scope = "row";
  const open = element("button", "link-button", row.name);
  open.type = "button";
  open.addEventListener("click", () => selectPackages([row.name]));
  const pypi = element("a", "pypi-link", "↗");
  pypi.href = `https://pypi.org/project/${encodeURIComponent(row.name)}/`;
  pypi.setAttribute("aria-label", `${row.name} on PyPI`);
  nameCell.append(open, pypi);

  const shareCell = element("td", "number", row.share === null ? "—" : `${(row.share * 100).toFixed(1)}%`);
  if (row.share !== null) {
    const bar = element("div", "share-bar");
    bar.style.width = `${row.share * 100}%`;
    shareCell.append(bar);
  }
  tr.append(
    nameCell,
    element("td", "number", formatNumber(row.downloads)),
    shareCell,
    element("td", "number", formatNumber(row.average)),
    element("td", "number", formatChange(row.change)),
  );
  return tr;
}

// ----- Startup -----

async function render() {
  const current = ++renderCount;
  writeUrl();
  controlUpdates.forEach((update) => update());
  byId("main").setAttribute("aria-busy", "true");
  const loaded = await loadView();
  if (current !== renderCount) return; // a newer choice has already taken over
  view = loaded;
  controlUpdates.forEach((update) => update());
  renderMessages();
  renderDetail();
  renderOverview();
  renderChart();
  renderBreakdown();
  byId("main").setAttribute("aria-busy", "false");
}

async function start() {
  setupThemeToggle();
  // Browsers block fetching local files, so the page has to be served over HTTP.
  if (location.protocol === "file:") {
    byId("chart").replaceChildren();
    showMessage("This page must be served over HTTP. Run pypi-tracker serve, then open http://localhost:8000/.");
    byId("main").setAttribute("aria-busy", "false");
    return;
  }
  buildControls(await loadJson("aggregate/summary.json"));
  await render();

  let lastWidth = byId("chart").clientWidth;
  new ResizeObserver(() => {
    if (byId("chart").clientWidth === lastWidth) return;
    lastWidth = byId("chart").clientWidth;
    drawChart();
  }).observe(byId("chart"));
}

start();
