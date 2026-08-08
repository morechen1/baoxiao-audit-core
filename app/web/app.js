const demoCases = [
  {
    id: "high-risk",
    number: "CASE 01",
    title: "高收益承诺宣传",
    category: "明显违规 / 高风险",
    riskLevel: "high",
    materialType: "advertisement",
    timestamp: "刚刚",
    rawText: "监管机构推荐本产品，保证年化收益8%，本金绝对安全，今天投保即可领取限量礼品。",
    summary: "材料同时出现监管背书、保证收益与零风险承诺等高风险信号，建议立即停止投放并由合规人员复核。",
    findings: [
      finding("F001", "监管背书误导", "high", "监管机构推荐", "该表述可能使消费者误以为产品获得监管机构背书或保证。", "请删除任何暗示监管机构推荐、审批或担保的营销表述。", "请确认营销材料是否使用了监管名称、标识或暗示性措辞。", "E001", "规范性依据 · 构造展示快照", "保险销售行为管理要求", "不得利用监管机构审核或备案程序提供保证等引人误解的表述。", "第十七条", "机构端：该用语可能造成监管背书误解，应结合投放场景复核并完成下架整改。", "消费者端：监管部门不会为具体保险产品作推荐或收益保证，请以合同和正式公开信息为准。"),
      finding("F002", "保证收益或本金", "high", "保证年化收益8%", "承诺固定收益或本金安全可能与保险产品实际风险、合同约定不一致。", "删除“保证”“绝对安全”等确定性承诺，改为以正式合同条款为准。", "该收益表述是否有对应合同依据，且是否完整提示限制条件？", "E002", "处罚案例 · 构造展示快照", "保险营销风险提示材料", "不得以保证收益、保本保息等表述误导消费者。", "风险提示段落", "机构端：当前材料存在确定性收益承诺风险信号，需核对产品条款与适用范围。", "消费者端：看到“保证收益”时，请注意核对保险合同是否真的包含相同承诺及限制条件。"),
      finding("F003", "零风险或无损失", "high", "本金绝对安全", "绝对化安全承诺可能弱化消费者对保险责任、现金价值或退保损失的理解。", "移除绝对化安全表达，并补充与产品相关的真实风险提示。", "材料是否遗漏等待期、责任免除、退保损失等关键说明？", "E003", "产品条款语境 · 构造展示快照", "保险合同风险提示", "投保人应阅读保险责任、责任免除及退保相关约定。", "风险提示", "机构端：该结论仅为风险信号，建议补充完整条款提示并进行人工复核。", "消费者端：保险产品的保障和收益以合同为准，购买前请阅读责任免除和退保相关约定。"),
    ],
  },
  {
    id: "low-risk",
    number: "CASE 02",
    title: "合同要点说明",
    category: "合规 / 低风险",
    riskLevel: "low",
    materialType: "product_introduction",
    timestamp: "今日 09:40",
    rawText: "本材料仅作产品信息说明，保险责任、等待期、责任免除及退保安排以正式保险合同为准。投保前请阅读条款并按需咨询持证人员。",
    summary: "未检测到当前规则集定义的高风险营销信号；仍建议按既有人工审核流程确认材料版本与适用对象。",
    findings: [],
  },
  {
    id: "boundary-risk",
    number: "CASE 03",
    title: "退保价值边界表述",
    category: "边界语义 / 中风险",
    riskLevel: "medium",
    materialType: "sales_script",
    timestamp: "昨日 16:20",
    rawText: "资金使用灵活，如有需要可随时办理退保并拿回全部投入资金。具体权益和现金价值请以合同约定为准。",
    summary: "材料含有可能弱化退保损失的表达；虽附有合同提示，仍需人工判断前后表述是否足以避免误导。",
    findings: [
      finding("F001", "退保 / 现金价值误述", "medium", "随时办理退保并拿回全部投入资金", "该表达可能使消费者忽略退保价值受合同、持有期等条件影响。", "避免使用“全部拿回”等绝对化用语；同时展示与该产品对应的现金价值和退保限制。", "是否已向消费者清晰说明退保金额可能低于已交保费及其适用条件？", "E001", "产品条款语境 · 构造展示快照", "保险合同现金价值说明", "退保时的现金价值按照保险合同约定计算，可能低于已交保险费。", "现金价值条款", "机构端：当前证据有限，但该表述可能弱化退保损失，应结合具体合同与上下文核验。", "消费者端：退保能拿回多少金额要看合同的现金价值约定，可能与已交保费不同，请先核对条款。", "partially_supported"),
    ],
  },
];

