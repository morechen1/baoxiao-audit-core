const demoCases = [
  {
    id: "high-risk",
    number: "CASE 01",
    title: "高收益承诺宣传",
    category: "明显违规 / 高风险",
    demoRole: "典型违规风险",
    riskLevel: "high",
    materialType: "advertisement",
    timestamp: "刚刚",
    rawText: "监管推荐本产品，保证收益8%，本金绝对安全，今天投保即可领取限量礼品。",
    summary: "材料同时出现监管背书、保证收益与零风险承诺等高风险信号，建议立即停止投放并由合规人员复核。",
    findings: [
      finding("F001", "监管背书误导", "high", "监管推荐", "该表述可能使消费者误以为产品获得监管机构背书或保证。", "请删除任何暗示监管机构推荐、审批或担保的营销表述。", "请确认营销材料是否使用了监管名称、标识或暗示性措辞。", "E001", "规范性依据 · 构造展示快照", "保险销售行为管理要求", "不得利用监管机构审核或备案程序提供保证等引人误解的表述。", "第十七条", "机构端：该用语可能造成监管背书误解，应结合投放场景复核并完成下架整改。", "消费者端：监管部门不会为具体保险产品作推荐或收益保证，请以合同和正式公开信息为准。"),
      finding("F002", "保证收益或本金", "high", "保证收益8%", "承诺固定收益或本金安全可能与保险产品实际风险、合同约定不一致。", "删除“保证”“绝对安全”等确定性承诺，改为以正式合同条款为准。", "该收益表述是否有对应合同依据，且是否完整提示限制条件？", "E002", "处罚案例 · 构造展示快照", "保险营销风险提示材料", "不得以保证收益、保本保息等表述误导消费者。", "风险提示段落", "机构端：当前材料存在确定性收益承诺风险信号，需核对产品条款与适用范围。", "消费者端：看到“保证收益”时，请注意核对保险合同是否真的包含相同承诺及限制条件。"),
      finding("F003", "零风险或无损失", "high", "本金绝对安全", "绝对化安全承诺可能弱化消费者对保险责任、现金价值或退保损失的理解。", "移除绝对化安全表达，并补充与产品相关的真实风险提示。", "材料是否遗漏等待期、责任免除、退保损失等关键说明？", "E003", "产品条款语境 · 构造展示快照", "保险合同风险提示", "投保人应阅读保险责任、责任免除及退保相关约定。", "风险提示", "机构端：该结论仅为风险信号，建议补充完整条款提示并进行人工复核。", "消费者端：保险产品的保障和收益以合同为准，购买前请阅读责任免除和退保相关约定。"),
    ],
  },
  {
    id: "low-risk",
    number: "CASE 02",
    title: "合同要点说明",
    category: "合规 / 低风险",
    demoRole: "低风险对照",
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
    category: "边界语义 / 高风险信号",
    demoRole: "边界语义风险",
    riskLevel: "high",
    materialType: "sales_script",
    timestamp: "昨日 16:20",
    rawText: "资金使用灵活，如有需要可随时退保没有损失。具体权益和现金价值请以合同约定为准。",
    summary: "材料含有可能弱化退保损失的表达；虽附有合同提示，仍需人工判断前后表述是否足以避免误导。",
    findings: [
      finding("F001", "退保 / 现金价值误述", "high", "随时退保没有损失", "该表达可能使消费者忽略退保价值受合同、持有期等条件影响。", "避免使用“没有损失”等绝对化用语；同时展示与该产品对应的现金价值和退保限制。", "是否已向消费者清晰说明退保金额可能低于已交保费及其适用条件？", "E001", "产品条款语境 · 构造展示快照", "保险合同现金价值说明", "退保时的现金价值按照保险合同约定计算，可能低于已交保险费。", "现金价值条款", "机构端：当前证据有限，但该表述可能弱化退保损失，应结合具体合同与上下文核验。", "消费者端：退保能拿回多少金额要看合同的现金价值约定，可能与已交保费不同，请先核对条款。", "partially_supported"),
    ],
  },
];

