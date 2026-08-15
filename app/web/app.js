const demoCases = [
  {
    id: "high-risk",
    number: "场景 01",
    title: "高风险营销案例",
    riskLevel: "high",
    riskPosition: "监管背书、收益承诺与绝对安全",
    materialType: "advertisement",
    rawText: "监管推荐本产品，保证收益8%，本金绝对安全，今天投保即可领取限量礼品。",
    summary: "检验多项显式高风险营销主张能否形成可追溯 Finding。",
  },
  {
    id: "boundary-risk",
    number: "场景 02",
    title: "边界风险案例",
    riskLevel: "medium",
    riskPosition: "退保损失表述与合同限定并存",
    materialType: "sales_script",
    rawText: "资金使用灵活，如有需要可随时退保没有损失。具体权益和现金价值请以合同约定为准。",
    summary: "检验前后语境并存时，系统如何保留风险信号与人工复核边界。",
  },
  {
    id: "low-risk",
    number: "场景 03",
    title: "合规对照案例",
    riskLevel: "low",
    riskPosition: "合同说明、责任免除与审慎提示",
    materialType: "product_introduction",
    rawText: "本材料仅作产品信息说明，保险责任、等待期、责任免除及退保安排以正式保险合同为准。投保前请阅读条款并按需咨询持证人员。",
    summary: "确认没有通过确定性校验的风险 Finding 时，下游解释链不会被触发。",
  },
];

const state = {
  current: null,
  pendingFile: null,
  batchResults: [],
  audience: "institution",
  selectedFinding: 0,
  expandedPipelineStage: null,
  provider: {
    provider: "deterministic_fixture",
    mode: "deterministic_demo",
    label: "确定性演示",
    model: "controlled-fixture-v2",
    ready: false,
  },
};

const viewTitles = {
  dashboard: "工作台",
  review: "新建审核",
  batch: "批量审核",
  processing: "审核处理中",
  result: "当前审核结果",
  validation: "检测效果",
};

const categoryLabels = {
  absolute_claim: "绝对化或最高级表述",
  coverage_exclusion: "免责事项隐瞒或弱化",
  extra_contractual_benefit: "合同外利益承诺",
  promotion_prize: "虚假促销或奖品宣传",
  guaranteed_return: "收益或本金保证",
  comparison_ranking: "不当比较或排名",
  interest_yield: "利率或收益误导",
  no_risk: "零风险或无损失",
  product_nature: "产品性质混淆",
  regulatory_endorsement: "监管背书误导",
  surrender_cash_value: "退保或现金价值误述",
  waiting_cooling_period: "等待期或犹豫期误述",
};

const materialTypeLabels = {
  advertisement: "宣传广告",
  sales_script: "销售话术",
  social_media: "社交媒体",
  product_introduction: "产品介绍",
  other: "其他文本",
};

const rejectionReasonLabels = {
  quote_not_found: "原文 Quote 不存在",
  quote_ambiguous: "原文 Quote 多次出现",
  negation: "否定语境",
  educational_context: "教育或禁止语境",
  prohibitive_context: "禁止性表述语境",
  quoted_context: "引用他人表述",
  historical_case_context: "历史案例叙述",
  internal_incentive_context: "内部激励语境",
  product_disclosure_context: "产品说明语境",
  taxonomy_arbitration: "风险类别仲裁",
  role_ambiguity: "角色与受益对象歧义",
  semantic_ambiguity: "语义歧义",
  confidence: "置信度门禁",
  duplicate: "重复候选融合",
};

const apiErrorLabels = {
  upload_pdf_no_extractable_text: "当前版本未检测到可提取文本，暂不支持扫描图片 OCR。",
  upload_extension_not_allowed: "不支持该文件类型，请选择 TXT、MD、DOCX、PDF 或 CSV。",
  upload_content_type_not_allowed: "文件类型与浏览器识别结果不一致，已安全拒绝。",
  upload_size_invalid: "文件为空或超过当前 10MB 大小限制。",
  upload_document_empty: "材料中没有可供审核的文本。",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

function riskLabel(level) {
  return ({ high: "高风险", medium: "中风险", low: "低风险" })[level] || "待复核";
}

function riskClass(level) {
  return `risk-${level}`;
}

function displayCategory(value) {
  return categoryLabels[value] || value || "风险 Finding";
}

function showView(name) {
  if (name === "result" && !state.current) return;
  $$(".view").forEach((view) => view.classList.remove("active-view"));
  const target = $(`#${name}`);
  if (!target) return;
  target.classList.add("active-view");
  $("#view-title").textContent = viewTitles[name] || "保销智审";
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.viewTarget === name));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 4600);
}

function scenarioCard(item) {
  return `<button class="scenario-card" type="button" data-run-case="${escapeHtml(item.id)}">
    <header><span class="scenario-number">${escapeHtml(item.number)}</span><span class="risk-pill ${riskClass(item.riskLevel)}">${riskLabel(item.riskLevel)}</span></header>
    <h3>${escapeHtml(item.title)}</h3>
    <p>${escapeHtml(item.summary)}</p>
    <footer><span>${escapeHtml(item.riskPosition)}</span><strong>开始演示 →</strong></footer>
  </button>`;
}

function renderDashboard() {
  $("#dashboard-scenarios").innerHTML = demoCases.map(scenarioCard).join("");
}