function finding(id, title, severity, matchedText, explanation, remediation, question, citationKey, sourceKind, sourceTitle, quote, locator, institution, consumer, evidenceStatus = "supported") {
  return { id, title, category: title, severity, matchedText, explanation, remediation, question, evidenceStatus, institution, consumer, citations: [{ key: citationKey, sourceKind, sourceTitle, quote, locator, sourceUrl: "构造展示数据，不对应真实公开来源", supportType: sourceKind }] };
}

const state = { current: null, audience: "institution", processingTimers: [] };
const viewTitles = { dashboard: "智能审核工作台", review: "新建智能审核", result: "审核结果中心", processing: "审核运行过程" };
const stages = [
  ["文本规范化", "构建材料指纹与可追溯偏移"],
  ["确定性风险筛查", "运行版本化营销风险规则"],
  ["可信知识检索", "仅使用准入的监管知识块"],
  ["受控 AI 解释", "只解释既有风险信号"],
  ["Citation 证据校验", "核验连续引文与证据边界"],
  ["生成双端结果", "输出机构端与消费者端视图"],
];

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function riskLabel(level) { return ({ high: "高风险", medium: "中风险", low: "低风险" })[level] || "待核验"; }
function riskClass(level) { return `risk-${level}`; }

function showView(name) {
  $$(".view").forEach((view) => view.classList.remove("active-view"));
  $(`#${name}`).classList.add("active-view");
  $("#view-title").textContent = viewTitles[name] || "保销智审";
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.viewTarget === name));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 4500);
}

function renderDashboard() {
  const totalFindings = demoCases.reduce((count, item) => count + item.findings.length, 0);
  const metrics = [
    ["累计展示审核", "03", "构造赛事案例", "#41d9e7"],
    ["识别风险信号", String(totalFindings), "可追溯规则命中", "#ffb86d"],
    ["受控引用覆盖", "100%", "每条展示 finding", "#8d78ff"],
    ["高风险待复核", "03", "优先处置提醒", "#ff5c73"],
  ];
  $("#metrics").innerHTML = metrics.map(([label, value, detail, color]) => `<article class="metric-card" style="--metric:${color}"><div class="metric-label">${label}</div><div class="metric-value">${value}</div><div class="metric-detail"><b>●</b> ${detail}</div></article>`).join("");
  $("#recent-cases").innerHTML = demoCases.map((item) => `<article class="recent-card" data-open-case="${item.id}"><header><span>${item.number}</span><span class="risk-pill ${riskClass(item.riskLevel)}">${riskLabel(item.riskLevel)}</span></header><h3>${item.title}</h3><p>${item.summary}</p><div class="recent-footer"><span>${item.timestamp}</span><span>${item.findings.length} 条风险信号 <b>→</b></span></div></article>`).join("");
}

function renderCaseSelector() {
  $("#case-selector").innerHTML = demoCases.map((item) => `<article class="case-card" data-run-case="${item.id}"><header><span class="case-number">${item.number}</span><span class="risk-pill ${riskClass(item.riskLevel)}">${riskLabel(item.riskLevel)}</span></header><h3>${item.title}</h3><p>${item.rawText}</p><footer><span>构造展示数据 · ${item.materialType}</span><b>开始审查 →</b></footer></article>`).join("");
}

function renderStages(index = -1) {
  $("#stage-list").innerHTML = stages.map(([label, detail], stageIndex) => {
    const status = stageIndex < index ? "done" : stageIndex === index ? "active" : "";
    const icon = stageIndex < index ? "✓" : String(stageIndex + 1).padStart(2, "0");
    return `<div class="stage ${status}" title="${detail}"><i>${icon}</i><span>${label}</span></div>`;
  }).join("");
  $("#process-progress-bar").style.width = `${Math.max(0, index) / stages.length * 100}%`;
}

function clearProcessingTimers() { state.processingTimers.forEach(window.clearTimeout); state.processingTimers = []; }

