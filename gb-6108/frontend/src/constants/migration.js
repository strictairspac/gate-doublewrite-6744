export const MigrationStates = Object.freeze({
  backfilling: "回填中",
  backfilled: "已回填·读整包",
  switched: "已切换·读页存储",
  reclaimed: "已回收·读页存储",
});

// Buttons offered per state. Withdraw stays available after reclaim because
// the whole-package snapshot is retained until the migration row is removed.
export const MigrationActions = Object.freeze({
  backfilling: [],
  backfilled: ["switch"],
  switched: ["reclaim", "withdraw"],
  reclaimed: ["withdraw"],
});

export const MigrationActionLabels = Object.freeze({
  backfill: "回填该批",
  switch: "核对并切换",
  reclaim: "回收整包副本",
  withdraw: "撤回并还原整包",
});