function renderCaseSelector() {
  $("#review-scenarios").innerHTML = demoCases.map((item, index) => `<button class="review-scenario" type="button" data-run-case="${escapeHtml(item.id)}">
    <span>${index + 1}</span><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.riskPosition)}</small></span><b>→</b>
  </button>`).join("");
}

function fetchJson(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData) && options.body !== undefined) {
    headers["Content-Type"] = headers["Content-Type"] || "application/json";
  }
  return fetch(path, {
    ...options,
    headers,
  }).then(async (response) => {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const code = payload?.error?.code || payload?.detail || `HTTP ${response.status}`;
      throw new Error(apiErrorLabels[code] || code);
    }
    return payload;
  });
}

function formatLocator(locator) {
  if (!locator || typeof locator !== "object") return "已验证定位信息";
  const preferred = ["article_number", "article", "section", "field_name", "page_number", "paragraph"];
  const value = preferred.map((key) => locator[key]).find(Boolean);
  return value ? `相关条款：${value}` : "已验证证据定位";
}

function explanationText(artifact, findingKey, audience) {
  if (!artifact?.validated_output) return { text: "" };
  const output = artifact.validated_output;
  const rows = audience === "institution" ? output.finding_explanations : output.risk_explanations;
  const row = rows?.find((item) => item.finding_key === findingKey);
  if (!row) return { text: "" };
  const candidates = [
    row.explanation?.text,
    row.plain_language_explanation?.text,
    row.evidence_assessment?.text,
  ];
  return { text: candidates.find((value) => typeof value === "string" && value.trim()) || "" };
}

function validatedCitationsForFinding(citations, findingKey) {
  return (citations || [])
    .filter((item) => item.finding_key === findingKey)
    .map((item) => ({
      key: item.citation_key,
      kind: "citation",
      sourceKind: item.support_type,
      sourceTitle: item.source_title,
      quote: item.cited_quote,
      locator: formatLocator(item.source_locator),
      sourceUrl: item.source_url,
    }));
}

function apiFinding(
  row,
  institutionArtifact,
  consumerArtifact,
  citationsByAudience = { institution: [], consumer: [] },
) {
  const findingKey = row.finding_key;
  const evidenceSnapshots = (row.evidence || []).map((item, index) => ({
    key: `EV-${String(index + 1).padStart(2, "0")}`,
    kind: "evidence",
    sourceKind: item.support_type,
    sourceTitle: item.source?.title || "可信知识来源",
    quote: item.evidence_references?.[0]?.quote || "该证据引用由后端快照保存。",
    locator: formatLocator(item.source_locator),
    sourceUrl: item.source?.source_url || "",
  }));
  const institution = explanationText(institutionArtifact, findingKey, "institution");
  const consumer = explanationText(consumerArtifact, findingKey, "consumer");
  return {
    id: findingKey,
    ruleId: row.rule_id || "",
    title: displayCategory(row.category),
    category: row.category,
    severity: row.severity,
    matchedText: row.matched_text,
    rawStartOffset: Number.isInteger(row.raw_start_offset) ? row.raw_start_offset : null,
    rawEndOffset: Number.isInteger(row.raw_end_offset) ? row.raw_end_offset : null,
    explanation: row.explanation,
    remediation: row.remediation_template || "请由合规人员依据完整材料复核并调整表述。",
    question: row.review_question,
    evidenceStatus: row.evidence_status,
    institution: institution.text || row.explanation,
    institutionSource: institution.text ? "真实模型受控解释" : "基础报告文本（确定性筛查结果）",
    institutionDisplaySource: institution.text ? "真实模型受控解释" : "基础报告文本（Finding 已确定性校验）",
    consumer: consumer.text || row.review_question,
    consumerSource: consumer.text ? "真实模型受控解释" : "基础报告文本（确定性筛查结果）",
    consumerDisplaySource: consumer.text ? "真实模型受控解释" : "基础报告文本（Finding 已确定性校验）",
    evidenceLinks: evidenceSnapshots,
    citations: {
      institution: validatedCitationsForFinding(citationsByAudience.institution, findingKey),
      consumer: validatedCitationsForFinding(citationsByAudience.consumer, findingKey),
    },
    artifactAvailable: {
      institution: Boolean(institutionArtifact),
      consumer: Boolean(consumerArtifact),
    },
  };
}

async function createApiExplanation(runId, audience) {
  const created = await fetchJson(`/api/v1/screenings/${runId}/explanations`, {
    method: "POST",
    body: JSON.stringify({ audience, provider: state.provider.provider }),
  });
  if (created.status !== "completed") throw new Error(`explanation_${created.status || "not_completed"}`);
  return fetchJson(created.artifact_endpoint);
}

