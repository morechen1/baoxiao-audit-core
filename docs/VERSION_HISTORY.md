# 保销智审版本历史

## V1 — CURRENT SUBMISSION BASELINE

- System freeze: `1176d10e1fd62b7778324157e585b1f22b3cb035`
- Evaluation: `b6bdd4c15d6795106341104400d13b7b8b2e3184`
- Release: `16ebd6b2dece09a66f9d130ee9e87c669ca2305e`
- Validation: 156 frozen internal cases; Micro P 79.50%、R 88.89%、F1 83.93%。

## V2 — RESEARCH UPGRADE, NOT RELEASE REPLACEMENT

- Implementation: `387ed4bd5b6d4061800bfaff502615e67ec1893d`
- System freeze: `08acc0dd210eb022c10c38ce97fe8b16e70558f3`
- Evaluation: `ca4637935f6f244bf5855a7b867689eea2b56698`
- DEV: Micro P 93.15%、R 91.89%、F1 92.52%。
- Unseen: Micro P 90.30%、R 77.56%、F1 83.45%；Negative FA 2.08%；Hard-negative FA 2.27%。

V2 未通过替换 V1 的预设 Recall/F1 门槛。V2 unseen holdout 已完成唯一一次冻结评测，
不得重跑、用于调参或作为本候选的开发数据。

## V1 Platform Integration Candidate

- Base: `submission-v1` / `16ebd6b2dece09a66f9d130ee9e87c669ca2305e`
- Detector: V1 unchanged
- Branch: `upgrade/v1-core-v2-platform`
- Status: **INTEGRATION CANDIDATE**

Platform capabilities:

- document ingestion: text / TXT / MD / DOCX / text PDF;
- batch review with bounded concurrency and partial-failure isolation;
- HTML / JSON report export;
- long-document V1 orchestration and global offset mapping;
- exact Semantic Parser result cache and runtime diagnostics;
- Provider fail-closed user experience.

This candidate does not rerun or relabel the V1 156-case evaluation and is not named V2 Release.
