/* Dashboard for new NIH awards, corrected for RePORTER's reporting lag.
 *
 * Hand-rolled SVG rather than a charting library: the two figures need specific
 * behaviour (a provisional/settled split along a single line, a masked tail, banded
 * uncertainty) that is more work to talk a general library out of than to draw. No
 * build step and no CDN, so the page also opens straight off disk.
 */

const SVG_NS = "http://www.w3.org/2000/svg";
const state = { data: null, ic: "NHLBI", family: "r01_equivalent", nowcast: true, hidden: new Set() };

/* ---------------------------------------------------------------- small helpers */

const el = (tag, attrs = {}, parent = null) => {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  if (parent) parent.appendChild(node);
  return node;
};

const cssVar = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();
const fyColor = (fy) => cssVar(`--fy-${fy}`) || cssVar("--accent");
const fmt = (n) => (n === null || n === undefined ? "—" : Math.round(n).toLocaleString());

function scaleLinear(d0, d1, r0, r1) {
  const span = d1 - d0 || 1;
  const f = (v) => r0 + ((v - d0) / span) * (r1 - r0);
  f.invert = (p) => d0 + ((p - r0) / (r1 - r0)) * span;
  return f;
}

/* Split a series into runs of consecutive non-null points, so a masked tail leaves a
 * genuine gap instead of a straight line bridging across missing weeks. */
function segments(values) {
  const out = [];
  let run = [];
  values.forEach((v, i) => {
    if (v === null || v === undefined) {
      if (run.length) out.push(run);
      run = [];
    } else {
      run.push([i + 1, v]);
    }
  });
  if (run.length) out.push(run);
  return out;
}

const linePath = (pts, x, y) =>
  pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");

/* ------------------------------------------------------------------- figure one */