function playDemo(caseItem) {
  clearProcessingTimers();
  state.current = { ...caseItem, mode: "构造预置案例 · 仅赛事演示，不进入正式证据库", status: "completed" };
  showView("processing");
  $("#processing-title").textContent = `正在审查「${caseItem.title}」`;
  $("#processing-hint").textContent = "预置案例展示已冻结的处理链路；其数据明确标记为构造展示数据。";
  renderStages(0);
  stages.forEach(([label, detail], index) => {
    state.processingTimers.push(window.setTimeout(() => {
      renderStages(index + 1);
      $("#processing-copy").textContent = `${label}：${detail}`;
      if (index === stages.length - 1) state.processingTimers.push(window.setTimeout(() => { renderResult(); showView("result"); }, 520));
    }, 430 * (index + 1)));
  });
}

async function fetchJson(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.error?.code || payload?.detail || `HTTP ${response.status}`);
  return payload;
}

function apiFinding(row, index, institutionArtifact, consumerArtifact, citations) {
  const citationsForFinding = (citations || []).filter((item) => item.finding_key === `F${String(index + 1).padStart(3, "0")}`).map((item) => ({ key: item.citation_key, sourceKind: item.support_type, sourceTitle: item.source_title, quote: item.cited_quote, locator: formatLocator(item.source_locator), sourceUrl: item.source_url }));
  const institution = explanationText(institutionArtifact, index, "institution") || row.explanation;
  const consumer = explanationText(consumerArtifact, index, "consumer") || row.review_question;
  return { id: `F${String(index + 1).padStart(3, "0")}`, title: row.category, category: row.category, severity: row.severity, matchedText: row.matched_text, explanation: row.explanation, remediation: row.remediation_template || "请由合规人员依据完整材料复核。", question: row.review_question, evidenceStatus: row.evidence_status, institution, consumer, citations: citationsForFinding.length ? citationsForFinding : (row.evidence || []).map((item, evidenceIndex) => ({ key: `E${String(evidenceIndex + 1).padStart(3, "0")}`, sourceKind: item.support_type, sourceTitle: item.source?.title || "可信知识来源", quote: item.evidence_references?.[0]?.quote || "该证据引用由后端快照保存。", locator: formatLocator(item.source_locator), sourceUrl: item.source?.source_url || "" })) };
}

function formatLocator(locator) { return locator && typeof locator === "object" ? Object.values(locator).filter(Boolean).join(" · ") || "已验证定位信息" : "已验证定位信息"; }

function explanationText(artifact, index, audience) {
  if (!artifact?.validated_output) return "";
  const output = artifact.validated_output;
  const rows = audience === "institution" ? output.finding_explanations : output.risk_explanations;
  const row = rows?.[index];
  if (!row) return "";
  const candidates = [row.explanation?.text, row.plain_language_explanation?.text, row.evidence_assessment?.text];
  return candidates.find((value) => typeof value === "string" && value.trim()) || "";
}

async function createApiExplanation(runId, audience) {
  const created = await fetchJson(`/api/v1/screenings/${runId}/explanations`, { method: "POST", body: JSON.stringify({ audience, provider: "deterministic_fixture" }) });
  if (created.status !== "completed") return null;
  return fetchJson(created.artifact_endpoint);
}

