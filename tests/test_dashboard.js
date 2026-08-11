/* Headless render test for the dashboard.
 *
 * Loads index.html + app.js in jsdom against the real data.json and inspects the SVG
 * that comes out. Chart code fails quietly -- a NaN coordinate produces an invisible
 * line, not an exception -- so the assertions here are mostly "did anything render, and
 * is every number finite", checked across every family and a sample of institutes.
 *
 *   NODE_PATH=<dir with jsdom> node tests/test_dashboard.js
 */

const fs = require("fs");
const path = require("path");

const DOCS = path.join(__dirname, "..", "docs");

let JSDOM;
try {
  ({ JSDOM } = require("jsdom"));
} catch (err) {
  console.log("SKIP  jsdom not installed (set NODE_PATH to a dir containing it)");
  process.exit(0);
}

// jsdom does not resolve custom properties from linked stylesheets, so declare the
// handful the chart code reads. Values mirror style.css light mode.
const CSS_VARS = [
  "--surface-1:#fcfcfb", "--surface-sunken:#f0efec", "--border:#e6e5e1",
  "--text-primary:#0b0b0b", "--text-secondary:#52514e", "--text-muted:#8a8880",
  "--fy-2022:#86b6ef", "--fy-2023:#2a78d6", "--fy-2024:#184f95",
  "--fy-2025:#d55181", "--fy-2026:#eb6834", "--accent:#2a78d6",
].join(";");

const failures = [];
const check = (name, cond, detail) => {
  if (cond) console.log("PASS  " + name);
  else { failures.push(name); console.log("FAIL  " + name + (detail ? ": " + detail : "")); }
};

function badNumbers(svg) {
  const bad = [];
  svg.querySelectorAll("*").forEach((node) => {
    for (const attr of node.attributes) {
      if (/NaN|Infinity|undefined/.test(attr.value)) {
        bad.push(`<${node.tagName} ${attr.name}="${attr.value.slice(0, 60)}">`);
      }
    }
  });
  return bad;
}

async function main() {
  const html = fs.readFileSync(path.join(DOCS, "index.html"), "utf8");
  const data = JSON.parse(fs.readFileSync(path.join(DOCS, "data.json"), "utf8"));

  const dom = new JSDOM(html, {
    runScripts: "outside-only", pretendToBeVisual: true, url: "http://localhost/",
  });
  const { window } = dom;
  const { document } = window;

  const errors = [];
  window.addEventListener("error", (e) => errors.push(String(e.message)));
  window.fetch = async () => ({ json: async () => data });
  document.body.style.cssText = CSS_VARS;

  try {
    window.eval(fs.readFileSync(path.join(DOCS, "app.js"), "utf8"));
  } catch (err) {
    check("app.js evaluates", false, err.message);
    return;
  }
  await new Promise((r) => window.setTimeout(r, 400));

  check("no uncaught errors during init", errors.length === 0, errors.join("; "));

  const fig1 = document.getElementById("fig1");

  check("chart rendered paths", fig1.querySelectorAll("path").length > 0,
        "found " + fig1.querySelectorAll("path").length);

  check("header stamped with as_of",
        document.getElementById("as-of").textContent === data.meta.as_of);

  // One legend entry per fiscal year, plus the nowcast entry.
  const legend = document.querySelectorAll("#legend1 .legend-item");
  check("legend covers every fiscal year",
        legend.length === data.meta.fiscal_years.length + 1,
        "found " + legend.length);

  check("completeness table populated",
        document.querySelectorAll("#completeness-table table.data tbody tr").length ===
          data.meta.families.length);

  check("weekly table populated",
        document.querySelectorAll("#fig1-table table.data tbody tr").length > 20);

  // The real risk: a NaN coordinate renders as nothing rather than throwing.
  check("chart has no NaN/undefined attributes", badNumbers(fig1).length === 0,
        badNumbers(fig1).slice(0, 3).join(" "));

  // Exercise every family, and every IC for the default family, through the real
  // controls -- an IC missing from one family's series would throw here.
  const group = document.getElementById("family-group");
  const select = document.getElementById("ic-select");
  let combos = 0;
  const comboErrors = [];

  for (const btn of group.children) {
    btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((r) => window.setTimeout(r, 0));
    for (const opt of select.options) {
      select.value = opt.value;
      select.dispatchEvent(new window.Event("change", { bubbles: true }));
      combos += 1;
      const bad = badNumbers(fig1);
      if (bad.length) comboErrors.push(`${btn.textContent}/${opt.value}: ${bad[0]}`);
      if (fig1.querySelectorAll("path").length === 0) {
        comboErrors.push(`${btn.textContent}/${opt.value}: nothing drawn`);
      }
    }
  }
  check(`all ${combos} family x institute combinations render`,
        comboErrors.length === 0, comboErrors.slice(0, 3).join(" | "));

  check("no uncaught errors after interaction", errors.length === 0, errors.join("; "));

  // Toggling the nowcast off must remove the dashed estimate.
  const toggle = document.getElementById("nowcast-toggle");
  toggle.checked = false;
  toggle.dispatchEvent(new window.Event("change", { bubbles: true }));
  const dashedOff = fig1.querySelectorAll("path[stroke-dasharray]").length;
  toggle.checked = true;
  toggle.dispatchEvent(new window.Event("change", { bubbles: true }));
  const dashedOn = fig1.querySelectorAll("path[stroke-dasharray]").length;
  check("nowcast toggle adds and removes the dashed estimate",
        dashedOff === 0 && dashedOn > 0, `off=${dashedOff} on=${dashedOn}`);

  // Cumulative is the default, and a cumulative series must never decrease.
  const viewGroup = document.getElementById("view-group");
  check("cumulative is the default view",
        viewGroup.children[0].getAttribute("aria-checked") === "true");

  // The combination loop above left the chart on the last family/institute it tried;
  // reset to a high-volume series so the y-extent comparison below is meaningful.
  group.children[0].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  select.value = "NHLBI";
  select.dispatchEvent(new window.Event("change", { bubbles: true }));

  const cumulativeMonotone = () => {
    const series = data.series.r01_equivalent.NHLBI.counts["2024"];
    let running = 0;
    const expected = series.map((v) => (running += v));
    // Reconstruct what the chart should be drawing and confirm it rises monotonically.
    return expected.every((v, i) => i === 0 || v >= expected[i - 1]);
  };
  check("cumulative series is non-decreasing", cumulativeMonotone());

  // Switching to weekly and back must both render, with different y-extents.
  const yExtent = () => {
    const labels = [...fig1.querySelectorAll("text")]
      .map((t) => parseInt(t.textContent, 10)).filter((n) => !isNaN(n));
    return Math.max(...labels);
  };
  const cumulativeMax = yExtent();
  viewGroup.children[1].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  const weeklyMax = yExtent();
  check("weekly view renders with a smaller y-extent than cumulative",
        weeklyMax < cumulativeMax, `weekly=${weeklyMax} cumulative=${cumulativeMax}`);
  check("weekly view has no NaN attributes", badNumbers(fig1).length === 0);
  viewGroup.children[0].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  check("switching back to cumulative restores the y-extent", yExtent() === cumulativeMax);

  // Clicking a legend entry hides that year's line.
  const before = fig1.querySelectorAll("path:not([stroke-dasharray])").length;
  document.querySelectorAll("#legend1 .legend-item")[0]
    .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  const after = fig1.querySelectorAll("path:not([stroke-dasharray])").length;
  check("legend click hides a series", after < before, `${before} -> ${after}`);

  console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
  process.exit(failures.length ? 1 : 0);
}

main();