function finding(id, title, severity, matchedText, explanation, remediation, question, citationKey, sourceKind, sourceTitle, quote, locator, institution, consumer, evidenceStatus = "supported") {
  return { id, title, category: title, severity, matchedText, explanation, remediation, question, evidenceStatus, institution, consumer, citations: [{ key: citationKey, sourceKind, sourceTitle, quote, locator, sourceUrl: "构造展示数据，不对应真实公开来源", supportType: sourceKind }] };
}

const state = {
  current: null,
  audience: "institution",
  processingTimers: [],
  provider: {
    provider: "deterministic_fixture",
    mode: "deterministic_demo",
    label: "确定性演示",
    model: "controlled-fixture-v2",
    ready: true,
  },
};
const viewTitles = { dashboard: "智能审核工作台", review: "新建智能审核", result: "审核结果中心", processing: "审核运行过程", evaluation: "系统验证" };
const stages = [
  ["材料解析", "构建材料指纹与可追溯偏移"],
  ["风险筛查", "运行确定性营销风险规则"],
  ["监管知识匹配", "只使用已准入的可信知识块"],
  ["AI 受控解释", "只解释既有 finding 与证据"],
  ["引用验证", "验证 Citation、Claim 与边界"],
  ["审核结果", "输出机构端与消费者端视图"],
];

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function riskLabel(level) { return ({ high: "高风险", medium: "中风险", low: "低风险" })[level] || "待核验"; }
function riskClass(level) { return `risk-${level}`; }
function displayCategory(value) {
  return ({
    regulatory_endorsement: "监管背书误导",
    guaranteed_return: "保证收益或本金",
    no_risk: "零风险或无损失",
    surrender_value_misstatement: "退保 / 现金价值误述",
    surrender_cash_value: "退保 / 现金价值误述",
  })[value] || value;
}

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

function formatEvaluationMetric(value) { return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "N/A"; }
function evaluationCard(title, status, body, tone = "") { return `<article class="evaluation-card ${tone}"><header><span class="eyebrow">${title}</span><b class="evaluation-status ${status}">${escapeHtml(status)}</b></header>${body}</article>`; }
function provisionalEvaluation(report, external = false) {
  const metrics = external ? report?.external_metrics : report?.metrics;
  if (!report?.provisional || !metrics) return "";
  return `<div class="evaluation-note"><b>PROVISIONAL / 待最终封存</b><span>${metrics.sample_count} 样本 · Micro F1 ${formatEvaluationMetric(metrics.micro_f1)} · Exact-set ${formatEvaluationMetric(metrics.exact_set_match)}。仅用于内部检查，不是正式 headline。</span></div>`;
}
function renderEvaluationCenter(payload) {
  const constructed = payload.constructed || {}; const external = payload.external || {}; const safety = payload.controlled_rag_safety || {};
  const cm = constructed.report?.metrics;
  const constructedBody = constructed.status === "COMPLETED" && cm
    ? `<h2>构造覆盖验证</h2><div class="evaluation-metrics"><span><b>${cm.sample_count}</b>样本</span><span><b>${formatEvaluationMetric(cm.micro_precision)}</b>Micro P</span><span><b>${formatEvaluationMetric(cm.micro_recall)}</b>Micro R</span><span><b>${formatEvaluationMetric(cm.micro_f1)}</b>Micro F1</span><span><b>${formatEvaluationMetric(cm.macro_f1)}</b>Macro F1</span><span><b>${formatEvaluationMetric(cm.exact_set_match)}</b>Exact-set</span></div>`
    : `<h2>构造覆盖验证</h2><p>正式 SEALED manifest 与报告尚未就绪。不会显示正式 headline。</p><small>当前 manifest：${escapeHtml(constructed.manifest?.status || "NOT READY")}</small>${provisionalEvaluation(constructed.provisional)}`;
  const er = external.report;
  const externalBody = external.status === "COMPLETED" && er?.external_metrics
    ? `<h2>监管公开案例外部验证</h2><div class="evaluation-metrics"><span><b>${er.independent_in_scope_count}</b>独立 in-scope</span><span><b>${formatEvaluationMetric(er.external_metrics.micro_f1)}</b>Micro F1</span><span><b>${formatEvaluationMetric(er.external_metrics.exact_set_match)}</b>Exact-set</span></div><small>${escapeHtml(er.independence_label || "")}</small>`
    : `<h2>监管公开案例外部验证</h2><p>${external.status === "INSUFFICIENT" ? "INSUFFICIENT — exploratory only：独立 in-scope 少于 30。" : "正式公开案例 manifest 与独立报告尚未就绪。"}</p><small>最低门槛：30 个未暴露 in-scope 案例；当前不发布外部 headline 指标。</small>${provisionalEvaluation(external.provisional, true)}`;
  const safetyReport = safety.report;
  const safetyBody = safety.status === "COMPLETED" && safetyReport
    ? `<h2>Controlled-RAG 安全门禁验证</h2><div class="evaluation-metrics"><span><b>${safetyReport.constructed_valid_executed}</b>Valid samples</span><span><b>${safetyReport.constructed_valid_passed}</b>Valid accepted</span><span><b>${safetyReport.constructed_invalid_executed}</b>Invalid samples</span><span><b>${safetyReport.constructed_invalid_blocked}</b>Invalid blocked</span></div>`
    : `<h2>Controlled-RAG 安全门禁验证</h2><div class="evaluation-metrics"><span><b>${safety.valid_samples || "N/A"}</b>Valid corpus</span><span><b>${safety.invalid_samples || "N/A"}</b>Invalid corpus</span></div><p>真实 acceptance 报告尚未载入：NOT READY。</p>`;
  $("#evaluation-center").innerHTML = `${evaluationCard("A · CONSTRUCTED", constructed.status || "NOT_READY", constructedBody, "cyan")}${evaluationCard("B · EXTERNAL", external.status || "NOT_READY", externalBody, "amber")}${evaluationCard("C · SAFETY GATE", safety.status || "NOT_READY", safetyBody, "violet")}<p class="evaluation-disclaimer">${escapeHtml(safety.notice || "业务识别性能 ≠ 大模型生成安全门禁。")}</p>`;
}
async function loadEvaluationCenter() {
  try { renderEvaluationCenter(await fetchJson("/api/v1/evaluations/latest")); }
  catch (error) { $("#evaluation-center").innerHTML = evaluationCard("EVALUATION CENTER", "NOT_READY", `<h2>评测状态不可用</h2><p>${escapeHtml(error.message)}</p>`); }
}