async function runLiveReview(form) {
  const title = $("#material-title").value.trim();
  const rawText = $("#material-text").value.trim();
  if (!title || !rawText) { showToast("请填写材料标题和待审文本。", true); return; }
  clearProcessingTimers();
  showView("processing");
  $("#processing-title").textContent = `正在提交「${title}」`;
  $("#processing-hint").textContent = "在线模式只调用已有 API；若可信知识库未准备，系统会失败关闭。";
  renderStages(0);
  try {
    $("#processing-copy").textContent = "正在调用 /api/v1/screenings 创建筛查任务。";
    const created = await fetchJson("/api/v1/screenings", { method: "POST", body: JSON.stringify({ title, material_type: $("#material-type").value, raw_text: rawText, source_label: "contest_demo_workspace" }) });
    renderStages(2);
    $("#processing-copy").textContent = "正在读取机构端与消费者端报告。";
    const [screening, institutionReport, consumerReport] = await Promise.all([fetchJson(created.report_endpoints.screening), fetchJson(created.report_endpoints.institution_report), fetchJson(created.report_endpoints.consumer_notice)]);
    renderStages(3);
    $("#processing-copy").textContent = "正在请求受控解释与 Citation 校验。";
    let institutionArtifact = null; let consumerArtifact = null;
    try { [institutionArtifact, consumerArtifact] = await Promise.all([createApiExplanation(created.screening_run_id, "institution"), createApiExplanation(created.screening_run_id, "consumer")]); } catch (error) { showToast(`筛查已完成；受控解释不可用：${error.message}`, true); }
    let citations = [];
    if (institutionArtifact) citations = await fetchJson(`/api/v1/explanations/${institutionArtifact.explanation_run_id}/citations`);
    renderStages(6);
    state.current = {
      id: `live-${created.screening_run_id}`, number: "LIVE API", title, rawText, materialType: $("#material-type").value, riskLevel: screening.findings.some((row) => row.severity === "high") ? "high" : screening.findings.some((row) => row.severity === "medium") ? "medium" : "low", summary: institutionReport.disclaimer, findings: screening.findings.map((row, index) => apiFinding(row, index, institutionArtifact, consumerArtifact, citations)), mode: "在线 API 审查 · 当前后端结果", status: created.status, institutionReport, consumerReport, institutionArtifact, consumerArtifact,
    };
    renderResult();
    showView("result");
  } catch (error) {
    showView("review");
    showToast(`在线审核未完成：${error.message}。可改用预置案例进行稳定演示。`, true);
  }
}

function renderResult() {
  const result = state.current;
  if (!result) { $("#result-content").innerHTML = `<div class="empty-result">尚未选择审查任务。请从工作台或新建审核页面开始。</div>`; return; }
  const findings = result.findings || [];
  const high = findings.filter((item) => item.severity === "high").length;
  const medium = findings.filter((item) => item.severity === "medium").length;
  const evidenceCount = findings.reduce((count, item) => count + item.citations.length, 0);
  $("#result-mode").textContent = result.mode;
  $("#result-content").innerHTML = `<div class="result-hero"><article class="result-summary"><span class="eyebrow">REVIEW RESULT / ${escapeHtml(result.number || "CASE")}</span><h1>${escapeHtml(result.title)}</h1><p>${escapeHtml(result.summary || "审核完成。")}</p><div class="result-kpis"><div><strong class="${riskClass(result.riskLevel)}">${riskLabel(result.riskLevel)}</strong><small>总体风险等级</small></div><div><strong>${findings.length}</strong><small>风险信号</small></div><div><strong>${evidenceCount}</strong><small>可见 Citation</small></div><div><strong>${result.status === "completed" ? "完成" : "已创建"}</strong><small>审核状态</small></div></div></article><aside class="result-status"><div><span class="eyebrow">MATERIAL PROFILE</span><h3>${escapeHtml(result.materialType || "文本材料")}</h3></div><div class="status-list"><span>材料指纹 <b>${result.id.includes("live") ? "已生成" : "演示快照"}</b></span><span>规则筛查 <b>确定性</b></span><span>证据校验 <b>${result.mode.startsWith("在线") ? "后端结果" : "展示完成"}</b></span><span>审计边界 <b>已保留</b></span></div></aside></div><div class="analysis-layout"><article class="findings-panel"><header class="panel-header"><div><span class="eyebrow">RISK FINDINGS</span><h2>风险详情与证据链</h2></div><div class="audience-toggle"><button class="active" data-audience="institution">机构端</button><button data-audience="consumer">消费者端</button></div></header><div id="finding-list">${findings.length ? findings.map(findingRow).join("") : `<div class="empty-result">未发现当前规则集中的风险信号。仍建议按既有人工审核流程确认材料版本与适用范围。</div>`}</div></article><aside class="detail-panel" id="detail-panel"><div class="detail-empty"><div><b>选择一条风险信号</b><br />查看“原文 → 规则 → 依据 → 受控解释”的完整链路。</div></div></aside></div>${result.rawText ? `<div class="notice"><b>材料原文：</b>${escapeHtml(result.rawText)}</div>` : ""}<div class="notice"><b>边界说明：</b>${result.mode.startsWith("在线") ? "结果来自当前后端 API；系统输出为风险信号与证据辅助，不构成违法认定或最终法律意见。" : "该案例及引用均为构造展示数据，仅用于现场演示交互与能力边界；不代表真实监管材料、真实产品或正式可信证据。"}</div>`;
  $$("[data-audience]").forEach((button) => button.addEventListener("click", () => { state.audience = button.dataset.audience; $$("[data-audience]").forEach((item) => item.classList.toggle("active", item === button)); const selected = $(".finding-row.selected"); if (selected) renderDetail(Number(selected.dataset.findingIndex)); }));
  $$(".finding-row").forEach((row) => row.addEventListener("click", () => { $$(".finding-row").forEach((item) => item.classList.remove("selected")); row.classList.add("selected"); renderDetail(Number(row.dataset.findingIndex)); }));
  if (findings.length) { const first = $(".finding-row"); first.classList.add("selected"); renderDetail(0); }
}