async function requestAudienceArtifacts(runId, create = createApiExplanation) {
  const audiences = ["institution", "consumer"];
  const settled = await Promise.allSettled(audiences.map((audience) => create(runId, audience)));
  const artifacts = {};
  const audienceStatus = {};
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

async function requestAudienceCitations(
  artifacts,
  fetchCitations = (explanationRunId) => fetchJson(`/api/v1/explanations/${explanationRunId}/citations`),
) {
  const audiences = ["institution", "consumer"];
  const settled = await Promise.allSettled(audiences.map((audience) => {
    const artifact = artifacts[audience];
    return artifact ? fetchCitations(artifact.explanation_run_id) : Promise.resolve([]);
  }));
  const citations = {};
  const statuses = {};
  settled.forEach((outcome, index) => {
    const audience = audiences[index];
    if (outcome.status === "fulfilled") {
      citations[audience] = outcome.value;
      statuses[audience] = { status: artifacts[audience] ? "completed" : "not_available" };
    } else {
      citations[audience] = [];
      statuses[audience] = { status: "failed", error: outcome.reason?.message || "unknown_error" };
    }
  });
  return { citations, statuses };
}

async function loadProviderStatus() {
  try {
    state.provider = await fetchJson("/api/v1/explanations/provider-status");
  } catch (_error) {
    state.provider.ready = false;
  }
  const target = $("#provider-status");
  if (target) {
    target.textContent = state.provider.ready
      ? `受控解释：${state.provider.mode === "real_ai" ? "真实模型" : "确定性演示"} · ${state.provider.model || "已配置"}`
      : "受控解释 Provider 当前不可用；风险筛查仍会失败关闭或保留确定性结果";
  }
  return state.provider;
}

async function playDemo(caseItem) {
  state.pendingFile = null;
  $("#file-input").value = "";
  $("#material-title").value = caseItem.title;
  $("#material-type").value = caseItem.materialType;
  $("#material-text").value = caseItem.rawText;
  updateTextCount();
  await runLiveReview();
}

async function runLiveReview() {
  const title = $("#material-title").value.trim();
  const rawText = $("#material-text").value.trim();
  if (!title || (!rawText && !state.pendingFile)) {
    showToast("请填写材料标题和待审材料。", true);
    return;
  }
  const submitButton = $("#live-review-form button[type='submit']");
  submitButton.disabled = true;
  await loadProviderStatus();
  showView("processing");
  $("#processing-title").textContent = "正在进行风险识别…";
  $("#processing-copy").textContent = "系统正在通过规则与语义两个通道形成候选风险，并执行确定性校验。";
  try {
    let created;
    let reportEndpoints;
    let submittedText = rawText;
    if (state.pendingFile) {
      const form = new FormData();
      form.append("file", state.pendingFile);
      form.append("material_type", $("#material-type").value);
      created = await fetchJson("/api/v2/screenings/upload", { method: "POST", body: form });
      reportEndpoints = {
        screening: created.report_endpoints.detail,
        institution_report: created.report_endpoints.institution,
        consumer_notice: created.report_endpoints.consumer,
      };
      submittedText = created.raw_text;
    } else {
      created = await fetchJson("/api/v1/screenings", {
        method: "POST",
        body: JSON.stringify({
          title,
          material_type: $("#material-type").value,
          raw_text: rawText,
          source_label: "v2_workspace",
        }),
      });
      reportEndpoints = created.report_endpoints;
    }
    $("#processing-title").textContent = "正在形成可信证据链…";
    $("#processing-copy").textContent = "风险 Finding 已完成确定性校验，系统正在读取可信 Evidence 与双端基础报告。";
    const [screening, institutionReport, consumerReport] = await Promise.all([
      fetchJson(reportEndpoints.screening),
      fetchJson(reportEndpoints.institution_report),
      fetchJson(reportEndpoints.consumer_notice),
    ]);

    const semanticStatus = screening.evidence_evaluation_summary?.semantic_parser?.status;
    if (["failed", "partial"].includes(semanticStatus)) {
      showToast("语义增强暂不可用或仅覆盖部分文本，已完成确定性筛查。", true);
    }

    let institutionArtifact = null;
    let consumerArtifact = null;
    let explanationStatus = { ...state.provider, status: screening.findings.length ? "pending" : "not_required" };
    const hasGroundedFinding = screening.findings.some(
      (finding) => finding.evidence_status !== "evidence_insufficient" && (finding.evidence || []).length,
    );
    if (screening.findings.length && hasGroundedFinding) {
      $("#processing-copy").textContent = "Finding 与监管 Evidence 已锁定，正在生成并验证机构端与消费者端受控解释。";
      const audienceResult = await requestAudienceArtifacts(created.screening_run_id);
      institutionArtifact = audienceResult.artifacts.institution || null;
      consumerArtifact = audienceResult.artifacts.consumer || null;
      explanationStatus = { ...state.provider, ...audienceResult };
      if (explanationStatus.status !== "completed") {
        const detail = Object.entries(explanationStatus.audiences)
          .filter(([, item]) => item.status === "failed")
          .map(([audience, item]) => `${audience === "institution" ? "机构端" : "消费者端"}：${item.error}`)
          .join("；");
        showToast(`筛查已完成；受控解释${explanationStatus.status === "partial" ? "部分成功" : "不可用"}：${detail}`, true);
      }
    } else if (screening.findings.length) {
      explanationStatus = { ...state.provider, status: "not_required_without_evidence", audiences: {} };
      showToast("风险 Finding 已保留，但未绑定可信监管依据，因此未生成受控解释。", true);
    }

    const citationResult = await requestAudienceCitations({
      institution: institutionArtifact,
      consumer: consumerArtifact,
    });
    explanationStatus.citations = citationResult.statuses;
    const allCitations = Object.values(citationResult.citations).flat();
    const constructedRuntime = allCitations.some((item) => String(item.source_url || "").includes("contest-demo.invalid"));
    const findings = screening.findings.map((row) => apiFinding(
      row,
      institutionArtifact,
      consumerArtifact,
      citationResult.citations,
    ));
    const riskLevel = findings.some((item) => item.severity === "high")
      ? "high"
      : findings.some((item) => item.severity === "medium") ? "medium" : "low";
    setCurrentResult({
      id: `live-${created.screening_run_id}`,
      number: `审核编号 ${created.screening_run_id}`,
      title,
      rawText: submittedText,
      materialType: $("#material-type").value,
      riskLevel,
      findings,
      mode: constructedRuntime ? "在线 API 审核 · 隔离构造赛事数据" : "在线 API 审核 · 当前后端结果",
      status: created.status,
      institutionReport,
      consumerReport,
      evidenceSummary: institutionReport.evidence_summary || {},
      explanationStatus,
      screeningDiagnostics: screening.evidence_evaluation_summary || {},
      reportEndpoints: {
        ...(created.report_endpoints || {}),
        html: created.report_endpoints?.html || `/api/v2/screenings/${created.screening_run_id}/reports/html`,
        json: created.report_endpoints?.json || `/api/v2/screenings/${created.screening_run_id}/reports/json`,
      },
    });
    state.pendingFile = null;
    $("#file-input").value = "";
    showView("result");
  } catch (error) {
    showView("review");
    showToast(`在线审核未完成：${error.message}`, true);
  } finally {
    submitButton.disabled = false;
  }
}

function setCurrentResult(result) {
  state.current = result;
  state.audience = "institution";
  state.selectedFinding = 0;
  state.expandedPipelineStage = null;
  $("#result-nav").classList.remove("is-hidden");
  renderResult();
}

function boundaryNotice(result) {
  if (result.mode.includes("隔离构造赛事数据")) {
    return "当前后端运行在隔离构造赛事数据环境；相关引用仅用于演示交互，不代表正式监管结论。";
  }
  return "结果来自当前后端 API，仅提供风险信号、可信证据与复核辅助，不构成违法认定或最终法律意见。";
}

function countValue(value) {
  return Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
}

function validationStatusLabel(status, hasFindings) {
  if (!hasFindings) return "未触发";
  if (status === "completed") return "完成";
  if (status === "partial") return "部分完成";
  if (status === "failed") return "失败关闭";
  return "未形成有效 Artifact";
}

function pipelineSnapshot(result) {
  const findings = result.findings || [];
  const diagnostics = result.screeningDiagnostics || {};
  const semantic = diagnostics.semantic_parser || {};
  const semanticAccepted = countValue(semantic.semantic_supplements);
  const modelCandidates = countValue(semantic.model_candidates);
  const rejectedCandidates = countValue(semantic.rejected_candidates);
  const deterministicCandidates = Number.isFinite(Number(diagnostics.deterministic_candidates))
    ? countValue(diagnostics.deterministic_candidates)
    : Math.max(0, findings.length - semanticAccepted);
  const candidateTotal = deterministicCandidates + modelCandidates;
  const severity = findings.reduce((counts, finding) => {
    counts[finding.severity] = (counts[finding.severity] || 0) + 1;
    return counts;
  }, { high: 0, medium: 0, low: 0 });
  const evidenceLinkCount = countValue(result.evidenceSummary?.link_count);
  const sourceCount = countValue(result.evidenceSummary?.source_document_count);
  const institutionCitationCount = findings.reduce(
    (count, finding) => count + (finding.citations?.institution || []).length,
    0,
  );
  const consumerCitationCount = findings.reduce(
    (count, finding) => count + (finding.citations?.consumer || []).length,
    0,
  );
  const citationCount = institutionCitationCount + consumerCitationCount;
  const audiences = result.explanationStatus?.audiences || {};
  const institutionDone = audiences.institution?.status === "completed";
  const consumerDone = audiences.consumer?.status === "completed";
  const anyExplanationDone = institutionDone || consumerDone;
  return {
    findings,
    semantic,
    semanticAccepted,
    modelCandidates,
    rejectedCandidates,
    deterministicCandidates,
    candidateTotal,
    severity,
    evidenceLinkCount,
    sourceCount,
    citationCount,
    institutionCitationCount,
    consumerCitationCount,
    institutionDone,
    consumerDone,
    anyExplanationDone,
  };
}

function rejectionDetail(semantic) {
  const reasons = Object.entries(semantic.reject_reasons || {}).filter(([, count]) => countValue(count) > 0);
  if (!reasons.length) return '<p class="pipeline-empty-detail">没有需要展示的语义候选拦截记录。</p>';
  return `<div class="pipeline-rejections">${reasons.map(([reason, count]) => `<span><b>× ${escapeHtml(rejectionReasonLabels[reason] || reason)}</b><strong>${countValue(count)} 条</strong></span>`).join("")}</div>`;
}

function pipelineStageCard(stage) {
  const expanded = state.expandedPipelineStage === stage.key;
  return `<button class="pipeline-stage${expanded ? " expanded" : ""}${stage.muted ? " muted" : ""}" type="button" data-pipeline-stage="${stage.key}" aria-expanded="${expanded}">
    <span class="pipeline-step">${stage.step}</span>
    <span class="pipeline-stage-copy"><strong>${stage.title}</strong><small>${stage.subtitle}</small></span>
    <span class="pipeline-stage-value">${stage.value}</span>
    <span class="pipeline-expand" aria-hidden="true">${expanded ? "−" : "+"}</span>
  </button>`;
}

function renderAuditPipeline() {
  const result = state.current;
  const target = $("#audit-pipeline");
  if (!result || !target) return;
  const data = pipelineSnapshot(result);
  const hasFindings = data.findings.length > 0;
  const explanationStatus = result.explanationStatus?.status;
  const stages = [
    {
      key: "material", step: "01", title: "材料解析", subtitle: "文本规范化与切分",
      value: "已完成",
      detail: `<div class="pipeline-detail-grid"><span><b>材料类型</b><strong>${escapeHtml(materialTypeLabels[result.materialType] || result.materialType || "文本材料")}</strong></span><span><b>文本长度</b><strong>${result.rawText.length} 字符</strong></span><span><b>处理状态</b><strong>规范化 / 切分完成</strong></span></div>`,
    },
    {
      key: "discovery", step: "02", title: "风险发现", subtitle: "规则识别 + AI 语义解析",
      value: `候选 ${data.candidateTotal}`,
      detail: `<div class="pipeline-detail-grid"><span><b>AI 语义候选</b><strong>${data.modelCandidates}</strong></span><span><b>规则 / 系统候选</b><strong>${data.deterministicCandidates}</strong></span><span><b>语义分块</b><strong>${countValue(data.semantic.semantic_chunks_completed)} / ${countValue(data.semantic.semantic_chunks)}</strong></span><span><b>Provider 调用</b><strong>${countValue(data.semantic.provider_calls)}</strong></span><span><b>缓存命中</b><strong>${countValue(data.semantic.cache_hits)}</strong></span><span><b>Provider 延迟</b><strong>${countValue(data.semantic.provider_latency_ms)} ms</strong></span><span><b>覆盖状态</b><strong>${data.semantic.partial_semantic_coverage ? "部分覆盖" : "完整 / 未启用"}</strong></span><span><b>候选总数</b><strong>${data.candidateTotal}</strong></span></div><p class="pipeline-detail-note">规则识别与语义解析协同产生风险候选；AI 语义解析只产生候选，不能直接形成最终风险结论。</p>`,
    },
    {
      key: "validation", step: "03", title: "确定性校验", subtitle: "证据、语境、角色与置信度门禁",
      value: `通过 ${data.findings.length} · 拦截 ${data.rejectedCandidates}`,
      detail: `<p class="pipeline-detail-note strong-note">候选风险经规则上下文及语义安全门禁等确定性机制校验后，形成最终 RiskFinding；AI 语义解析只生成候选，Finding 归属、severity 与证据均由系统控制。</p><div class="validation-gates"><span>规则上下文校验</span><span>原文证据校验</span><span>否定语境</span><span>教育 / 禁止语境</span><span>角色与受益对象</span><span>语义歧义</span><span>置信度门禁</span><span>重复候选融合</span></div>${rejectionDetail(data.semantic)}`,
    },
    {
      key: "finding", step: "04", title: "RiskFinding", subtitle: "最终风险事实",
      value: `${data.findings.length} 条`,
      detail: `<div class="pipeline-detail-grid"><span><b>最终形成</b><strong>${data.findings.length} 条</strong></span><span><b>高风险</b><strong>${data.severity.high}</strong></span><span><b>中风险</b><strong>${data.severity.medium}</strong></span><span><b>低风险</b><strong>${data.severity.low}</strong></span></div>`,
    },
    {
      key: "knowledge", step: "05", title: "可信监管知识", subtitle: "15 个可信来源 · 73 个 KnowledgeChunks",
      value: hasFindings ? `${data.evidenceLinkCount} EvidenceLinks` : "未触发",
      muted: !hasFindings,
      // 15/73 已于 2026-08-09 对 baoxiao_contest_final 做只读核验：15 个 approved/indexed 来源、73 个 active chunks。
      detail: hasFindings
        ? `<div class="pipeline-detail-grid"><span><b>本次 EvidenceLinks</b><strong>${data.evidenceLinkCount}</strong></span><span><b>本次监管来源</b><strong>${data.sourceCount}</strong></span><span><b>可信知识资产</b><strong>15 来源 / 73 Chunks</strong></span></div>`
        : '<p class="pipeline-empty-detail">未形成有效 RiskFinding，因此未触发可信监管知识检索。</p>',
    },
    {
      key: "explanation", step: "06", title: "受控解释与验证", subtitle: "双端解释 · Claim · Citation",
      value: hasFindings ? validationStatusLabel(explanationStatus, true) : "未触发",
      muted: !hasFindings,
      detail: hasFindings
        ? `<div class="pipeline-validation-list"><span><b>机构合规视图</b><strong>${data.institutionDone ? `完成 · ${data.institutionCitationCount} Citations` : "未形成有效 Artifact"}</strong></span><span><b>消费者权益视图</b><strong>${data.consumerDone ? `完成 · ${data.consumerCitationCount} Citations` : "未形成有效 Artifact"}</strong></span><span><b>Citation Validation</b><strong>${data.citationCount > 0 ? `${data.citationCount} 条已验证` : "未形成有效 Citation"}</strong></span><span><b>Claim Validation</b><strong>${data.anyExplanationDone ? "PASS" : "未形成有效 Artifact"}</strong></span><span><b>Uncertainty Validation</b><strong>${data.anyExplanationDone ? "PASS" : "未形成有效 Artifact"}</strong></span></div>`
        : '<p class="pipeline-empty-detail">未形成有效 RiskFinding，因此未触发受控解释与 Citation、Claim、Uncertainty 验证。</p>',
    },
  ];
  const activeStage = stages.find((stage) => stage.key === state.expandedPipelineStage);
  target.innerHTML = `<header class="pipeline-heading"><div><span class="section-kicker">Runtime Trace</span><h2>完整审核流程</h2></div><p>点击阶段查看当前审核的真实运行摘要</p></header><div class="pipeline-track">${stages.map(pipelineStageCard).join('<i aria-hidden="true">→</i>')}</div>${activeStage ? `<div class="pipeline-detail">${activeStage.detail}</div>` : ""}${!hasFindings ? '<p class="pipeline-short-circuit">未形成有效 RiskFinding，因此未触发后续监管知识检索与受控解释生成。</p>' : ""}`;
  target.querySelectorAll("[data-pipeline-stage]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.pipelineStage;
      state.expandedPipelineStage = state.expandedPipelineStage === key ? null : key;
      renderAuditPipeline();
    });
  });
}

