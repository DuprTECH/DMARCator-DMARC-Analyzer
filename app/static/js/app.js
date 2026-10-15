/* UnDMARC – UI helpers and ECharts builders */
(function () {
  "use strict";

  // ---------------------------------------------------------------- theme
  const root = document.documentElement;
  const stored = (() => { try { return localStorage.getItem("theme"); } catch (e) { return null; } })();
  if (stored) root.dataset.theme = stored;

  function isDark() {
    if (root.dataset.theme) return root.dataset.theme === "dark";
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  window.toggleTheme = function () {
    root.dataset.theme = isDark() ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (e) { /* ignore */ }
    redrawAll();
  };

  function v(name) { return getComputedStyle(root).getPropertyValue(name).trim(); }

  function palette() {
    return {
      text: v("--text-primary"), text2: v("--text-secondary"), muted: v("--text-muted"),
      grid: v("--grid"), surface: v("--surface-1"), surface2: v("--surface-2"),
      series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v("--series-" + i)),
      good: v("--good"), warning: v("--warning"), serious: v("--serious"), critical: v("--critical"),
      accent: v("--accent"),
    };
  }

  /** Resolve a palette key: status/accent names or "s1".."s8" categorical slots. */
  function colorOf(p, key) {
    if (/^s[1-8]$/.test(key)) return p.series[+key.slice(1) - 1];
    return p[key] || key;
  }

  const CAT_COLOR = { compliant: "good", forwarded: "accent", noncompliant: "warning", threat: "critical" };
  const LANG = root.lang === "en" ? "en" : "sk";
  /** Translate a Slovak UI string (dictionary provided by the server in window.I18N). */
  const t = (s) => ((window.I18N || {})[s] || s);
  const nf = new Intl.NumberFormat(LANG === "en" ? "en-US" : "sk-SK");
  const fmt = (n) => (n == null ? "–" : nf.format(n));
  const pct = (n) => {
    if (n == null) return "–";
    const s = n.toFixed(1) + "\u00a0%";
    return LANG === "en" ? s : s.replace(".", ",");
  };

  // -------------------------------------------------------------- registry
  const charts = [];
  function register(el, build) {
    if (!el || !window.echarts) return;
    const inst = echarts.init(el, null, { renderer: "svg" });
    const entry = { el, inst, build };
    charts.push(entry);
    inst.setOption(build(palette()), true);
    new ResizeObserver(() => inst.resize()).observe(el);
    return inst;
  }
  function redrawAll() {
    charts.forEach((c) => c.inst.setOption(c.build(palette()), true));
    drawSparks();
  }
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redrawAll);

  function base(p) {
    return {
      textStyle: { fontFamily: getComputedStyle(document.body).fontFamily, color: p.text2 },
      tooltip: {
        backgroundColor: p.surface, borderColor: p.grid, textStyle: { color: p.text, fontSize: 12 },
        extraCssText: "box-shadow:0 4px 16px rgba(0,0,0,.12);border-radius:8px;",
      },
      animationDuration: 400,
    };
  }
  function axisCommon(p) {
    return {
      axisLine: { lineStyle: { color: p.grid } }, axisTick: { show: false },
      axisLabel: { color: p.muted, fontSize: 11 }, splitLine: { lineStyle: { color: p.grid } },
    };
  }
  /** Tooltip header – a day, or the start of a week for long periods. */
  const period = (d) => (d.bucket === "week" ? `${t("Týždeň od")} ${d.day}` : d.day);
  const shortDay = (d) => { const [, m, dd] = d.split("-"); return LANG === "en" ? `${+m}/${+dd}` : `${+dd}.${+m}.`; };

  // --------------------------------------------------------------- builders
  const DM = {
    fmt, pct,

    /** Stacked daily volume: compliant vs failing (forwarded / other). */
    volume(el, timeline) {
      return register(el, (p) => {
        const days = timeline.map((d) => shortDay(d.day));
        const ser = [
          { name: t("Prešlo DMARC"), key: (d) => d.pass, color: p.good },
          { name: t("Preposlané (fail)"), key: (d) => Math.min(d.forwarded, d.fail), color: p.accent },
          { name: t("Zlyhalo"), key: (d) => Math.max(d.fail - d.forwarded, 0), color: p.critical },
        ];
        return Object.assign(base(p), {
          grid: { left: 8, right: 8, top: 36, bottom: 4, containLabel: true },
          legend: { top: 0, left: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10, textStyle: { color: p.text2 } },
          tooltip: Object.assign(base(p).tooltip, { trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: p.surface2, opacity: .6 } },
            formatter: (items) => {
              const d = timeline[items[0].dataIndex];
              return `<b>${period(d)}</b><br>` + items.map((i) => `${i.marker} ${i.seriesName}: <b>${fmt(i.value)}</b>`).join("<br>") +
                `<br>${t("Spolu")}: <b>${fmt(d.total)}</b> · ${t("súlad")} <b>${pct(d.pass_pct)}</b>`;
            } }),
          xAxis: Object.assign({ type: "category", data: days }, axisCommon(p), { splitLine: { show: false } }),
          yAxis: Object.assign({ type: "value" }, axisCommon(p), { axisLabel: { color: p.muted, fontSize: 11, formatter: (v) => fmt(v) } }),
          series: ser.map((s, i) => ({
            name: s.name, type: "bar", stack: "v", barMaxWidth: 26, data: timeline.map(s.key),
            // the 1px surface gap would swallow bars that are only a few pixels wide
            itemStyle: { color: s.color, borderColor: p.surface, borderWidth: timeline.length > 60 ? 0 : 1,
              borderRadius: i === ser.length - 1 && timeline.length <= 60 ? [4, 4, 0, 0] : 0 },
            emphasis: { focus: "series" },
          })),
        });
      });
    },

    /** DMARC compliance % over time – own chart, single axis. */
    compliance(el, timeline) {
      return register(el, (p) => Object.assign(base(p), {
        grid: { left: 8, right: 12, top: 18, bottom: 4, containLabel: true },
        tooltip: Object.assign(base(p).tooltip, { trigger: "axis", axisPointer: { type: "line", lineStyle: { color: p.muted } },
          formatter: (items) => { const d = timeline[items[0].dataIndex]; return `<b>${period(d)}</b><br>${t("Súlad")}: <b>${pct(d.pass_pct)}</b><br>${t("Správ")}: ${fmt(d.total)}`; } }),
        xAxis: Object.assign({ type: "category", boundaryGap: false, data: timeline.map((d) => shortDay(d.day)) }, axisCommon(p), { splitLine: { show: false } }),
        yAxis: Object.assign({ type: "value", min: 0, max: 100, axisLabel: { color: p.muted, fontSize: 11, formatter: "{value} %" } }, { splitLine: { lineStyle: { color: p.grid } } }),
        series: [{
          type: "line", data: timeline.map((d) => d.pass_pct), connectNulls: true, smooth: 0.25, symbol: "circle", symbolSize: 8, showSymbol: false,
          lineStyle: { width: 2, color: p.accent }, itemStyle: { color: p.accent, borderColor: p.surface, borderWidth: 2 },
          areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: p.accent + "33" }, { offset: 1, color: p.accent + "00" }] } },
          markLine: { silent: true, symbol: "none", lineStyle: { color: p.good, type: "dashed" }, label: { color: p.muted, formatter: "98 %", position: "insideEndTop" }, data: [{ yAxis: 98 }] },
        }],
      }));
    },

    /** Donut for DMARC categories (status colours + labels). */
    categories(el, items) {
      return register(el, (p) => {
        const total = items.reduce((a, b) => a + b.value, 0);
        return Object.assign(base(p), {
          tooltip: Object.assign(base(p).tooltip, { trigger: "item", formatter: (i) => `${i.marker} ${i.name}<br><b>${fmt(i.value)}</b> (${pct(i.percent)})` }),
          legend: { bottom: 0, left: "center", icon: "circle", itemWidth: 9, itemGap: 12, textStyle: { color: p.text2, fontSize: 11.5 },
            formatter: (n) => { const it = items.find((x) => t(x.label) === n); return `${n}  ${total ? pct(100 * it.value / total) : "–"}`; } },
          series: [{
            type: "pie", radius: ["50%", "70%"], center: ["50%", "42%"], avoidLabelOverlap: true, label: { show: false },
            itemStyle: { borderColor: p.surface, borderWidth: 2, borderRadius: 4 },
            data: items.map((it) => ({ name: t(it.label), value: it.value, itemStyle: { color: p[CAT_COLOR[it.key]] || p.series[0] } })),
          }, {
            // total in the middle of the ring – a single centred label keeps both lines aligned
            type: "pie", radius: ["0%", "45%"], center: ["50%", "42%"], silent: true, tooltip: { show: false },
            itemStyle: { color: "transparent" }, labelLine: { show: false },
            label: { show: true, position: "center", formatter: `{v|${fmt(total)}}\n{s|${t("správ")}}`,
              rich: { v: { color: p.text, fontSize: 20, fontWeight: 650, lineHeight: 26 }, s: { color: p.muted, fontSize: 11, lineHeight: 16 } } },
            data: [{ value: 1, name: "" }],
          }],
        });
      });
    },

    /** Generic donut with categorical slots in fixed order. */
    donut(el, items, colors) {
      return register(el, (p) => Object.assign(base(p), {
        tooltip: Object.assign(base(p).tooltip, { trigger: "item", formatter: (i) => `${i.marker} ${i.name}<br><b>${fmt(i.value)}</b> (${pct(i.percent)})` }),
        legend: { orient: "vertical", right: 0, top: "middle", icon: "circle", itemWidth: 9, textStyle: { color: p.text2 } },
        series: [{
          type: "pie", radius: ["55%", "78%"], center: ["32%", "50%"], label: { show: false },
          itemStyle: { borderColor: p.surface, borderWidth: 2, borderRadius: 4 },
          data: items.map((it, i) => ({ name: t(it.label), value: it.value, itemStyle: { color: colorOf(p, colors ? colors[i] : "s" + ((i % 8) + 1)) } })),
        }],
      }));
    },

    /** Horizontal bars – magnitude in a single hue. */
    hbar(el, items, opts) {
      opts = opts || {};
      return register(el, (p) => {
        const rows = items.slice().reverse();
        return Object.assign(base(p), {
          grid: { left: 8, right: 48, top: 4, bottom: 4, containLabel: true },
          tooltip: Object.assign(base(p).tooltip, { trigger: "item", formatter: (i) => `${i.name}<br><b>${fmt(i.value)}</b>` }),
          xAxis: { type: "value", show: false },
          yAxis: { type: "category", data: rows.map((r) => t(r.name)), axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { color: p.text2, fontSize: 12, width: 150, overflow: "truncate" } },
          series: [{
            type: "bar", barMaxWidth: 14, data: rows.map((r) => r.value),
            itemStyle: { color: opts.color ? p[opts.color] : p.accent, borderRadius: [0, 4, 4, 0] },
            label: { show: true, position: "right", color: p.muted, fontSize: 11, formatter: (i) => fmt(i.value) },
          }],
        });
      });
    },

    /** Optional world choropleth (needs GeoIP data). */
    world(el, items) {
      if (!el) return;
      fetch("https://cdn.jsdelivr.net/npm/echarts@4.9.0/map/json/world.json")
        .then((r) => r.json())
        .then((geo) => {
          echarts.registerMap("world", geo);
          const max = Math.max(1, ...items.map((i) => i.total));
          register(el, (p) => Object.assign(base(p), {
            tooltip: Object.assign(base(p).tooltip, { trigger: "item", formatter: (i) => i.data ? `${i.name}<br>${t("Správ")}: <b>${fmt(i.data.value)}</b><br>${t("Zlyhalo")}: <b>${fmt(i.data.fail)}</b>` : i.name }),
            visualMap: { min: 0, max, show: false, inRange: { color: ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"] } },
            series: [{
              type: "map", map: "world", roam: true, zoom: 1.15, emphasis: { label: { show: false }, itemStyle: { areaColor: p.warning } },
              itemStyle: { areaColor: p.surface2, borderColor: p.surface, borderWidth: .5 }, select: { disabled: true },
              data: items.map((i) => ({ name: i.name === "United States" ? "United States" : i.name, value: i.total, fail: i.fail })),
            }],
          }));
        })
        .catch(() => { el.innerHTML = `<div class="empty">${t("Mapa nie je dostupná (offline).")}</div>`; });
    },
  };

  // -------------------------------------------------------------- sparklines
  function drawSparks() {
    const color = v("--accent");
    document.querySelectorAll("svg.spark[data-values]").forEach((svg) => {
      const vals = JSON.parse(svg.dataset.values || "[]");
      const w = 120, h = 28, max = Math.max(1, ...vals);
      if (!vals.length) return;
      const step = vals.length > 1 ? w / (vals.length - 1) : w;
      const pts = vals.map((x, i) => `${(i * step).toFixed(1)},${(h - 2 - (x / max) * (h - 4)).toFixed(1)}`).join(" ");
      svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
      svg.innerHTML = `<polyline points="0,${h} ${pts} ${w},${h}" fill="${color}22" stroke="none"/>` +
        `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round"/>`;
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    drawSparks();
    document.querySelectorAll("[data-confirm]").forEach((el) =>
      el.addEventListener("submit", (e) => { if (!confirm(el.dataset.confirm)) e.preventDefault(); }));
    document.querySelectorAll("[data-autosubmit]").forEach((el) =>
      el.addEventListener("change", () => el.form.submit()));
  });

  window.DM = DM;
})();
