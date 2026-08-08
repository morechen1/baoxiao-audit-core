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
    assert stylesheet.status_code == 200
    assert script.status_code == 200
    assert "demoCases" in script.text


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
const bootstrap = "renderDashboard(); renderCaseSelector(); bindEvents(); loadProviderStatus();";
source = source.replace(bootstrap, "");
source += "\nglobalThis.__m3Fix = { apiFinding, requestAudienceArtifacts };";
const sandbox = { console, Promise, Object, String, Array, Error, globalThis: null };
sandbox.globalThis = sandbox;
vm.runInNewContext(source, sandbox);
const { apiFinding, requestAudienceArtifacts } = sandbox.__m3Fix;
const artifact = (audience, rows) => ({ validated_output: {
  [audience === "institution" ? "finding_explanations" : "risk_explanations"]: rows,
}});
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
const bound = apiFinding(row, institution, consumer, []);
assert.equal(bound.id, "F001");
assert.equal(bound.institution, "机构 F001");
assert.equal(bound.consumer, "消费者 F001");
assert.equal(bound.institutionSource, "真实模型受控解释");
const fallback = apiFinding(row, null, null, []);
assert.equal(fallback.institutionSource, "基础报告文本（确定性筛查结果）");
assert.equal(fallback.consumerSource, "基础报告文本（确定性筛查结果）");
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
})().catch((error) => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        [node, "-e", probe, str(script_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