function renderResult() {
  const result = state.current;
  if (!result) {
    $("#result-content").innerHTML = '<div class="empty-result">尚未执行审核，请先提交材料。</div>';
    return;
  }
  const findings = result.findings || [];
  const evidenceCount = countValue(result.evidenceSummary?.link_count);
  const sourceCount = countValue(result.evidenceSummary?.source_document_count);
  $("#result-mode").textContent = result.mode;
  const lowRisk = findings.length === 0;
  $("#result-content").innerHTML = `
    <section class="result-header">
      <div class="result-heading">
        <div><span class="section-kicker">审核结果</span><h1>${escapeHtml(result.title)}</h1><p>${escapeHtml(result.number)} · 风险输出已完成确定性校验</p></div>
        <div class="result-actions">
          <a class="secondary-button report-link" href="${escapeHtml(result.reportEndpoints?.html || "#")}" target="_blank" rel="noreferrer">导出 HTML</a>
          <a class="secondary-button report-link" href="${escapeHtml(result.reportEndpoints?.json || "#")}" download>导出 JSON</a>
          <button class="secondary-button" id="result-new-review" type="button">审核新材料</button>
        </div>
      </div>
      <div class="overview-grid">
        <article class="overview-card"><span>风险等级</span><strong class="${riskClass(result.riskLevel)}">${riskLabel(result.riskLevel)}</strong></article>
        <article class="overview-card"><span>风险发现</span><strong>${findings.length} 项</strong></article>
        <article class="overview-card"><span>EvidenceLinks</span><strong>${evidenceCount} 条</strong></article>
        <article class="overview-card"><span>监管来源</span><strong>${sourceCount} 个</strong></article>
      </div>
    </section>
    <section class="audit-pipeline" id="audit-pipeline"></section>
    <section class="material-card">
      <header><h2>原始审核材料</h2><span>${findings.length ? "点击风险卡片查看对应原文位置" : "完整原文"}</span></header>
      <div class="material-content" id="material-content"></div>
    </section>
    ${lowRisk ? `<section class="low-risk-banner"><span>✓</span><div><strong>当前未形成有效风险 Finding</strong><p>系统未形成通过确定性校验的风险 Finding，因此未触发下游监管知识 RAG 与解释生成。</p></div></section>` : `
      <section class="result-layout">
        <aside class="findings-panel">
          <header class="panel-heading"><h2>发现的风险</h2><p>选择一项 Finding，查看原文位置、双端解释与监管证据。</p></header>
          <div class="finding-list" id="finding-list"></div>
        </aside>
        <article class="detail-panel" id="detail-panel"></article>
      </section>`}
    <p class="result-boundary"><strong>审查边界：</strong>${escapeHtml(boundaryNotice(result))}</p>`;

  $("#result-new-review").addEventListener("click", () => showView("review"));
  renderAuditPipeline();
  renderMaterial();
  if (!lowRisk) {
    renderFindingList();
    renderDetail();
  }
}

