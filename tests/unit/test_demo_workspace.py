import hashlib
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
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


def test_step6_status_contract_distinguishes_completed_skipped_and_unavailable() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the Step 6 status contract test")
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
source += "\nglobalThis.__step6 = { validationStatusLabel, audienceStatusLabel, "
  + "explanationFailureState, requestAudienceArtifacts };";
const sandbox = { console, Promise, Object, String, Array, Error, Set, globalThis: null };
sandbox.globalThis = sandbox;
vm.runInNewContext(source, sandbox);
const {
  validationStatusLabel, audienceStatusLabel, explanationFailureState,
  requestAudienceArtifacts,
} = sandbox.__step6;

assert.equal(validationStatusLabel("completed", true), "完成");
assert.equal(validationStatusLabel("completed", true), "完成");
assert.equal(validationStatusLabel("not_required", false), "无需执行");
assert.equal(validationStatusLabel("unavailable", true), "增强暂不可用");
assert.equal(validationStatusLabel("not_requested_long_document", true), "已跳过（长文档）");
assert.equal(validationStatusLabel("not_requested", true), "未请求增强");
assert.equal(validationStatusLabel("failed", true), "验证失败关闭");
assert.equal(audienceStatusLabel("completed"), "完成");
assert.equal(audienceStatusLabel("unavailable"), "增强暂不可用");

const unavailable = new Error("provider timeout");
unavailable.code = "explanation_provider_timeout";
assert.equal(explanationFailureState(unavailable), "unavailable");
const invalid = new Error("invalid output");
invalid.code = "explanation_output_invalid_schema";
assert.equal(explanationFailureState(invalid), "failed");

const outcomes = await requestAudienceArtifacts(1, async (_runId, audience) => {
  const error = new Error(`${audience} unavailable`);
  error.code = "explanation_provider_not_configured";
  throw error;
});
assert.equal(outcomes.status, "unavailable");
assert.equal(outcomes.audiences.institution.status, "unavailable");
assert.equal(outcomes.audiences.consumer.status, "unavailable");
assert(source.includes("resetLiveReviewState();"));
assert(source.includes("if (state.provider.ready)"));
})().catch((error) => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        [node, "-e", probe, str(script_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_step6_launcher_reloads_changed_config_without_exposing_credentials() -> None:
    root = Path(__file__).parents[2]
    launcher = (root / "一键启动.command").read_text(encoding="utf-8")
    stopper = (root / "停止程序.command").read_text(encoding="utf-8")

    assert 'CONFIG_SHA_FILE="$ROOT_DIR/runtime/config.sha256"' in launcher
    assert 'CURRENT_CONFIG_SHA="$(shasum -a 256 "$CONFIG_FILE"' in launcher
    assert 'if [[ "$RUNNING_CONFIG_SHA" == "$CURRENT_CONFIG_SHA" ]]' in launcher
    assert 'stop_owned_project "$RUNNING_PID"' in launcher
    assert "! kill -0 \"$PROJECT_PID\"" in launcher
    assert 'lsof -a -p "$candidate_pid" -d cwd' in launcher
    assert 'lsof -a -p "$PROJECT_PID" -d cwd' in stopper
    assert "LLM_API_KEY" not in launcher
    assert "Authorization" not in launcher


def test_step6_launcher_restarts_owned_server_after_local_config_change(
    tmp_path: Path,
) -> None:
    required = ("bash", "curl", "lsof", "shasum")
    if any(shutil.which(command) is None for command in required):
        pytest.skip("launcher integration test requires macOS release commands")
    root = Path(__file__).parents[2]
    fixture = tmp_path / "release"
    (fixture / "config").mkdir(parents=True)
    (fixture / "runtime/logs").mkdir(parents=True)
    (fixture / "scripts").mkdir(parents=True)
    shutil.copy2(root / "一键启动.command", fixture / "一键启动.command")
    shutil.copy2(root / "停止程序.command", fixture / "停止程序.command")

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    config = fixture / "config/local.env"
    config.write_text(f"FINAL_DEMO_PORT={port}\nLLM_API_KEY=configured-later\n", encoding="utf-8")
    server = fixture / "server.py"
    server.write_text(
        """from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/health' else 404)
        self.end_headers()
        if self.path == '/health': self.wfile.write(b'{\"status\":\"ok\"}')
    def log_message(self, *_args): pass
ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
""",
        encoding="utf-8",
    )
    starter = fixture / "scripts/start_project.sh"
    starter.write_text(
        f'#!/bin/bash\nexec "{sys.executable}" "{server}" "${{FINAL_DEMO_PORT}}"\n',
        encoding="utf-8",
    )
    starter.chmod(0o755)
    fake_bin = fixture / "fake-bin"
    fake_bin.mkdir()
    fake_open = fake_bin / "open"
    fake_open.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_open.chmod(0o755)

    old_server = subprocess.Popen([sys.executable, str(server), str(port)], cwd=fixture)
    launcher: subprocess.Popen[str] | None = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2)
                break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("fixture server did not start")
        (fixture / "runtime/config.sha256").write_text("stale\n", encoding="utf-8")
        environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
        launcher = subprocess.Popen(
            ["bash", str(fixture / "一键启动.command")],
            cwd=fixture,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 15
        new_pid = 0
        expected_config_sha = hashlib.sha256(config.read_bytes()).hexdigest()
        while time.monotonic() < deadline:
            pid_file = fixture / "runtime/project.pid"
            if pid_file.is_file():
                new_pid = int(pid_file.read_text().strip())
                if new_pid != old_server.pid:
                    try:
                        urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/health", timeout=0.2
                        )
                        config_sha_file = fixture / "runtime/config.sha256"
                        if (
                            config_sha_file.is_file()
                            and config_sha_file.read_text().strip() == expected_config_sha
                        ):
                            break
                    except OSError:
                        pass
            time.sleep(0.1)
        else:
            pytest.fail("launcher did not restart the owned server")
        assert old_server.poll() is not None
        assert new_pid > 0
        assert (fixture / "runtime/config.sha256").read_text().strip() == expected_config_sha
        stopped = subprocess.run(
            ["bash", str(fixture / "停止程序.command")],
            cwd=fixture,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert stopped.returncode == 0, stopped.stderr
        launcher.wait(timeout=10)
    finally:
        if old_server.poll() is None:
            old_server.terminate()
            old_server.wait(timeout=5)
        if launcher is not None and launcher.poll() is None:
            launcher.terminate()
            launcher.wait(timeout=5)
