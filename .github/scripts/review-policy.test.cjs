// cspell:ignore basehead
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const workflow = fs.readFileSync(path.join(__dirname, "../workflows/review-notifier-policy.yml"), "utf8");
const block = workflow.split("      - name: Verify trusted action updates")[1]
  .split("      - name: Publish policy result")[0].split("          script: |\n")[1];
assert.ok(block);
const script = block.split("\n").map(line => line.startsWith("            ") ? line.slice(12) : line).join("\n");
const execute = new (Object.getPrototypeOf(async function () {}).constructor)("github", "process", script);
function run(status, tags = []) {
  const github = {
    rest: {repos: {
      get: async () => ({data: {default_branch: "main"}}),
      compareCommitsWithBasehead: async () => ({data: {status}}),
      listTags() {},
    }},
    paginate: async () => tags,
  };
  return execute(github, {env: {CHANGED_ACTIONS: JSON.stringify([{repository: "actions/github-script", sha: "b".repeat(40)}])}});
}
test("official default-branch ancestors and exact official release tags are allowed", async () => {
  await run("ahead");
  await run("identical");
  await run("diverged", [{commit: {sha: "b".repeat(40)}}]);
});
test("an arbitrary fork commit cannot replace the trusted enforcement action", async () => {
  await assert.rejects(run("behind"), /official/);
  await assert.rejects(run("diverged", [{commit: {sha: "c".repeat(40)}}]), /official/);
});