function drawWeekly() {
  const { data } = state;
  const meta = data.meta;
  const entry = data.series[state.family][state.ic];
  const svg = document.getElementById("fig1");
  svg.textContent = "";

  const width = svg.clientWidth || 900;
  const height = 400;
  const pad = { t: 12, r: 16, b: 34, l: 46 };
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("height", height);

  const years = meta.fiscal_years;
  const visible = years.filter((fy) => !state.hidden.has(String(fy)));
  const nc = entry.nowcast;
  const showNowcast = state.nowcast && !state.hidden.has(String(meta.current_fiscal_year));

  // Scale to the observed counts and the point estimate only. The band's upper bound
  // on a barely-reported week runs several times the point estimate, and letting it
  // set the axis would flatten four years of real data to read one uncertain week.
  // The band is clipped to the plot area instead.
  let peak = 1;
  visible.forEach((fy) => entry.counts[fy].forEach((v) => { if (v > peak) peak = v; }));
  if (showNowcast) nc.point.forEach((v, i) => {
    if (v !== null && nc.status[i] === "provisional" && v > peak) peak = v;
  });

  const x = scaleLinear(1, meta.weeks_in_year, pad.l, width - pad.r);
  const y = scaleLinear(0, peak * 1.08, height - pad.b, pad.t);

  const grid = cssVar("--border");
  const ink2 = cssVar("--text-secondary");
  const muted = cssVar("--text-muted");

  /* Shade the stretch of the fiscal year the current-year data does not cover yet. */
  const edge = meta.last_observed_week[String(meta.current_fiscal_year)];
  if (edge && edge < meta.weeks_in_year) {
    el("rect", {
      x: x(edge), y: pad.t, width: x(meta.weeks_in_year) - x(edge),
      height: height - pad.b - pad.t, fill: cssVar("--surface-sunken"), opacity: .6,
    }, svg);
  }

  // Axes: recessive, horizontal rules plus a tick at each month boundary.
  const ticks = y.invert ? niceTicks(0, peak * 1.08) : [];
  ticks.forEach((t) => {
    el("line", { x1: pad.l, x2: width - pad.r, y1: y(t), y2: y(t), stroke: grid, "stroke-width": 1 }, svg);
    const label = el("text", { x: pad.l - 8, y: y(t) + 4, "text-anchor": "end", fill: muted, "font-size": 11 }, svg);
    label.textContent = t;
  });
  meta.month_ticks.forEach((m) => {
    el("line", { x1: x(m.week), x2: x(m.week), y1: pad.t, y2: height - pad.b, stroke: grid, "stroke-width": 1 }, svg);
    const label = el("text", { x: x(m.week), y: height - pad.b + 16, "text-anchor": "middle", fill: muted, "font-size": 11 }, svg);
    label.textContent = m.label;
  });

  const yTitle = el("text", {
    x: 12, y: pad.t + (height - pad.t - pad.b) / 2, fill: muted, "font-size": 11,
    "text-anchor": "middle", transform: `rotate(-90 12 ${pad.t + (height - pad.t - pad.b) / 2})`,
  }, svg);
  yTitle.textContent = "Awards issued that week";

  // Uncertainty band around the lag-corrected estimate, clipped to the plot area.
  const clipId = "plot-clip";
  const defs = el("defs", {}, svg);
  el("rect", {
    x: pad.l, y: pad.t, width: width - pad.l - pad.r, height: height - pad.t - pad.b,
  }, el("clipPath", { id: clipId }, defs));

  if (showNowcast) {
    const band = [];
    const back = [];
    nc.status.forEach((s, i) => {
      if (s !== "provisional") return;
      band.push([x(i + 1), y(nc.high[i])]);
      back.unshift([x(i + 1), y(nc.low[i])]);
    });
    if (band.length > 1) {
      const pts = band.concat(back).map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
      el("polygon", {
        points: pts, fill: fyColor(meta.current_fiscal_year), opacity: .16,
        "clip-path": `url(#${clipId})`,
      }, svg);
    }
  }

  // One line per fiscal year, drawn oldest first so the current year sits on top.
  years.forEach((fy) => {
    if (state.hidden.has(String(fy))) return;
    const isCurrent = fy === meta.current_fiscal_year;
    const counts = entry.counts[fy].slice();
    // Trim the current year at its data edge rather than letting it fall to zero.
    const trimmed = counts.map((v, i) => (isCurrent && edge && i + 1 > edge ? null : v));

    segments(trimmed).forEach((seg) => {
      el("path", {
        d: linePath(seg, x, y), fill: "none", stroke: fyColor(fy),
        "stroke-width": isCurrent ? 2.5 : 1.8, "stroke-linecap": "round",
        "stroke-linejoin": "round", opacity: isCurrent ? 1 : .95,
      }, svg);
    });
  });

  // The corrected estimate, dashed so it never reads as observed data.
  if (showNowcast) {
    const pts = nc.status.map((s, i) => (s === "provisional" ? nc.point[i] : null));
    // Join the dashed run to the last settled point so the line does not float free.
    const lastSettled = nc.status.lastIndexOf("settled");
    if (lastSettled >= 0) pts[lastSettled] = entry.counts[meta.current_fiscal_year][lastSettled];
    segments(pts).forEach((seg) => {
      el("path", {
        d: linePath(seg, x, y), fill: "none", stroke: fyColor(meta.current_fiscal_year),
        "stroke-width": 2.5, "stroke-dasharray": "6 4", "stroke-linecap": "round",
      }, svg);
    });
  }

  drawCrosshair(svg, { width, height, pad, x, y, entry, meta, showNowcast, edge });
  renderLegend(meta, showNowcast);
  updateWeeklyCopy(entry, meta, edge);
  buildWeeklyTable(entry, meta, edge);
}

function niceTicks(lo, hi) {
  const raw = (hi - lo) / 5;
  const mag = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1))));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10;
  const out = [];
  for (let v = 0; v <= hi; v += step) out.push(Math.round(v));
  return out;
}

/* Crosshair + tooltip: the default interaction for a line chart, and the mechanism
 * that lets five overlapping series be read precisely at any week. */
