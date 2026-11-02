// Charts the JSON reports written by `pypi-tracker build`. No dependencies and no build step.

const config = JSON.parse(document.body.dataset.config);
const cardsElement = document.getElementById("cards");
const rangesElement = document.getElementById("ranges");
const messageElement = document.getElementById("message");
const tooltip = document.getElementById("tooltip");

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const CHART_HEIGHT = 200;
const MARGIN = { top: 10, right: 14, bottom: 26, left: 46 };

const ranges = [
  ...config.rolling_days.map((days) => ({ report: `last-${days}-days`, label: `Last ${days} days` })),
  ...config.calendar.map((name) => ({ report: name, label: name[0].toUpperCase() + name.slice(1) })),
];

let selected = ranges.find((range) => range.report === "last-30-days") ?? ranges[0];
let drawnCharts = [];
const reportCache = new Map();

// ----- Loading -----

function loadReport(packageName, report) {
  const url = `../packages/${encodeURIComponent(packageName)}/${report}.json`;
  if (!reportCache.has(url)) {
    const request = fetch(url)
      .then((response) => (response.ok ? response.json() : null))
      .catch(() => null);
    reportCache.set(url, request);
  }
  return reportCache.get(url);
}

async function render() {
  const range = selected;
  const packages = await Promise.all(
    config.packages.map(async (name) => ({
      name,
      report: await loadReport(name, range.report),
      summary: config.summary ? await loadReport(name, "summary") : null,
    })),
  );
  if (range !== selected) return; // a newer button click has already taken over

  drawnCharts = [];
  cardsElement.replaceChildren(...packages.map((item) => buildCard(item, range)));
  drawnCharts.forEach(drawChart);
}

// ----- Turning reports into something to display -----

// Daily reports have `observations`; calendar reports have `periods`.
function toPoints(report) {
  if (report.observations) {
    return report.observations.map((day) => ({
      label: day.date,
      downloads: day.downloads,
      note: day.downloads === null ? "No data recorded" : "",
    }));
  }
  return report.periods.map((period) => ({
    label: String(period.week ?? period.month ?? period.year),
    downloads: period.downloads,
    partial: !period.complete,
    note: period.complete ? "" : `Partial: ${period.recorded_days} of ${period.days} days recorded`,
  }));
}

function headline(report, range) {
  if (report.observations) {
    return {
      value: report.downloads,
      caption: `downloads, ${range.label.toLowerCase()}`,
      warning: report.complete ? "" : `Incomplete: ${report.missing_days} of ${report.period.days} days missing`,
    };
  }
  // A partial period would read as a sudden drop, so lead with the latest complete one.
  const unit = { weekly: "week", monthly: "month", yearly: "year" }[range.report];
  const period = report.periods.filter((item) => item.complete).at(-1);
  if (!period) return { value: null, caption: `no complete ${unit} recorded yet`, warning: "" };
  return {
    value: period.downloads,
    caption: `downloads, latest complete ${unit} (${period[unit]})`,
    warning: "",
  };
}

function coverageText(summary) {
  if (!summary || summary.earliest_date === null) return "";
  let text = `${formatNumber(summary.recorded_downloads)} recorded downloads since ${summary.earliest_date}, as of ${summary.as_of}`;
  if (summary.missing_days > 0) text += `, ${formatNumber(summary.missing_days)} days missing`;
  return text;
}

function formatNumber(value) {
  return value.toLocaleString();
}

// ----- Page elements -----

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function buildCard({ name, report, summary }, range) {
  const card = element("article", "card");
  const title = element("h2");
  const link = element("a", "", name);
  link.href = `https://pypi.org/project/${encodeURIComponent(name)}/`;
  title.append(link);
  card.append(title);

  if (!report) {
    card.append(element("p", "caption warning", `Could not load the ${range.report} report.`));
    return card;
  }

  const points = toPoints(report);
  if (points.length === 0) {
    card.append(element("p", "caption", "No data recorded yet."));
    return card;
  }

  const { value, caption, warning } = headline(report, range);
  card.append(element("div", "value", value === null ? "–" : formatNumber(value)));
  card.append(element("p", "caption", caption));
  if (warning) card.append(element("p", "caption warning", warning));

  const chart = element("div", "chart");
  card.append(chart);
  drawnCharts.push({ container: chart, points, isDaily: Boolean(report.observations), name, range });

  const note = chartNote(points);
  if (note) card.append(note);

  const coverage = coverageText(summary);
  if (coverage) card.append(element("p", "coverage", coverage));
  card.append(buildTable(points));
  return card;
}

function chartNote(points) {
  if (points.some((point) => point.partial)) {
    const note = element("p", "note");
    note.append(element("span", "swatch"), "Lighter bars are partial periods.");
    return note;
  }
  if (points.some((point) => point.downloads === null)) {
    return element("p", "note", "Gaps in the line are days with no recorded data.");
  }
  return null;
}

