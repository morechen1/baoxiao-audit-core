# Review batch completion

Production database sessions use `autoflush=False`. Review decisions therefore flush the
new decision and its `ReviewBatchItem.decision_id` association before counting undecided
batch items.

The flush remains inside the surrounding transaction: it does not commit, and any later
failure rolls back the decision, item association, document or evaluation status, and batch
completion together. A batch becomes `completed` only when no item remains undecided;
partially decided and untouched batches remain `exported`, while cancellation remains an
independent terminal state.