function drawCrosshair(svg, ctx) {
  const { width, height, pad, x, y, entry, meta, showNowcast, edge } = ctx;
  const tip = document.getElementById("tooltip1");
  const holder = svg.parentElement;

  const rule = el("line", {
    y1: pad.t, y2: height - pad.b, stroke: cssVar("--text-muted"),
    "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0,
  }, svg);
  const dots = el("g", { opacity: 0 }, svg);

  const hit = el("rect", {
    x: pad.l, y: pad.t, width: width - pad.l - pad.r, height: height - pad.t - pad.b,
    fill: "transparent", style: "cursor:crosshair",
  }, svg);

  const hide = () => { rule.setAttribute("opacity", 0); dots.setAttribute("opacity", 0); tip.hidden = true; };

  hit.addEventListener("mouseleave", hide);
  hit.addEventListener("mousemove", (event) => {
    const box = svg.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const week = Math.min(meta.weeks_in_year, Math.max(1, Math.round(x.invert(px))));

    rule.setAttribute("x1", x(week));
    rule.setAttribute("x2", x(week));
    rule.setAttribute("opacity", 1);
    dots.textContent = "";
    dots.setAttribute("opacity", 1);

    const rows = [];
    meta.fiscal_years.forEach((fy) => {
      if (state.hidden.has(String(fy))) return;
      const isCurrent = fy === meta.current_fiscal_year;
      if (isCurrent && edge && week > edge) return;
      const v = entry.counts[fy][week - 1];
      el("circle", { cx: x(week), cy: y(v), r: 4.5, fill: fyColor(fy),
        stroke: cssVar("--surface-1"), "stroke-width": 2 }, dots);
      rows.push({ label: `FY${fy}`, color: fyColor(fy), value: fmt(v) });
    });

    const nc = entry.nowcast;
    let footer = "";
    if (showNowcast && nc.status[week - 1] === "provisional") {
      const est = nc.point[week - 1];
      el("circle", { cx: x(week), cy: y(est), r: 4.5, fill: "none",
        stroke: fyColor(meta.current_fiscal_year), "stroke-width": 2 }, dots);
      rows.push({
        label: `FY${meta.current_fiscal_year} corrected`,
        color: fyColor(meta.current_fiscal_year),
        value: `${fmt(est)}`,
        dashed: true,
      });
      footer = `${Math.round(nc.fraction[week - 1] * 100)}% reported so far · 95% range ${fmt(nc.low[week - 1])}–${fmt(nc.high[week - 1])}`;
    } else if (nc.status[week - 1] === "masked" && week <= (edge || 0)) {
      footer = "too little reported to estimate";
    }

    tip.innerHTML =
      `<h4>${weekLabel(meta, week)}</h4>` +
      `<table>${rows.map((r) =>
        `<tr><td><span class="sw" style="background:${r.color}"></span>${r.label}</td>` +
        `<td class="v">${r.value}</td></tr>`).join("")}</table>` +
      (footer ? `<div class="muted" style="margin-top:6px">${footer}</div>` : "");

    tip.hidden = false;
    const tw = tip.offsetWidth;
    const left = x(week) / width * holder.clientWidth;
    tip.style.left = `${Math.min(Math.max(left + 14, 4), holder.clientWidth - tw - 4)}px`;
    tip.style.top = `${pad.t + 6}px`;
  });
}

function weekLabel(meta, week) {
  const start = new Date(Date.UTC(meta.current_fiscal_year - 1, 9, 1));
  start.setUTCDate(start.getUTCDate() + (week - 1) * 7);
  const opts = { month: "short", day: "numeric", timeZone: "UTC" };
  return `Week ${week} · from ${start.toLocaleDateString("en-US", opts)}`;
}

function renderLegend(meta, showNowcast) {
  const box = document.getElementById("legend1");
  box.textContent = "";
  meta.fiscal_years.forEach((fy) => {
    const item = document.createElement("div");
    item.className = "legend-item";
    item.setAttribute("role", "listitem");
    item.dataset.dimmed = state.hidden.has(String(fy));
    item.innerHTML = `<span class="legend-swatch" style="background:${fyColor(fy)}"></span>FY${fy}`;
    item.title = "Click to show or hide this year";
    item.addEventListener("click", () => {
      const key = String(fy);
      if (state.hidden.has(key)) state.hidden.delete(key); else state.hidden.add(key);
      drawWeekly();
    });
    box.appendChild(item);
  });
  if (showNowcast) {
    const item = document.createElement("div");
    item.className = "legend-item";
    item.style.color = cssVar("--text-muted");
    item.innerHTML =
      `<span class="legend-swatch legend-swatch--dashed" style="color:${fyColor(meta.current_fiscal_year)}"></span>` +
      `lag-corrected estimate`;
    box.appendChild(item);
  }
}