// Every value on the chart is also readable here, without hovering. Newest first.
function buildTable(points) {
  const details = element("details");
  details.append(element("summary", "", "Data table"));

  const table = element("table");
  const header = element("tr");
  header.append(element("th", "", "Period"), element("th", "number", "Downloads"), element("th", "", "Note"));
  const head = element("thead");
  head.append(header);
  const body = element("tbody");
  for (const point of [...points].reverse()) {
    const row = element("tr");
    row.append(
      element("td", "", point.label),
      element("td", "number", point.downloads === null ? "–" : formatNumber(point.downloads)),
      element("td", "", point.note),
    );
    body.append(row);
  }
  table.append(head, body);

  const scroll = element("div", "table-scroll");
  scroll.append(table);
  details.append(scroll);
  return details;
}

// ----- Charts -----

function svgElement(parent, tag, attributes) {
  const node = document.createElementNS(SVG_NAMESPACE, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  parent.append(node);
  return node;
}

// Round axis steps of 1, 2 or 5 times a power of ten.
function niceTicks(maximum) {
  const roughStep = Math.max(maximum, 1) / 3;
  const power = 10 ** Math.floor(Math.log10(roughStep));
  const niceStep = [1, 2, 5, 10].map((factor) => factor * power).find((candidate) => candidate >= roughStep);
  const step = Math.max(niceStep, 1); // downloads are whole numbers
  const ticks = [0];
  while (ticks.at(-1) < maximum || ticks.length === 1) ticks.push(ticks.at(-1) + step);
  return ticks;
}

function formatTick(value, axisMaximum) {
  if (value === 0) return "0";
  if (axisMaximum >= 1000000000) return `${value / 1000000000}B`;
  if (axisMaximum >= 1000000) return `${value / 1000000}M`;
  if (axisMaximum >= 10000) return `${value / 1000}K`;
  return value.toLocaleString("en-US");
}

function drawChart({ container, points, isDaily, name, range }) {
  const width = container.clientWidth;
  if (width === 0) return; // not laid out right now; the ResizeObserver redraws when it is
  const left = MARGIN.left;
  const right = width - MARGIN.right;
  const top = MARGIN.top;
  const bottom = CHART_HEIGHT - MARGIN.bottom;

  const known = points.map((point) => point.downloads).filter((value) => value !== null);
  const ticks = niceTicks(Math.max(0, ...known));
  const axisMaximum = ticks.at(-1);
  const y = (value) => bottom - (value / axisMaximum) * (bottom - top);

  const step = (right - left) / (isDaily ? Math.max(points.length - 1, 1) : points.length);
  const x = (index) => {
    if (!isDaily) return left + step * (index + 0.5);
    return points.length === 1 ? (left + right) / 2 : left + step * index;
  };

  const svg = svgElement(container, "svg", {
    height: CHART_HEIGHT,
    viewBox: `0 0 ${width} ${CHART_HEIGHT}`,
    role: "img",
    tabindex: 0,
    "aria-label": `${name} downloads, ${range.label}. Values are in the data table below.`,
  });
  container.replaceChildren(svg);

  for (const tick of ticks) {
    svgElement(svg, "line", {
      class: tick === 0 ? "axis-line" : "grid",
      x1: left,
      x2: right,
      y1: y(tick),
      y2: y(tick),
    });
    const label = svgElement(svg, "text", { class: "axis-label", x: left - 8, y: y(tick) + 4, "text-anchor": "end" });
    label.textContent = formatTick(tick, axisMaximum);
  }

  // Show the first and last labels, plus the middle one if there is room for it.
  const lastIndex = points.length - 1;
  const labelled = new Set([0, lastIndex, ...(width > 380 ? [Math.floor(lastIndex / 2)] : [])]);
  for (const index of labelled) {
    // A line's ends sit on the chart's edges; a bar's label stays centred under the bar.
    let anchor = "middle";
    if (isDaily && points.length > 1 && index === 0) anchor = "start";
    if (isDaily && points.length > 1 && index === lastIndex) anchor = "end";
    const label = svgElement(svg, "text", {
      class: "axis-label",
      x: labelPosition(x(index), anchor, left, right),
      y: CHART_HEIGHT - 8,
      "text-anchor": anchor,
    });
    label.textContent = points[index].label;
  }

  // Hover marks go first so that the data draws on top of them.
  const band = svgElement(svg, "rect", { class: "band", y: top, width: step, height: bottom - top, visibility: "hidden" });
  const crosshair = svgElement(svg, "line", { class: "crosshair", y1: top, y2: bottom, visibility: "hidden" });
  if (isDaily) drawLine(svg, points, x, y, bottom);
  else drawBars(svg, points, x, y, bottom, step);
  const hoverDot = svgElement(svg, "circle", { class: "dot", r: 4, visibility: "hidden" });

  let active = lastIndex;

  function show(index) {
    active = index;
    const point = points[index];
    const pointY = point.downloads === null ? (top + bottom) / 2 : y(point.downloads);
    if (isDaily) {
      crosshair.setAttribute("x1", x(index));
      crosshair.setAttribute("x2", x(index));
      crosshair.setAttribute("visibility", "visible");
      hoverDot.setAttribute("cx", x(index));
      hoverDot.setAttribute("cy", pointY);
      hoverDot.setAttribute("visibility", point.downloads === null ? "hidden" : "visible");
    } else {
      band.setAttribute("x", x(index) - step / 2);
      band.setAttribute("visibility", "visible");
    }
    showTooltip(point, svg.getBoundingClientRect(), x(index), pointY);
  }

  function hide() {
    for (const mark of [band, crosshair, hoverDot]) mark.setAttribute("visibility", "hidden");
    tooltip.hidden = true;
  }

  svg.addEventListener("pointermove", (event) => {
    const position = event.clientX - svg.getBoundingClientRect().left - left;
    const index = isDaily ? Math.round(position / step) : Math.floor(position / step);
    show(Math.min(Math.max(index, 0), lastIndex));
  });
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("focus", () => show(active));
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (event) => {
    const move = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (!move) return;
    event.preventDefault();
    show(Math.min(Math.max(active + move, 0), lastIndex));
  });
}