function validFindingRanges() {
  const result = state.current;
  if (!result) return [];
  return result.findings.map((item, index) => ({
    index,
    start: item.rawStartOffset,
    end: item.rawEndOffset,
    severity: item.severity,
    matchedText: item.matchedText,
  })).filter((item) => Number.isInteger(item.start)
    && Number.isInteger(item.end)
    && item.start >= 0
    && item.end > item.start
    && item.end <= result.rawText.length
    && result.rawText.slice(item.start, item.end) === item.matchedText);
}

function highlightedMaterialHtml() {
  const text = state.current?.rawText || "";
  const ranges = validFindingRanges();
  if (!ranges.length) return escapeHtml(text);
  const boundaries = [...new Set([0, text.length, ...ranges.flatMap((item) => [item.start, item.end])])].sort((a, b) => a - b);
  const severityWeight = { high: 3, medium: 2, low: 1 };
  return boundaries.slice(0, -1).map((start, boundaryIndex) => {
    const end = boundaries[boundaryIndex + 1];
    const segment = escapeHtml(text.slice(start, end));
    const active = ranges.filter((range) => range.start <= start && end <= range.end);
    if (!active.length) return segment;
    const selected = active.find((range) => range.index === state.selectedFinding);
    const target = selected || active[0];
    const tone = [...active].sort((a, b) => severityWeight[b.severity] - severityWeight[a.severity])[0].severity;
    const activeClass = selected ? " active" : "";
    return `<mark class="source-highlight severity-${tone}${activeClass}" data-highlight-index="${target.index}">${segment}</mark>`;
  }).join("");
}

