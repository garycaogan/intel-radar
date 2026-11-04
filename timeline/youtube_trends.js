// Refresh timeline/youtube_trends.json for the 热点时间线 page's YouTube 历史热点 section.
// Google Trends has no API: open https://trends.google.com/trends/?hl=en in a browser first (that sets its cookie,
// otherwise the explore call answers 429), then any explore page, paste this into the console and run
//   await refresh(["kpop demon hunters", "squid game", ...])
// It asks for each keyword's own weekly curve (YouTube Search, worldwide) and once more next to the anchor for the
// peak ratio, 3 s apart, and copies the JSON to the clipboard. Keep the keyword spelling used in youtube_hot.json; the anchor must be one of them.
const TIME = "2025-01-01 " + new Date().toISOString().slice(0, 10);
const ANCHOR = "kpop demon hunters";
async function trend(keywords) {
  const req = { comparisonItem: keywords.map((k) => ({ keyword: k, geo: "", time: TIME })), category: 0, property: "youtube" };
  const ex = await fetch(`/trends/api/explore?hl=en-US&tz=480&req=${encodeURIComponent(JSON.stringify(req))}`, { method: "POST" }).then((r) => r.text());
  const w = JSON.parse(ex.slice(ex.indexOf("{"))).widgets.find((x) => x.id === "TIMESERIES");
  const d = await fetch(`/trends/api/widgetdata/multiline?hl=en-US&tz=480&req=${encodeURIComponent(JSON.stringify(w.request))}&token=${w.token}`).then((r) => r.text());
  return JSON.parse(d.slice(d.indexOf("{"))).default.timelineData.map((p) => [Number(p.time), p.value]);
}
async function refresh(keywords) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const series = {};
  let start = 0;
  for (const k of keywords) {
    const solo = await trend([k]);
    await sleep(3000);
    start = solo[0][0];
    let ratio = 1;
    if (k !== ANCHOR) {
      const pair = await trend([k, ANCHOR]);
      await sleep(3000);
      const pk = Math.max(...pair.map((p) => p[1][0])), pa = Math.max(...pair.map((p) => p[1][1]));
      ratio = pa ? +(pk / pa).toFixed(3) : null;
    }
    series[k] = { values: solo.map((p) => p[1][0]), ratio };
  }
  const out = { fetched: new Date().toISOString().slice(0, 10), source: "Google Trends，YouTube 搜索类目（gprop=youtube），全球，按周",
                time: TIME, anchor: ANCHOR, start, series };
  copy(JSON.stringify(out));
  return out;
}
