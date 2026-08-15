# 保销智审版本历史

## V1

System freeze:
`1176d10e1fd62b7778324157e585b1f22b3cb035`

Evaluation:
`b6bdd4c15d6795106341104400d13b7b8b2e3184`

Release:
`16ebd6b2dece09a66f9d130ee9e87c669ca2305e`

Validation:

- 156 frozen internal validation cases
- Micro Precision: 79.50%
- Micro Recall: 88.89%
- Micro F1: 83.93%

Status:
**CURRENT SUBMISSION BASELINE**

---

## V2

Implementation:
`387ed4bd5b6d4061800bfaff502615e67ec1893d`

System freeze:
`08acc0dd210eb022c10c38ce97fe8b16e70558f3`

Evaluation:
`ca4637935f6f244bf5855a7b867689eea2b56698`

DEV:

- Micro Precision: 93.15%
- Micro Recall: 91.89%
- Micro F1: 92.52%

UNSEEN:

- Micro Precision: 90.30%
- Micro Recall: 77.56%
- Micro F1: 83.45%
- Negative false alarm: 2.08%
- Hard-negative false alarm: 2.27%

Status:
**RESEARCH UPGRADE — NOT RELEASE REPLACEMENT**

Reason:
V2 achieved higher precision and substantially lower false alarms, but recall and Micro F1 did
not pass the predefined release gate.

The V2 unseen holdout is a frozen one-shot evaluation asset. It must not be rerun or reused for
tuning, prompt changes, validator changes, threshold selection, or rule development.