function renderMaterial() {
  const target = $("#material-content");
  if (!target) return;
  target.innerHTML = highlightedMaterialHtml();
  target.querySelectorAll("[data-highlight-index]").forEach((highlight) => {
    highlight.addEventListener("click", () => selectFinding(Number(highlight.dataset.highlightIndex)));
  });
}

function findingRow(item, index) {
  return `<button class="finding-row${index === state.selectedFinding ? " selected" : ""}" type="button" data-finding-index="${index}">
    <span class="finding-index">${escapeHtml(item.id)}</span>
    <span><span class="finding-heading"><h3>${escapeHtml(item.title)}</h3><span class="risk-pill ${riskClass(item.severity)}">${riskLabel(item.severity)}</span></span>
    <span class="matched">“${escapeHtml(item.matchedText)}”</span>
    <span class="finding-meta"><span>Finding 已校验</span><span>${escapeHtml(item.evidenceStatus)}</span><span>${item.evidenceLinks.length} EvidenceLinks</span></span></span>
  </button>`;
}

function renderFindingList() {
  const target = $("#finding-list");
  if (!target) return;
  target.innerHTML = state.current.findings.map(findingRow).join("");
  target.querySelectorAll("[data-finding-index]").forEach((button) => {
    button.addEventListener("click", () => selectFinding(Number(button.dataset.findingIndex)));
  });
}