function findingRow(item, index) { return `<article class="finding-row" data-finding-index="${index}"><span class="finding-index">${escapeHtml(item.id)}</span><div><h3>${escapeHtml(item.title)} <span class="risk-pill ${riskClass(item.severity)}">${riskLabel(item.severity)}</span></h3><p>命中原文：<span class="matched">“${escapeHtml(item.matchedText)}”</span> · ${escapeHtml(item.evidenceStatus)}</p></div><span class="finding-arrow">›</span></article>`; }

function renderDetail(index) {
  const item = state.current.findings[index];
  const audienceText = state.audience === "institution" ? item.institution : item.consumer;
  const audienceLabel = state.audience === "institution" ? "机构端受控解释" : "消费者端通俗说明";
  const citations = item.citations.length ? item.citations.map((citation) => `<article class="citation"><header><span>${escapeHtml(citation.key)}</span><span>${escapeHtml(citation.sourceKind || "证据")}</span></header><strong>${escapeHtml(citation.sourceTitle || "已验证来源")}</strong><div class="quote">“${escapeHtml(citation.quote)}”</div><p>${escapeHtml(citation.locator || "已验证定位信息")} · ${escapeHtml(citation.sourceUrl || "来源快照")}</p></article>`).join("") : `<p>该风险信号当前没有可用 Citation；系统按证据不足边界提示人工复核。</p>`;
  $("#detail-panel").innerHTML = `<div class="detail-title"><div><span class="eyebrow">${escapeHtml(item.id)} / EVIDENCE TRACE</span><h2>${escapeHtml(item.title)}</h2><p>${escapeHtml(item.evidenceStatus)}</p></div><span class="risk-pill ${riskClass(item.severity)}">${riskLabel(item.severity)}</span></div><div class="detail-section"><h4>01 · 原文命中</h4><div class="quote">“${escapeHtml(item.matchedText)}”</div></div><div class="detail-section"><h4>02 · 确定性风险说明</h4><p>${escapeHtml(item.explanation)}</p></div><div class="detail-section"><h4>03 · ${audienceLabel}</h4><p>${escapeHtml(audienceText)}</p></div><div class="detail-section"><h4>04 · CITATION / 来源证据</h4>${citations}</div><div class="detail-section"><h4>05 · 人工复核与整改建议</h4><p><b>复核：</b>${escapeHtml(item.question)}</p><p><b>建议：</b>${escapeHtml(item.remediation)}</p></div>`;
}

function bindEvents() {
  $$("[data-view-target]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.viewTarget)));
  $("#hero-start").addEventListener("click", () => showView("review"));
  $("#hero-demo").addEventListener("click", () => playDemo(demoCases[0]));
  document.addEventListener("click", (event) => {
    const caseButton = event.target.closest("[data-run-case], [data-open-case]");
    if (caseButton) { const item = demoCases.find((value) => value.id === (caseButton.dataset.runCase || caseButton.dataset.openCase)); if (item) playDemo(item); }
  });
  $("#file-input").addEventListener("change", async (event) => {
    const file = event.target.files?.[0]; if (!file) return;
    $("#material-text").value = await file.text(); $("#material-title").value = file.name.replace(/\.txt$/i, "") || "导入文本材料"; showToast("TXT 内容已载入，尚未发送到后端。");
  });
  $("#live-review-form").addEventListener("submit", (event) => { event.preventDefault(); runLiveReview(event.currentTarget); });
}

renderDashboard(); renderCaseSelector(); bindEvents();