function updateWeeklyCopy(entry, meta, edge) {
  const label = state.ic === "ALL" ? "all NIH institutes" : state.ic;
  document.getElementById("fig1-sub").textContent =
    `${meta.family_labels[state.family]} · ${label}`;

  const nc = entry.nowcast;
  const provisional = nc.status.filter((s) => s === "provisional").length;
  const masked = nc.status.filter((s, i) => s === "masked" && i + 1 <= (edge || 0)).length;

  const totals = meta.fiscal_years.map((fy) =>
    `FY${fy} ${entry.counts[fy].reduce((a, b) => a + b, 0).toLocaleString()}`).join(" · ");

  document.getElementById("fig1-note").textContent =
    `Full-year totals: ${totals}. FY${meta.current_fiscal_year} runs to week ${edge} of ${meta.weeks_in_year}; ` +
    `the last ${provisional} week${provisional === 1 ? "" : "s"} are still filling in and are shown corrected` +
    (masked ? `, and ${masked} week${masked === 1 ? " is" : "s are"} too incomplete to estimate.` : ".");
}

function buildWeeklyTable(entry, meta, edge) {
  const head = ["Week", ...meta.fiscal_years.map((fy) => `FY${fy}`), "FY" + meta.current_fiscal_year + " corrected"];
  const rows = [];
  for (let w = 1; w <= meta.weeks_in_year; w++) {
    const cells = meta.fiscal_years.map((fy) => {
      const isCurrent = fy === meta.current_fiscal_year;
      if (isCurrent && edge && w > edge) return "";
      return entry.counts[fy][w - 1];
    });
    const st = entry.nowcast.status[w - 1];
    const est = st === "provisional" ? fmt(entry.nowcast.point[w - 1])
      : st === "masked" ? "" : "";
    if (cells.every((c) => c === "" || c === 0) && !est) continue;
    rows.push(`<tr><td>${w}</td>${cells.map((c) => `<td>${c === "" ? "" : c}</td>`).join("")}<td class="est">${est}</td></tr>`);
  }
  document.getElementById("fig1-table").innerHTML =
    `<table class="data"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.join("")}</tbody></table>`;
}

/* ------------------------------------------------------------------- figure two */