// The outer labels align to the chart's edges, and a centred label must not run off them.
function labelPosition(position, anchor, left, right) {
  if (anchor === "start") return left;
  if (anchor === "end") return right;
  return Math.min(Math.max(position, left + 30), right - 30);
}

// Days with no data split the line into runs: a missing day is a gap, never a zero.
function drawLine(svg, points, x, y, bottom) {
  let run = [];
  const finishRun = () => {
    if (run.length === 1) {
      svgElement(svg, "circle", { class: "dot", r: 4, cx: x(run[0]), cy: y(points[run[0]].downloads) });
    } else if (run.length > 1) {
      const path = run.map((index, position) => `${position ? "L" : "M"}${x(index)} ${y(points[index].downloads)}`).join(" ");
      svgElement(svg, "path", { class: "area", d: `${path} L${x(run.at(-1))} ${bottom} L${x(run[0])} ${bottom} Z` });
      svgElement(svg, "path", { class: "line", d: path });
    }
    run = [];
  };
  points.forEach((point, index) => {
    if (point.downloads === null) finishRun();
    else run.push(index);
  });
  const lastPoint = points.length - 1;
  const endsWithData = run.length > 1;
  finishRun();
  if (endsWithData) {
    svgElement(svg, "circle", { class: "dot", r: 4, cx: x(lastPoint), cy: y(points[lastPoint].downloads) });
  }
}

function drawBars(svg, points, x, y, bottom, step) {
  const width = Math.min(24, Math.max(step - 2, 1));
  points.forEach((point, index) => {
    if (!point.downloads) return; // a zero or missing period has no bar to draw
    const barLeft = x(index) - width / 2;
    const barRight = barLeft + width;
    const barTop = y(point.downloads);
    const radius = Math.min(4, width / 2, bottom - barTop);
    svgElement(svg, "path", {
      class: point.partial ? "bar partial" : "bar",
      d: `M${barLeft} ${bottom} V${barTop + radius} Q${barLeft} ${barTop} ${barLeft + radius} ${barTop}
          H${barRight - radius} Q${barRight} ${barTop} ${barRight} ${barTop + radius} V${bottom} Z`,
    });
  });
}

function showTooltip(point, chartBox, pointX, pointY) {
  const value = point.downloads === null ? "No data" : `${formatNumber(point.downloads)} downloads`;
  tooltip.replaceChildren(element("strong", "", value), element("span", "", point.label));
  if (point.note && point.downloads !== null) tooltip.append(element("span", "", point.note));
  tooltip.hidden = false;

  const centred = chartBox.left + pointX - tooltip.offsetWidth / 2;
  const widest = document.documentElement.clientWidth - tooltip.offsetWidth - 8;
  tooltip.style.left = `${Math.min(Math.max(centred, 8), widest)}px`;
  tooltip.style.top = `${Math.max(chartBox.top + pointY - 10, tooltip.offsetHeight + 8)}px`;
}

// ----- Startup -----

function buildRangeButtons() {
  for (const range of ranges) {
    const button = element("button", "", range.label);
    button.type = "button";
    button.setAttribute("aria-pressed", range === selected);
    button.addEventListener("click", () => {
      selected = range;
      for (const other of rangesElement.children) other.setAttribute("aria-pressed", other === button);
      render();
    });
    rangesElement.append(button);
  }
}

// Browsers block fetching local files, so the page has to be served over HTTP.
if (location.protocol === "file:") {
  messageElement.hidden = false;
  messageElement.textContent = "This page must be served over HTTP. Run ";
  messageElement.append(element("code", "", "python -m http.server"));
  messageElement.append(" in the output directory, then open http://localhost:8000/dashboard/.");
} else {
  buildRangeButtons();
  render();

  let lastWidth = cardsElement.clientWidth;
  new ResizeObserver(() => {
    if (cardsElement.clientWidth === lastWidth) return;
    lastWidth = cardsElement.clientWidth;
    drawnCharts.forEach(drawChart);
  }).observe(cardsElement);
}
