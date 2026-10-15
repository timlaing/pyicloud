const rules = require("../component-labels.json");

/** Return configured labels from a form selection, report text or file paths. */
function classify({ title = "", body = "", files = [], issue = false }) {
  body = body ?? "";
  // A form selection is authoritative; Unknown falls back to text matching.
  const selection = issue
    ? body.match(/(?:^|\n)### Service \/ component\s*\n+([^\n]+)/)?.[1]
    : undefined;
  const selected = selection?.split(",").map((value) => value.trim());
  const explicit = selected?.some((label) =>
    rules.some((rule) => rule.label === label),
  );
  const text = `${title}\n${body}`;
  return rules
    .filter((rule) =>
      explicit
        ? selected.includes(rule.label)
        : new RegExp(rule.text, "i").test(text) ||
          files.some((file) => new RegExp(rule.paths).test(file)),
    )
    .map((rule) => rule.label);
}

module.exports = async ({ github, context }) => {
  const issue = context.payload.issue;
  // Issue edits never relabel: classifying again would restore labels a
  // maintainer deliberately removed, and needs-triage must not return after
  // triage. issue-autolabeler.yml should only send opened and reopened events.
  if (issue && context.payload.action === "edited") return;
  const item = issue || context.payload.pull_request;
  const files = [];
  if (!issue) {
    const changes = await github.paginate(github.rest.pulls.listFiles, {
      ...context.repo,
      pull_number: item.number,
      per_page: 100,
    });
    for (const change of changes) {
      files.push(change.filename);
      if (change.previous_filename) files.push(change.previous_filename);
    }
  }
  const labels = classify({ ...item, files, issue: Boolean(issue) });
  if (issue) labels.push("needs-triage");
  // Additive: preserve labels chosen by maintainers, including other components.
  if (labels.length) {
    await github.rest.issues.addLabels({
      ...context.repo,
      issue_number: item.number,
      labels,
    });
  }
};
module.exports.classify = classify;
