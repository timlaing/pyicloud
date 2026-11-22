const { test } = require("node:test");
const assert = require("node:assert/strict");
const { notifierProof } = require("./review.cjs");
function fixture() {
  const sha = "a".repeat(40);
  const proof = { workflow_id: 17, status: "completed", conclusion: "success",
    display_title: `Review notifier policy / ${sha}`, event: "pull_request_target",
    head_branch: "feature", head_sha: sha, actor: {login: "writer"} };
  const status = {id: 1, context: "Review notifier policy", creator: {login: "github-actions[bot]"},
    state: "success", target_url: "https://github.com/owner/repo/actions/runs/42"};
  const github = {rest: {actions: {
    getWorkflowRun: async () => ({data: proof}),
    getWorkflow: async () => ({data: {id: 17, path: ".github/workflows/review-notifier-policy.yml"}}),
  }, repos: {get: async () => ({data: {default_branch: "main"}})}}};
  return {proof, status, github, check: () => notifierProof(github, {repo: {owner: "owner", repo: "repo"}}, {head: {sha}}, [status])};
}
test("only a successful current-revision run of the actual notifier workflow is accepted", async () => {
  const f = fixture();
  assert.equal(await f.check(), true);
  for (const change of [{workflow_id: 18}, {status: "in_progress"}, {conclusion: "failure"}, {display_title: "Wrong revision"}, {event: "pull_request"}]) {
    const original = structuredClone(f.proof);
    Object.assign(f.proof, change);
    assert.equal(await f.check(), false);
    Object.assign(f.proof, original);
  }
});
test("a forged status cannot point at another repository or a different workflow run", async () => {
  const f = fixture();
  for (const target of ["https://evil.invalid/owner/repo/actions/runs/42", "https://github.com/owner/other/actions/runs/42", "https://github.com/owner/repo/actions/runs/42?other=1"]) {
    f.status.target_url = target;
    assert.equal(await f.check(), false);
  }
});
test("install PR path accepts only owner-authored same-repository heads", async () => {
  const f = fixture();
  f.proof.event = "pull_request";
  const context = {repo: {owner: "owner", repo: "repo"}};
  const head = "a".repeat(40);
  const owner = {head: {sha: head, repo: {full_name: "owner/repo"}}, user: {login: "owner"}};
  assert.equal(await notifierProof(f.github, context, owner, [f.status]), true);
  assert.equal(await notifierProof(f.github, context, {head: {sha: head, repo: {full_name: "someone/fork"}}, user: {login: "owner"}}, [f.status]), false);
  assert.equal(await notifierProof(f.github, context, {head: {sha: head, repo: {full_name: "owner/repo"}}, user: {login: "writer"}}, [f.status]), false);
});
test("feature-branch dispatch requires explicit owner attestation of the exact head", async () => {
  const f = fixture();
  f.proof.event = "workflow_dispatch";
  assert.equal(await f.check(), false);
  f.proof.actor.login = "owner";
  assert.equal(await f.check(), true);
  f.proof.head_sha = "b".repeat(40);
  assert.equal(await f.check(), false);
  f.proof.head_branch = "main";
  assert.equal(await f.check(), true);
});