function selectFinding(index) {
  if (!state.current?.findings[index]) return;
  state.selectedFinding = index;
  renderMaterial();
  renderFindingList();
  renderDetail();
}

function safeSourceUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.hostname.endsWith(".invalid");
  } catch (_error) {
    return false;
  }
}

function referenceCard(reference) {
  const isCitation = reference.kind === "citation";
  const sourceLink = safeSourceUrl(reference.sourceUrl)
    ? `<a href="${escapeHtml(reference.sourceUrl)}" target="_blank" rel="noreferrer">查看监管依据 ↗</a>`
    : "";
  return `<article class="citation">
    <header><span>${escapeHtml(reference.key)}</span><span>${isCitation ? "Citation 已验证" : "Evidence 快照"}</span></header>
    <strong>${escapeHtml(reference.sourceTitle || "可信知识来源")}</strong>
    <p class="citation-quote">“${escapeHtml(reference.quote || "已保存证据快照") }”</p>
    <footer><span>${escapeHtml(reference.locator || "已验证定位信息")}</span>${sourceLink}</footer>
  </article>`;
}

function citationsForAudience(item, audience) {
  return item.citations?.[audience] || [];
}

function renderDetail() {
  const item = state.current?.findings[state.selectedFinding];
  const target = $("#detail-panel");
  if (!item || !target) return;
  const institution = state.audience === "institution";
  const audienceText = institution ? item.institution : item.consumer;
  const source = institution ? item.institutionDisplaySource : item.consumerDisplaySource;
  const evidenceLinks = item.evidenceLinks.length
    ? `<div class="citation-list">${item.evidenceLinks.map(referenceCard).join("")}</div>`
    : '<p class="no-citation">当前 Finding 未绑定可用 EvidenceLink，系统保留证据不足状态并提示人工复核。</p>';
  const citations = citationsForAudience(item, state.audience);
  const citationCards = citations.length
    ? `<div class="citation-list">${citations.map(referenceCard).join("")}</div>`
    : `<p class="no-citation">${item.artifactAvailable[state.audience]
      ? "该视图未形成持久化的 validated Citation。"
      : "该视图未形成有效 Artifact，因此没有可展示的 Citation。"}</p>`;
  target.innerHTML = `
    <header class="detail-header">
      <div class="detail-title"><div><h2>${escapeHtml(item.title)}</h2><p>${escapeHtml(item.id)} · ${escapeHtml(item.ruleId || item.category)}</p></div><span class="risk-pill ${riskClass(item.severity)}">${riskLabel(item.severity)}</span></div>
      <div class="audience-tabs" role="tablist" aria-label="解释视图">
        <button type="button" role="tab" data-audience="institution" aria-selected="${institution}" class="${institution ? "active" : ""}">机构合规视图</button>
        <button type="button" role="tab" data-audience="consumer" aria-selected="${!institution}" class="${!institution ? "active" : ""}">消费者权益视图</button>
      </div>
    </header>
    <div class="detail-body">
      <section class="detail-section"><h3>风险描述</h3><div class="matched-quote">命中原文：“${escapeHtml(item.matchedText)}”</div><p>${escapeHtml(item.explanation)}</p></section>
      <section class="detail-section"><h3>受控合规解释</h3><div class="explanation-box"><span class="explanation-source">${escapeHtml(source)}</span><p>${escapeHtml(audienceText)}</p></div></section>
      <section class="detail-section"><h3>监管 EvidenceLinks</h3>${evidenceLinks}</section>
      <section class="detail-section"><h3>${institution ? "机构端" : "消费者端"} validated Citations</h3>${citationCards}</section>
      <section class="detail-section"><h3>${institution ? "人工复核与整改建议" : "消费者核实建议"}</h3><div class="remediation-grid"><div><strong>${institution ? "复核问题" : "建议核实"}</strong><p>${escapeHtml(item.question)}</p></div><div><strong>${institution ? "修改方向" : "审慎提示"}</strong><p>${escapeHtml(item.remediation)}</p></div></div></section>
    </div>`;
  target.querySelectorAll("[data-audience]").forEach((button) => {
    button.addEventListener("click", () => {
      state.audience = button.dataset.audience;
      renderDetail();
    });
  });
}