function drawComparison() {
  const { data } = state;
  const meta = data.meta;
  const rows = data.comparison[state.family];
  const svg = document.getElementById("fig2");
  svg.textContent = "";

  if (!rows.length) {
    document.getElementById("fig2-note").textContent = "No agency has enough baseline history for this family.";
    return;
  }

  const width = svg.clientWidth || 900;
  const rowH = 21;
  const pad = { t: 26, r: 92, b: 30, l: 76 };
  const height = pad.t + pad.b + rows.length * rowH;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("height", height);

  const maxPace = Math.max(1.35, ...rows.map((r) => r.pace));
  const x = scaleLinear(0, maxPace, pad.l, width - pad.r);

  const grid = cssVar("--border");
  const muted = cssVar("--text-muted");
  const ink2 = cssVar("--text-secondary");

  // Every mark here is the same entity -- FY2026 -- so it wears FY2026's colour, the
  // same one it has in figure 1. Colouring by whether an institute is ahead or behind
  // would encode rank in hue, which makes the palette shift meaning between the two
  // figures and adds nothing position on the axis is not already saying.
  const markColor = cssVar("--fy-2026");

  [0, 0.25, 0.5, 0.75, 1, 1.25].filter((t) => t <= maxPace).forEach((t) => {
    const onPace = t === 1;
    el("line", {
      x1: x(t), x2: x(t), y1: pad.t - 8, y2: height - pad.b + 4,
      stroke: onPace ? muted : grid, "stroke-width": 1,
      "stroke-dasharray": onPace ? "4 3" : null,
    }, svg);
    const label = el("text", {
      x: x(t), y: pad.t - 14, "text-anchor": "middle",
      fill: onPace ? ink2 : muted, "font-size": 11,
      "font-weight": onPace ? 600 : 400,
    }, svg);
    label.textContent = onPace ? "on pace" : `${Math.round(t * 100)}%`;
  });

  rows.forEach((r, i) => {
    const cy = pad.t + i * rowH + rowH / 2;

    el("line", { x1: x(0), x2: x(r.pace), y1: cy, y2: cy, stroke: markColor,
      "stroke-width": 2, "stroke-linecap": "round", opacity: .45 }, svg);
    el("circle", { cx: x(r.pace), cy, r: 5, fill: markColor,
      stroke: cssVar("--surface-1"), "stroke-width": 2 }, svg);

    const name = el("text", { x: pad.l - 10, y: cy + 4, "text-anchor": "end",
      fill: ink2, "font-size": 12 }, svg);
    name.textContent = r.ic;

    const val = el("text", { x: width - pad.r + 10, y: cy + 4, fill: muted, "font-size": 11.5 }, svg);
    val.textContent = `${Math.round(r.pace * 100)}%  (${r.observed} vs ${Math.round(r.baseline)})`;

    const hit = el("rect", { x: pad.l - 70, y: cy - rowH / 2, width: width - pad.l + 70,
      height: rowH, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (e) => showPaceTip(e, r, meta));
    hit.addEventListener("mouseleave", () => { document.getElementById("tooltip2").hidden = true; });
  });

  const median = rows.map((r) => r.pace).sort((a, b) => a - b)[Math.floor(rows.length / 2)];
  document.getElementById("fig2-sub").textContent =
    `${meta.family_labels[state.family]} · lag-corrected awards so far this year vs the same point in FY${meta.baseline_fiscal_years[0]}–${meta.baseline_fiscal_years.slice(-1)[0]}`;
  document.getElementById("fig2-note").textContent =
    `Each agency is measured against its own recent history, since raw counts across agencies are not comparable ` +
    `(NCI funds an order of magnitude more R01s than NIDCD). Median agency is at ${Math.round(median * 100)}% of its ` +
    `normal pace through week ${meta.last_observed_week[String(meta.current_fiscal_year)]}. ` +
    `Non-NIH HHS agencies that RePORTER covers (AHRQ, FDA, NIOSH, NCIPC) are included. ` +
    `Agencies with fewer than 5 baseline awards are omitted.`;

  buildComparisonTable(rows, meta);
}

function showPaceTip(event, r, meta) {
  const tip = document.getElementById("tooltip2");
  const holder = event.target.ownerSVGElement.parentElement;
  const box = holder.getBoundingClientRect();
  const baseline = Object.entries(r.baseline_by_year)
    .map(([fy, n]) => `<tr><td>FY${fy}</td><td class="v">${n}</td></tr>`).join("");
  tip.innerHTML =
    `<h4>${r.ic}</h4><table>` +
    `<tr><td>Observed so far</td><td class="v">${r.observed}</td></tr>` +
    `<tr><td>Lag-corrected</td><td class="v">${r.corrected}</td></tr>` +
    `<tr><td>Baseline mean</td><td class="v">${r.baseline}</td></tr>` +
    `<tr><td><strong>Pace</strong></td><td class="v"><strong>${Math.round(r.pace * 100)}%</strong></td></tr>` +
    `</table><div class="muted" style="margin-top:6px">Same point in prior years</div>` +
    `<table>${baseline}</table>`;
  tip.hidden = false;
  tip.style.left = `${Math.min(event.clientX - box.left + 14, holder.clientWidth - tip.offsetWidth - 4)}px`;
  tip.style.top = `${Math.max(4, event.clientY - box.top - 40)}px`;
}

function buildComparisonTable(rows, meta) {
  const head = ["Agency", "Observed", "Corrected", "Baseline mean", "Pace"];
  const body = rows.map((r) =>
    `<tr><td>${r.ic}</td><td>${r.observed}</td><td class="est">${r.corrected}</td>` +
    `<td>${r.baseline}</td><td>${Math.round(r.pace * 100)}%</td></tr>`).join("");
  document.getElementById("fig2-table").innerHTML =
    `<table class="data"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table>`;
}

/* ---------------------------------------------------------------------- chrome */

function buildCompletenessTable() {
  const c = state.data.completeness;
  const labels = state.data.meta.family_labels;
  const days = [7, 14, 28, 56, 90, 180];
  const head = ["Award family", ...days.map((d) => `${d}d`), "median lag"];
  const body = state.data.meta.families.map((f) => {
    const row = c[f];
    return `<tr><td>${labels[f]}</td>` +
      days.map((d) => `<td>${row.checkpoints[d]}%</td>`).join("") +
      `<td>${row.median_lag ?? "—"} d</td></tr>`;
  }).join("");
  document.getElementById("completeness-table").innerHTML =
    `<table class="data"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table>` +
    `<p class="note">Share of awards visible in RePORTER within N days of their notice date, under the current reporting regime.</p>`;
}

function buildControls() {
  const meta = state.data.meta;

  const select = document.getElementById("ic-select");
  const all = document.createElement("option");
  all.value = "ALL";
  all.textContent = "All NIH institutes";
  select.appendChild(all);
  meta.ics.forEach((ic) => {
    const opt = document.createElement("option");
    opt.value = ic;
    opt.textContent = `${ic} — ${(meta.ic_totals[state.family][ic] || 0).toLocaleString()} awards`;
    select.appendChild(opt);
  });
  select.value = state.ic;
  select.addEventListener("change", () => { state.ic = select.value; drawWeekly(); });

  const group = document.getElementById("family-group");
  meta.families.forEach((f) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("role", "radio");
    btn.setAttribute("aria-checked", f === state.family);
    btn.textContent = meta.family_labels[f];
    btn.addEventListener("click", () => {
      state.family = f;
      [...group.children].forEach((c) => c.setAttribute("aria-checked", c === btn));
      refreshIcLabels();
      drawWeekly();
      drawComparison();
    });
    group.appendChild(btn);
  });

  document.getElementById("nowcast-toggle").addEventListener("change", (e) => {
    state.nowcast = e.target.checked;
    drawWeekly();
  });

  document.querySelectorAll("[data-table-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = document.getElementById(btn.dataset.tableToggle);
      target.hidden = !target.hidden;
      btn.textContent = target.hidden ? "Table" : "Hide table";
    });
  });

  const toggle = document.getElementById("theme-toggle");
  toggle.addEventListener("click", () => {
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    document.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
    try { localStorage.setItem("theme", dark ? "light" : "dark"); } catch (_) {}
    drawWeekly();
    drawComparison();
  });
}

function refreshIcLabels() {
  const meta = state.data.meta;
  const select = document.getElementById("ic-select");
  [...select.options].forEach((opt) => {
    if (opt.value === "ALL") return;
    opt.textContent = `${opt.value} — ${(meta.ic_totals[state.family][opt.value] || 0).toLocaleString()} awards`;
  });
}

function applyStoredTheme() {
  let stored = null;
  try { stored = localStorage.getItem("theme"); } catch (_) {}
  const dark = stored ? stored === "dark"
    : window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
}

async function init() {
  applyStoredTheme();
  const res = await fetch("data.json");
  state.data = await res.json();

  document.getElementById("as-of").textContent = state.data.meta.as_of;
  document.getElementById("award-count").textContent =
    state.data.meta.total_awards.toLocaleString();
  document.getElementById("fig2-title").textContent =
    `FY${state.data.meta.current_fiscal_year} pace, by institute or agency`;

  if (!state.data.series[state.family][state.ic]) state.ic = "ALL";

  buildControls();
  buildCompletenessTable();
  drawWeekly();
  drawComparison();

  let timer;
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { drawWeekly(); drawComparison(); }, 150);
  });
}

init();
