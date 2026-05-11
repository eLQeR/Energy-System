// Сторінка онтології: завантажує граф з Fuseki і малює його через vis-network.

const GROUP_COLORS = {
  HeatPump:      "#1f6feb",
  IndoorUnit:    "#58a6ff",
  OutdoorUnit:   "#79c0ff",
  Compressor:    "#bf8700",
  Fan:           "#d29922",
  ExpansionValve:"#a371f7",
  Sensor:        "#3fb950",
  Controller:    "#39c5cf",
  Refrigerant:   "#cf222e",
  Tank:          "#8957e5",
  Literal:       "#8c959f",
  Resource:      "#57606a",
};

let network = null;
let lastData = { nodes: [], edges: [] };

function colorFor(group) {
  return GROUP_COLORS[group] || "#57606a";
}

function nodeShape(kind) {
  return kind === "literal" ? "box" : "dot";
}

function renderLegend(nodes) {
  const groups = new Set(nodes.map(n => n.group));
  const root = document.getElementById("legend");
  root.innerHTML = [...groups].map(g => `
    <div style="display:flex;align-items:center;gap:6px;">
      <span style="width:12px;height:12px;border-radius:50%;
                   background:${colorFor(g)};display:inline-block;"></span>
      ${esc(g)}
    </div>
  `).join("");
}

function drawGraph(data) {
  lastData = data;
  const container = document.getElementById("ontology-graph");

  const visNodes = data.nodes.map(n => ({
    id: n.id,
    label: n.label,
    group: n.group,
    shape: nodeShape(n.kind),
    color: {
      background: colorFor(n.group),
      border:     "#24292f",
      highlight:  { background: "#ffd33d", border: "#24292f" },
    },
    font: {
      color: n.kind === "literal" ? "#24292f" : "#ffffff",
      size:  n.kind === "literal" ? 11 : 13,
      face:  "system-ui, -apple-system, Segoe UI",
    },
    size: n.kind === "literal" ? 10 : 18,
  }));

  const visEdges = data.edges.map((e, i) => ({
    id: `e${i}`,
    from: e.from, to: e.to,
    label: e.label,
    arrows: "to",
    color: { color: "#8c959f", highlight: "#1f6feb" },
    font: { color: "#57606a", size: 10, strokeWidth: 0, align: "middle" },
    smooth: { type: "dynamic" },
  }));

  if (network) network.destroy();
  network = new vis.Network(container, { nodes: visNodes, edges: visEdges }, {
    physics: {
      enabled: true,
      solver: "forceAtlas2Based",
      forceAtlas2Based: { gravitationalConstant: -60, springLength: 110 },
      stabilization: { iterations: 220 },
    },
    interaction: { hover: true, tooltipDelay: 150, navigationButtons: true },
    nodes: { borderWidth: 1.5 },
    edges: { length: 160 },
  });

  document.getElementById("graph-stats").textContent =
    `${data.nodes.length} вузлів · ${data.edges.length} ребер`;

  renderLegend(data.nodes);
}

async function loadGraph() {
  const device = document.getElementById("device-select").value;
  document.getElementById("graph-stats").textContent = "Завантаження…";
  try {
    const qs = device ? `?device=${encodeURIComponent(device)}` : "";
    const data = await api(`/api/ontology/graph${qs}`);
    setLive(true);
    drawGraph(data);
  } catch (e) {
    setLive(false);
    document.getElementById("graph-stats").textContent = "Помилка: " + e.message;
    toast("Не вдалось завантажити граф: " + e.message, "danger");
  }
}

async function loadDevices() {
  try {
    const devices = await api("/api/devices");
    const sel = document.getElementById("device-select");
    for (const d of devices) {
      const opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = d.label ? `${d.label} (${d.id})` : d.id;
      sel.appendChild(opt);
    }
  } catch (e) {
    console.warn("device list unavailable:", e.message);
  }
}

document.getElementById("btn-reload").addEventListener("click", loadGraph);
document.getElementById("btn-fit").addEventListener("click", () => {
  if (network) network.fit({ animation: { duration: 400 } });
});
document.getElementById("device-select").addEventListener("change", loadGraph);

loadDevices().then(loadGraph);