function renderBatchResults(payload) {
  state.batchResults = payload.items || [];
  const target = $("#batch-results");
  target.classList.remove("is-hidden");
  target.innerHTML = `<header class="batch-result-heading"><div><span class="section-kicker">批量执行完成</span><h2>${payload.succeeded || 0} 成功 · ${payload.failed || 0} 失败</h2></div><span class="batch-status ${payload.status === "completed" ? "ok" : "partial"}">${payload.status === "completed" ? "全部完成" : "部分完成"}</span></header>
    <div class="batch-progress"><i style="width:${Math.min(100, countValue(payload.progress))}%"></i></div>
    <div class="batch-table">${state.batchResults.map((item, index) => `<article class="batch-row">
      <div><strong>${escapeHtml(item.title || item.material || `材料 ${index + 1}`)}</strong><small>${escapeHtml(item.source_type || "未解析")} · ${item.status === "completed" ? `${countValue(item.finding_count)} Findings` : escapeHtml(item.error || "处理失败")}</small></div>
      <span class="risk-pill ${item.status === "completed" ? riskClass(item.risk_level) : "risk-high"}">${item.status === "completed" ? riskLabel(item.risk_level) : "失败"}</span>
      ${item.status === "completed" ? `<button class="secondary-button" type="button" data-batch-index="${index}">查看结果</button>` : ""}
    </article>`).join("")}</div>`;
  target.querySelectorAll("[data-batch-index]").forEach((button) => {
    button.addEventListener("click", () => openBatchResult(Number(button.dataset.batchIndex)));
  });
}

async function runBatchReview() {
  const files = [...($("#batch-file-input").files || [])];
  if (!files.length || files.length > 20) {
    showToast("请选择 1 至 20 份材料。", true);
    return;
  }
  const button = $("#batch-review-form button[type='submit']");
  button.disabled = true;
  button.textContent = "批量审核中…";
  const target = $("#batch-results");
  target.classList.remove("is-hidden");
  target.innerHTML = '<div class="batch-loading"><span class="loading-spinner"></span><strong>正在逐份执行真实筛查</strong><p>单项失败将被独立记录，不会回滚已完成结果。</p></div>';
  try {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    form.append("material_type", $("#batch-material-type").value);
    const payload = await fetchJson("/api/v2/batches", { method: "POST", body: form });
    renderBatchResults(payload);
  } catch (error) {
    target.classList.add("is-hidden");
    showToast(`批量审核未完成：${error.message}`, true);
  } finally {
    button.disabled = false;
    button.textContent = "开始批量审核";
  }
}

async function openBatchResult(index) {
  const item = state.batchResults[index];
  if (!item?.report_endpoints) return;
  showView("processing");
  $("#processing-title").textContent = "正在读取批量审核结果…";
  $("#processing-copy").textContent = "仅读取已完成的 Finding 与 Evidence，不自动触发新的解释 Provider 调用。";
  try {
    const [screening, institutionReport, consumerReport] = await Promise.all([
      fetchJson(item.report_endpoints.detail),
      fetchJson(item.report_endpoints.institution),
      fetchJson(item.report_endpoints.consumer),
    ]);
    const findings = screening.findings.map((row) => apiFinding(row, null, null));
    setCurrentResult({
      id: `batch-${item.screening_run_id}`,
      number: `审核编号 ${item.screening_run_id}`,
      title: item.title,
      rawText: item.raw_text,
      materialType: $("#batch-material-type").value,
      riskLevel: item.risk_level,
      findings,
      mode: "V2 批量审核 · 已保存结果",
      status: item.status,
      institutionReport,
      consumerReport,
      evidenceSummary: institutionReport.evidence_summary || {},
      explanationStatus: { status: "not_requested", audiences: {} },
      screeningDiagnostics: screening.evidence_evaluation_summary || {},
      reportEndpoints: item.report_endpoints,
    });
    showView("result");
  } catch (error) {
    showView("batch");
    showToast(`批量结果读取失败：${error.message}`, true);
  }
}

function updateTextCount() {
  const target = $("#text-count");
  if (target) target.textContent = String($("#material-text").value.length);
}

function bindEvents() {
  $$("[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewTarget));
  });
  $("#dashboard-start").addEventListener("click", () => showView("review"));
  document.addEventListener("click", (event) => {
    const caseButton = event.target.closest("[data-run-case]");
    if (!caseButton) return;
    const item = demoCases.find((value) => value.id === caseButton.dataset.runCase);
    if (item) playDemo(item);
  });
  $("#material-text").addEventListener("input", () => {
    if (state.pendingFile) {
      state.pendingFile = null;
      $("#file-input").value = "";
    }
    updateTextCount();
  });
  $("#file-input").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    state.pendingFile = file;
    const textPreview = /\.(txt|md)$/i.test(file.name) ? await file.text() : "";
    $("#material-text").value = textPreview;
    $("#material-text").placeholder = textPreview
      ? "已从文件读取文本，可直接提交。"
      : `已选择 ${file.name}；文档内容将在服务器安全解析。`;
    $("#material-title").value = file.name.replace(/\.(txt|md|docx|pdf)$/i, "") || "导入材料";
    updateTextCount();
    showToast(`${file.name} 已选择，尚未提交审核。`);
  });
  $("#live-review-form").addEventListener("submit", (event) => {
    event.preventDefault();
    runLiveReview();
  });
  $("#batch-review-form").addEventListener("submit", (event) => {
    event.preventDefault();
    runBatchReview();
  });
}

function loadEvaluationCenter() {
  // 保留兼容入口；FINAL 前端不再展示独立 Evaluation Center 页面。
}

renderDashboard(); renderCaseSelector(); bindEvents(); loadProviderStatus(); loadEvaluationCenter();
