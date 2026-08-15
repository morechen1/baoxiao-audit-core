import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_contest_demo_workspace_and_assets_are_served() -> None:
    client = TestClient(app)

    workspace = client.get("/")
    stylesheet = client.get("/static/styles.css")
    script = client.get("/static/app.js")

    assert workspace.status_code == 200
    assert "保销智审" in workspace.text
    assert "新建智能审核" in workspace.text
    assert "批量材料审核" in workspace.text
    assert ".docx,.pdf" in workspace.text
    assert stylesheet.status_code == 200
    assert script.status_code == 200
    assert "demoCases" in script.text
    assert "/api/v2/screenings/upload" in script.text
    assert "/api/v2/batches" in script.text
    assert "语义增强暂不可用" in script.text
    assert "导出 HTML" in script.text


def test_explanation_ui_uses_finding_keys_and_keeps_partial_audience_artifacts() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the focused browser-script unit test")
    script_path = Path(__file__).parents[2] / "app" / "web" / "app.js"
    probe = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
(async () => {
let source = fs.readFileSync(process.argv[1], "utf8");
const bootstrap = "renderDashboard(); renderCaseSelector(); bindEvents(); "
  + "loadProviderStatus(); loadEvaluationCenter();";
source = source.replace(bootstrap, "");
source += "\nglobalThis.__releaseFix = { apiFinding, requestAudienceArtifacts, "
  + "requestAudienceCitations, citationsForAudience, pipelineSnapshot };";
const sandbox = { console, Promise, Object, String, Array, Error, globalThis: null };
sandbox.globalThis = sandbox;
vm.runInNewContext(source, sandbox);
const {
  apiFinding, requestAudienceArtifacts, requestAudienceCitations,
  citationsForAudience, pipelineSnapshot,
} = sandbox.__releaseFix;
const artifact = (audience, rows) => ({
  explanation_run_id: audience === "institution" ? 101 : 202,
  validated_output: {
    [audience === "institution" ? "finding_explanations" : "risk_explanations"]: rows,
  },
});
const institution = artifact("institution", [
  { finding_key: "F002", explanation: { text: "机构 F002" } },
  { finding_key: "F001", explanation: { text: "机构 F001" } },
]);
const consumer = artifact("consumer", [
  { finding_key: "F002", plain_language_explanation: { text: "消费者 F002" } },
  { finding_key: "F001", plain_language_explanation: { text: "消费者 F001" } },
]);
const row = {
  finding_key: "F001", category: "风险", severity: "high", matched_text: "命中",
  explanation: "基础机构文本", review_question: "基础消费者文本",
  evidence_status: "supported", evidence: [],
};
const citation = (findingKey, key, title) => ({
  finding_key: findingKey, citation_key: key, support_type: "regulation",
  source_title: title, cited_quote: `${title}原文`, source_locator: {article:"第二条"},
  source_url: `https://example.com/${key}`,
});
const institutionCitations = [citation("F001", "E001", "机构依据")];
const consumerCitations = [citation("F001", "E002", "消费者依据")];
const bound = apiFinding(row, institution, consumer, {
  institution: institutionCitations, consumer: consumerCitations,
});
assert.equal(bound.id, "F001");
assert.equal(bound.institution, "机构 F001");
assert.equal(bound.consumer, "消费者 F001");
assert.equal(bound.institutionSource, "真实模型受控解释");
assert.equal(bound.citations.institution[0].key, "E001");
assert.equal(bound.citations.consumer[0].key, "E002");
assert.equal(bound.evidenceLinks.length, 0);
const switched = ["institution", "consumer", "institution", "consumer", "institution"]
  .map((audience) => citationsForAudience(bound, audience)[0].key);
assert.deepEqual(switched, ["E001", "E002", "E001", "E002", "E001"]);
const fallback = apiFinding(row, null, null, { institution: [], consumer: [] });
assert.equal(fallback.institutionSource, "基础报告文本（确定性筛查结果）");
assert.equal(fallback.consumerSource, "基础报告文本（确定性筛查结果）");
assert.equal(fallback.citations.institution.length, 0);
assert.equal(fallback.citations.consumer.length, 0);
assert(source.includes("部分成功"));

async function resultFor(outcomes) {
  return requestAudienceArtifacts(1, async (_runId, audience) => {
    const value = outcomes[audience];
    if (value instanceof Error) throw value;
    return value;
  });
}
const both = await resultFor({ institution, consumer });
assert.equal(both.status, "completed");
assert.equal(both.artifacts.institution, institution);
assert.equal(both.artifacts.consumer, consumer);
const institutionOnly = await resultFor({ institution, consumer: new Error("consumer_failed") });
assert.equal(institutionOnly.status, "partial");
assert.equal(institutionOnly.artifacts.institution, institution);
assert.equal(institutionOnly.artifacts.consumer, undefined);
assert.equal(institutionOnly.audiences.consumer.status, "failed");
const consumerOnly = await resultFor({ institution: new Error("institution_failed"), consumer });
assert.equal(consumerOnly.status, "partial");
assert.equal(consumerOnly.artifacts.consumer, consumer);
assert.equal(consumerOnly.artifacts.institution, undefined);
assert.equal(consumerOnly.audiences.institution.status, "failed");
const neither = await resultFor({
  institution: new Error("institution_failed"), consumer: new Error("consumer_failed"),
});
assert.equal(neither.status, "failed");
assert.deepEqual(Object.keys(neither.artifacts), []);

const citationCalls = [];
const audienceCitations = await requestAudienceCitations(
  { institution, consumer },
  async (runId) => {
    citationCalls.push(runId);
    return runId === institution.explanation_run_id ? institutionCitations : consumerCitations;
  },
);
assert.equal(citationCalls.length, 2);
assert.equal(audienceCitations.citations.institution[0].citation_key, "E001");
assert.equal(audienceCitations.citations.consumer[0].citation_key, "E002");
const partialCitations = await requestAudienceCitations(
  { institution, consumer: null },
  async () => institutionCitations,
);
assert.equal(partialCitations.citations.institution.length, 1);
assert.equal(partialCitations.citations.consumer.length, 0);
assert.equal(partialCitations.statuses.consumer.status, "not_available");

const snapshot = pipelineSnapshot({
  findings: [bound], evidenceSummary: { link_count: 7, source_document_count: 3 },
  explanationStatus: { audiences: {
    institution: {status:"completed"}, consumer: {status:"completed"},
  } }, screeningDiagnostics: {},
});
assert.equal(snapshot.evidenceLinkCount, 7);
assert.equal(snapshot.sourceCount, 3);
assert.equal(snapshot.institutionCitationCount, 1);
assert.equal(snapshot.consumerCitationCount, 1);
})().catch((error) => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        [node, "-e", probe, str(script_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
