import { api } from "../../api/workbench.js";
import { el } from "../../utils/dom.js";
import { time } from "../../utils/format.js";
import { MigrationActionLabels, MigrationActions, MigrationStates } from "../../constants/migration.js";
import { section } from "../common/panel.js";

const endpoint = {
  backfill: api.backfillRun,
  switch: api.switchRun,
  reclaim: api.reclaimRun,
  withdraw: api.withdrawRun,
};

function stateBadge(state) {
  return el("span", {class: `status state-migration-${state}`, "data-state": state},
    MigrationStates[state] || state);
}

function actionButtons(runId, state, onChange, issue) {
  const names = MigrationActions[state] || [];
  return el("div", {class: "inline-actions"}, names.map(name =>
    el("button", {
      type: "button",
      class: name === "withdraw" ? "quiet" : "",
      onclick: async event => {
        const button = event.target;
        button.disabled = true;
        issue.textContent = "";
        try {
          await endpoint[name](runId);
          await onChange();
        } catch (error) {
          issue.textContent = error.message;
          button.disabled = false;
        }
      },
    }, MigrationActionLabels[name])));
}

function progress(entry) {
  return `${entry.written_pages}/${entry.page_count} 页`;
}

/**
 * Whole-package -> per-page storage control panel.
 * Runs without a migration row appear first so the operator can backfill
 * batches that the worker has not reached yet.
 */
export async function migrationPanel(isOperator, onChange) {
  const [runs, migrations] = await Promise.all([api.runs(), api.migrations()]);
  const issue = el("p", {class: "feedback", role: "alert"});
  const byRun = new Map(migrations.map(item => [item.run_id, item]));
  const doneRuns = runs.filter(item => item.status === "done");

  const rows = doneRuns.map(run => {
    const entry = byRun.get(run.id);
    if (!entry) {
      const backfill = isOperator ? el("button", {
        type: "button",
        onclick: async event => {
          event.target.disabled = true;
          issue.textContent = "";
          try {
            await api.backfillRun(run.id);
            await onChange();
          } catch (error) {
            issue.textContent = error.message;
            event.target.disabled = false;
          }
        },
      }, MigrationActionLabels.backfill) : el("span", {}, "待回填");
      return [run.id, run.case_title, stateBadge("backfilling"), "0 页", backfill];
    }
    return [
      entry.run_id, run.case_title, stateBadge(entry.state), progress(entry),
      isOperator ? actionButtons(entry.run_id, entry.state, onChange, issue) : "—",
    ];
  });

  const sweep = isOperator ? el("button", {
    type: "button",
    onclick: async event => {
      event.target.disabled = true;
      issue.textContent = "";
      try {
        const result = await api.backfillAll();
        if (!result.backfilled.length) issue.textContent = "没有待回填的批次。";
        await onChange();
      } catch (error) {
        issue.textContent = error.message;
        event.target.disabled = false;
      }
    },
  }, "回填全部待迁移批次") : null;

  return section("证据存储迁移",
    el("p", {class: "panel-hint"},
      "回填按页写入并保留整包；核对页序、正文与证据指纹一致后才切换读取来源；切换失败或撤回时继续读整包。"),
    sweep, issue,
    rows.length ? el("div", {class: "table-scroll", tabindex: 0, role: "region", "aria-label": "存储迁移进度"},
      el("table", {},
        el("thead", {}, el("tr", {}, ["批次", "资料", "迁移状态", "页进度", "操作"]
          .map(text => el("th", {scope: "col"}, text)))),
        el("tbody", {}, rows.map(row => el("tr", {}, row.map(value =>
          el("td", {}, value instanceof Node ? value : String(value)))))))) :
      el("p", {class: "empty"}, "暂无已完成批次。"));
}