function renderDashboard() {
  const totalFindings = demoCases.reduce((count, item) => count + item.findings.length, 0);
  const highFindings = demoCases.reduce((count, item) => count + item.findings.filter((findingItem) => findingItem.severity === "high").length, 0);
  const totalCitations = demoCases.reduce((count, item) => count + item.findings.reduce((findingCount, findingItem) => findingCount + findingItem.citations.length, 0), 0);
  const metrics = [
    ["可选演示案例", String(demoCases.length).padStart(2, "0"), "构造赛事案例", "#41d9e7"],
    ["规则风险发现", String(totalFindings), "可追溯规则命中", "#ffb86d"],
    ["可见引用证据", String(totalCitations), "按 finding 绑定", "#8d78ff"],
    ["高风险待复核", String(highFindings), "优先处置提醒", "#ff5c73"],
  ];
  $("#metrics").innerHTML = metrics.map(([label, value, detail, color]) => `<article class="metric-card" style="--metric:${color}"><div class="metric-label">${label}</div><div class="metric-value">${value}</div><div class="metric-detail"><b>●</b> ${detail}</div></article>`).join("");
  $("#recent-cases").innerHTML = demoCases.map((item) => `<article class="recent-card" data-open-case="${item.id}"><header><span>${item.number}</span><span class="risk-pill ${riskClass(item.riskLevel)}">${riskLabel(item.riskLevel)}</span></header><span class="case-role">${item.demoRole}</span><h3>${item.title}</h3><p>${item.summary}</p><div class="recent-footer"><span>${item.timestamp}</span><span>${item.findings.length} 条风险信号 <b>→</b></span></div></article>`).join("");
  $("#pipeline-flow").innerHTML = stages.map(([label, detail], index) => `<div class="pipeline-step"><span>${String(index + 1).padStart(2, "0")}</span><strong>${label}</strong><small>${detail}</small></div>${index < stages.length - 1 ? '<i class="pipeline-arrow" aria-hidden="true">→</i>' : ""}`).join("");
  renderProviderContext();
}

