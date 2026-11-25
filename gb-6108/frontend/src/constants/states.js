export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败", review: "复核签署",
  evidence_backfill: "证据回填", evidence_switch: "切换存储", evidence_reclaim: "回收整包",
  evidence_withdraw: "撤回切换",
});