function renderCaseSelector() {
  $("#case-selector").innerHTML = demoCases.map((item) => `<article class="case-card" data-run-case="${item.id}"><header><span class="case-number">${item.number}</span><span class="risk-pill ${riskClass(item.riskLevel)}">${riskLabel(item.riskLevel)}</span></header><span class="case-role">${item.demoRole}</span><h3>${item.title}</h3><p>${item.rawText}</p><footer><span>构造 Demo 数据 · ${item.materialType}</span><b>开始审查 →</b></footer></article>`).join("");
}

function renderProviderContext() {
  const provider = state.provider;
  const mode = provider.mode === "real_ai" ? "真实大模型" : "确定性演示模式";
  const detail = provider.mode === "real_ai" ? "受控生成 · Citation 验证" : "Fixture · 稳定回归与备用演示";
  $("#dashboard-provider").innerHTML = `<span>解释模式 <b class="provider-live">${escapeHtml(mode)}</b></span><span>当前 Provider <b>${escapeHtml(provider.model || "未配置")}</b></span><span>运行边界 <b>${escapeHtml(detail)}</b></span>`;
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
  $("#processing-hint").textContent = "演示回放展示已验证的处理结果，不表示逐阶段实时后台耗时；数据明确标记为构造展示数据。";
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

function apiFinding(row, institutionArtifact, consumerArtifact, citations) {
  const findingKey = row.finding_key;
  const citationsForFinding = (citations || []).filter((item) => item.finding_key === findingKey).map((item) => ({ key: item.citation_key, sourceKind: item.support_type, sourceTitle: item.source_title, quote: item.cited_quote, locator: formatLocator(item.source_locator), sourceUrl: item.source_url }));
  const institution = explanationText(institutionArtifact, findingKey, "institution");
  const consumer = explanationText(consumerArtifact, findingKey, "consumer");
  return { id: findingKey, title: displayCategory(row.category), category: row.category, severity: row.severity, matchedText: row.matched_text, explanation: row.explanation, remediation: row.remediation_template || "请由合规人员依据完整材料复核。", question: row.review_question, evidenceStatus: row.evidence_status, institution: institution.text || row.explanation, institutionSource: institution.text ? "真实模型受控解释" : "基础报告文本（确定性筛查结果）", consumer: consumer.text || row.review_question, consumerSource: consumer.text ? "真实模型受控解释" : "基础报告文本（确定性筛查结果）", citations: citationsForFinding.length ? citationsForFinding : (row.evidence || []).map((item, evidenceIndex) => ({ key: `E${String(evidenceIndex + 1).padStart(3, "0")}`, sourceKind: item.support_type, sourceTitle: item.source?.title || "可信知识来源", quote: item.evidence_references?.[0]?.quote || "该证据引用由后端快照保存。", locator: formatLocator(item.source_locator), sourceUrl: item.source?.source_url || "" })) };
}

function formatLocator(locator) {
  if (!locator || typeof locator !== "object") return "已验证定位信息";
  const preferred = ["article_number", "article", "section", "field_name", "page_number", "paragraph"];
  const value = preferred.map((key) => locator[key]).find(Boolean);
  return value ? `定位：${value}` : "已验证证据定位";
}

function explanationText(artifact, findingKey, audience) {
  if (!artifact?.validated_output) return { text: "" };
  const output = artifact.validated_output;
  const rows = audience === "institution" ? output.finding_explanations : output.risk_explanations;
  const row = rows?.find((item) => item.finding_key === findingKey);
  if (!row) return { text: "" };
  const candidates = [row.explanation?.text, row.plain_language_explanation?.text, row.evidence_assessment?.text];
  return { text: candidates.find((value) => typeof value === "string" && value.trim()) || "" };
}

async function createApiExplanation(runId, audience) {
  const created = await fetchJson(`/api/v1/screenings/${runId}/explanations`, { method: "POST", body: JSON.stringify({ audience, provider: state.provider.provider }) });
  if (created.status !== "completed") throw new Error(`explanation_${created.status || "not_completed"}`);
  return fetchJson(created.artifact_endpoint);
}

async function requestAudienceArtifacts(runId, create = createApiExplanation) {
  const audiences = ["institution", "consumer"];
  const settled = await Promise.allSettled(audiences.map((audience) => create(runId, audience)));
  const artifacts = {}; const audienceStatus = {};
  settled.forEach((outcome, index) => {
    const audience = audiences[index];
    if (outcome.status === "fulfilled") {
      artifacts[audience] = outcome.value;
      audienceStatus[audience] = { status: "completed" };
    } else {
      audienceStatus[audience] = { status: "failed", error: outcome.reason?.message || "unknown_error" };
    }
  });
  const completed = Object.values(audienceStatus).filter((item) => item.status === "completed").length;
  return {
    artifacts,
    audiences: audienceStatus,
    status: completed === 2 ? "completed" : completed === 1 ? "partial" : "failed",
  };
}

async function loadProviderStatus() {
  try { state.provider = await fetchJson("/api/v1/explanations/provider-status"); }
  catch (_error) { /* Preserve explicit deterministic fallback when the status endpoint is unavailable. */ }
  if ($("#dashboard-provider")) renderProviderContext();
  return state.provider;
}

async function runLiveReview(form) {
  const title = $("#material-title").value.trim();
  const rawText = $("#material-text").value.trim();
  if (!title || !rawText) { showToast("请填写材料标题和待审文本。", true); return; }
  await loadProviderStatus();
  clearProcessingTimers();
  showView("processing");
  $("#processing-title").textContent = `正在提交「${title}」`;
  $("#processing-hint").textContent = "在线模式只调用已有 API；若可信知识库未准备，系统会失败关闭。";
  renderStages(0);
  try {
    $("#processing-copy").textContent = "正在创建筛查任务，并锁定材料与规则输入。";
    const created = await fetchJson("/api/v1/screenings", { method: "POST", body: JSON.stringify({ title, material_type: $("#material-type").value, raw_text: rawText, source_label: "contest_demo_workspace" }) });
    renderStages(2);
    $("#processing-copy").textContent = "风险 finding 与可信知识证据已锁定，正在读取双端基础报告。";
    const [screening, institutionReport, consumerReport] = await Promise.all([fetchJson(created.report_endpoints.screening), fetchJson(created.report_endpoints.institution_report), fetchJson(created.report_endpoints.consumer_notice)]);
    renderStages(3);
    $("#processing-copy").textContent = state.provider.mode === "real_ai"
      ? "正在生成受控合规解释：已锁定 finding 与证据，正在请求真实大模型；随后验证 Citation 与 Claim。"
      : "正在生成确定性演示解释，并验证 Citation 与 Claim。";
    let institutionArtifact = null; let consumerArtifact = null;
    let explanationStatus = { ...state.provider, status: screening.findings.length ? "pending" : "not_required" };
    if (screening.findings.length) {
      const audienceResult = await requestAudienceArtifacts(created.screening_run_id);
      institutionArtifact = audienceResult.artifacts.institution || null;
      consumerArtifact = audienceResult.artifacts.consumer || null;
      explanationStatus = { ...state.provider, ...audienceResult };
      if (explanationStatus.status !== "completed") {
        const detail = Object.entries(explanationStatus.audiences).filter(([, item]) => item.status === "failed").map(([audience, item]) => `${audience}: ${item.error}`).join("；");
        showToast(`筛查已完成；受控解释${explanationStatus.status === "partial" ? "部分成功" : "不可用"}：${detail}`, true);
      }
    }
    let citations = [];
    const citationArtifact = institutionArtifact || consumerArtifact;
    if (citationArtifact) citations = await fetchJson(`/api/v1/explanations/${citationArtifact.explanation_run_id}/citations`);
    renderStages(6);
    const constructedRuntime = citations.some((item) => String(item.source_url || "").includes("contest-demo.invalid"));
    state.current = {
      id: `live-${created.screening_run_id}`, number: "LIVE API", title, rawText, materialType: $("#material-type").value, riskLevel: screening.findings.some((row) => row.severity === "high") ? "high" : screening.findings.some((row) => row.severity === "medium") ? "medium" : "low", summary: institutionReport.disclaimer, findings: screening.findings.map((row) => apiFinding(row, institutionArtifact, consumerArtifact, citations)), mode: constructedRuntime ? "在线 API 审查 · 隔离构造赛事数据" : "在线 API 审查 · 当前后端结果", status: created.status, institutionReport, consumerReport, institutionArtifact, consumerArtifact, explanationStatus,
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
  const low = findings.filter((item) => item.severity === "low").length;
  const evidenceCount = findings.reduce((count, item) => count + item.citations.length, 0);
  $("#result-mode").textContent = result.mode;
  const boundaryNotice = result.mode.includes("隔离构造赛事数据")
    ? "当前后端运行在隔离的构造赛事数据环境；引用不代表真实监管材料、真实产品或正式可信证据。"
    : result.mode.startsWith("在线")
      ? "结果来自当前后端 API；系统输出为风险信号与证据辅助，不构成违法认定或最终法律意见。"
      : "该案例及引用均为构造展示数据，仅用于现场演示交互与能力边界；不代表真实监管材料、真实产品或正式可信证据。";
  const explanationStatus = result.explanationStatus || { label: "确定性演示", status: "completed" };
  const audienceText = explanationStatus.audiences
    ? Object.entries(explanationStatus.audiences).map(([audience, item]) => `${audience === "institution" ? "机构端" : "消费者端"}${item.status === "completed" ? "成功" : `失败：${item.error || "未知错误"}`}`).join("；")
    : "";
  const providerText = explanationStatus.status === "failed"
    ? `${explanationStatus.label}（失败：${audienceText || "未知错误"}）`
    : explanationStatus.status === "partial"
      ? `${explanationStatus.label}（部分成功：${audienceText}）`
    : explanationStatus.status === "not_required" ? "未调用（0 条风险信号）"
      : `${explanationStatus.label}（${audienceText || "成功"}）${explanationStatus.model ? ` · ${explanationStatus.model}` : ""}`;
  const providerMode = explanationStatus.mode === "real_ai" ? "真实大模型" : explanationStatus.label || "确定性演示模式";
  const providerModel = explanationStatus.model || (explanationStatus.mode === "real_ai" ? "DeepSeek V4 Flash" : "controlled-fixture-v2");
  const lowRiskNotice = findings.length ? "" : `<div class="low-risk-banner"><span>✓</span><div><strong>未发现规则命中风险</strong><p>本次未触发大模型解释，以避免无依据生成；这是受控审核边界的一部分。</p></div></div>`;
  $("#result-content").innerHTML = `<div class="result-hero"><article class="result-summary"><span class="eyebrow">AUDIT RESULT / ${escapeHtml(result.number || "CASE")}</span><h1>${escapeHtml(result.title)}</h1><p>${escapeHtml(result.summary || "审核完成。")}</p><div class="result-kpis"><div><strong class="${riskClass(result.riskLevel)}">${riskLabel(result.riskLevel)}</strong><small>综合风险等级</small></div><div><strong>${findings.length}</strong><small>Findings</small></div><div><strong>${high}</strong><small>高风险</small></div><div><strong>${medium}</strong><small>中风险</small></div><div><strong>${low}</strong><small>低风险</small></div><div><strong>${evidenceCount}</strong><small>Citation</small></div></div></article><aside class="result-status"><div><span class="eyebrow">CONTROLLED EXPLANATION</span><h3>${escapeHtml(providerMode)}</h3><p class="provider-model">${escapeHtml(providerModel)}</p></div><div class="status-list"><span>材料指纹 <b>${result.id.includes("live") ? "已生成" : "演示快照"}</b></span><span>确定性筛查 <b>已完成</b></span><span>AI 解释 <b>${escapeHtml(providerText)}</b></span><span>引用验证 <b>${findings.length && explanationStatus.status !== "failed" ? "通过 / 已保留" : "未触发"}</b></span><span>审核状态 <b>${result.status === "completed" ? "已完成" : "已创建"}</b></span></div></aside></div>${lowRiskNotice}<div class="analysis-layout"><article class="findings-panel"><header class="panel-header"><div><span class="eyebrow">RISK FINDINGS</span><h2>风险详情与证据链</h2><p class="panel-subtitle">确定性规则先锁定 finding，再绑定监管证据与受控 AI 解释。</p></div><div class="audience-toggle"><button class="active" data-audience="institution">机构合规审核解释</button><button data-audience="consumer">消费者风险提示</button></div></header><div id="finding-list">${findings.length ? findings.map(findingRow).join("") : `<div class="empty-result">未发现当前规则集中的风险信号。仍建议按既有人工审核流程确认材料版本与适用范围。</div>`}</div></article><aside class="detail-panel" id="detail-panel"><div class="detail-empty"><div><b>选择一条风险信号</b><br />查看“规则 → 监管证据 → 受控 AI”的完整链路。</div></div></aside></div>${result.rawText ? `<div class="notice"><b>材料原文：</b>${escapeHtml(result.rawText)}</div>` : ""}<div class="notice"><b>构造数据与审查边界：</b>${boundaryNotice}</div>`;
  $$("[data-audience]").forEach((button) => button.addEventListener("click", () => { state.audience = button.dataset.audience; $$("[data-audience]").forEach((item) => item.classList.toggle("active", item === button)); const selected = $(".finding-row.selected"); if (selected) renderDetail(Number(selected.dataset.findingIndex)); }));
  $$(".finding-row").forEach((row) => row.addEventListener("click", () => { $$(".finding-row").forEach((item) => item.classList.remove("selected")); row.classList.add("selected"); renderDetail(Number(row.dataset.findingIndex)); }));
  if (findings.length) { const first = $(".finding-row"); first.classList.add("selected"); renderDetail(0); }
}

function findingRow(item, index) { return `<article class="finding-row" data-finding-index="${index}"><span class="finding-index">${escapeHtml(item.id)}</span><div><div class="finding-heading"><h3>${escapeHtml(item.title)}</h3><span class="risk-pill ${riskClass(item.severity)}">${riskLabel(item.severity)}</span></div><p class="matched">命中原文：“${escapeHtml(item.matchedText)}”</p><div class="finding-meta"><span>规则说明已锁定</span><span>${escapeHtml(item.evidenceStatus)}</span><span>${item.citations.length} 条 Citation</span></div></div><span class="finding-arrow">查看 →</span></article>`; }

function renderDetail(index) {
  const item = state.current.findings[index];
  const audienceText = state.audience === "institution" ? item.institution : item.consumer;
  const audienceLabel = state.audience === "institution" ? "机构合规审核解释" : "消费者风险提示";
  const audienceSource = (state.audience === "institution" ? item.institutionSource : item.consumerSource) || "确定性演示文本";
  const citations = item.citations.length ? item.citations.map((citation) => `<article class="citation"><header><span>${escapeHtml(citation.key)}</span><span>${escapeHtml(citation.sourceKind || "证据")}</span></header><strong>${escapeHtml(citation.sourceTitle || "已验证来源")}</strong><div class="quote">“${escapeHtml(citation.quote)}”</div><p>${escapeHtml(citation.locator || "已验证定位信息")} · 已验证来源快照</p></article>`).join("") : `<p>该风险信号当前没有可用 Citation；系统按证据不足边界提示人工复核。</p>`;
  $("#detail-panel").innerHTML = `<div class="detail-title"><div><span class="eyebrow">${escapeHtml(item.id)} / CONTROLLED TRACE</span><h2>${escapeHtml(item.title)}</h2><p>证据状态：${escapeHtml(item.evidenceStatus)}</p></div><span class="risk-pill ${riskClass(item.severity)}">${riskLabel(item.severity)}</span></div><div class="detail-section trace-rule"><h4>A · 原始风险依据 / 确定性规则</h4><div class="quote">“${escapeHtml(item.matchedText)}”</div><p>${escapeHtml(item.explanation)}</p></div><div class="detail-section trace-evidence"><h4>B · 监管证据 / Citation 已绑定</h4>${citations}</div><div class="detail-section trace-model"><h4>C · AI 受控解释 / ${audienceLabel}</h4><p class="source-badge">${escapeHtml(audienceSource)}</p><p>${escapeHtml(audienceText)}</p></div><div class="detail-section"><h4>人工复核与整改建议</h4><p><b>复核：</b>${escapeHtml(item.question)}</p><p><b>建议：</b>${escapeHtml(item.remediation)}</p></div>`;
}

function bindEvents() {
  $$("[data-view-target]").forEach((button) => button.addEventListener("click", () => { showView(button.dataset.viewTarget); if (button.dataset.viewTarget === "evaluation") loadEvaluationCenter(); }));
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

renderDashboard(); renderCaseSelector(); bindEvents(); loadProviderStatus(); loadEvaluationCenter();
