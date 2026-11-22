const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const review = require("./review.cjs");
const approve = require("./approve-workflows.cjs");
const merge = require("./dependabot.cjs");
const qualitySource = require("./quality-source.cjs");

const sha = "a".repeat(40);
const observerText = fs.readFileSync(path.join(__dirname, "../workflows/review-status.yml"), "utf8");
const observerBlock = observerText.split("          script: |\n")[1];
const observerScript = observerBlock.split("\n").map(line => line.startsWith("            ") ? line.slice(12) : line).join("\n");
const runObserver = new (Object.getPrototypeOf(async function () {}).constructor)("github", "context", "core", "require", observerScript);
const pr = {
  number: 42,
  state: "open",
  draft: false,
  user: { id: 7, login: "contributor" },
  head: { sha, repo: { full_name: "someone/fork" } },
  base: { ref: "main", repo: { full_name: "owner/repo" } },
};
function fixture(event = "pull_request") {
  const calls = [];
  const responses = {
    associated: [structuredClone(pr)],
    reviews: [],
    checks: ["CI passed", "SonarCloud", "CodeQL"].map((name, id) => ({
      id,
      name,
      app: {
        slug:
          name === "CI passed" ? "github-actions" : "github-advanced-security",
      },
      status: "completed",
      conclusion: "success",
    })),
    statuses: [{id: 1, context: "Review notifier policy", creator: {login: "github-actions[bot]"}, state: "success", target_url: "https://github.com/owner/repo/actions/runs/42"}, {id: 2, context: "Quality", creator: {login: "github-actions[bot]"}, state: "pending"}],
    comments: [],
    previous: [],
    runs: [],
    permission: "read",
    current: structuredClone(pr),
  };
  const route = (name, value) => async (args) => {
    calls.push([name, args]);
    return { data: typeof value === "function" ? value(args) : value };
  };
  const github = {
    rest: {
      repos: {
        get: route("repository", {default_branch: "main"}),
        listPullRequestsAssociatedWithCommit: "associated",
        listCommitStatusesForRef: "statuses",
        createCommitStatus: route("status", {}),
        getCollaboratorPermissionLevel: route("permission", () => ({
          permission: responses.permission,
        })),
      },
      checks: { listForRef: "checks" },
      pulls: {
        get: route("get", () => responses.current),
        listReviews: "reviews",
        list: "previous",
        createReview: route("approve", {}),
        merge: route("merge", { merged: true }),
      },
      issues: {
        listComments: "comments",
        createComment: route("comment", (args) => ({
          id: 99,
          body: args.body,
        })),
        updateComment: route("updateComment", {}),
      },
      actions: {
        listWorkflowRunsForRepo: "runs",
        approveWorkflowRun: route("approveRun", {}),
        getWorkflowRun: route("source", (args) => Number(args.run_id) === 42 ? {workflow_id: 17, status: "completed", conclusion: "success", display_title: `Review notifier policy / ${sha}`, event: "pull_request_target"} : responses.ci),
        getWorkflow: route("notifierWorkflow", {id: 17, path: ".github/workflows/review-notifier-policy.yml"}),
      },
    },
    paginate: async (endpoint) => responses[endpoint],
    graphql: async (...args) => calls.push(["graphql", args]),
  };
  const context = {
    repo: { owner: "owner", repo: "repo" },
    payload: {
      workflow_run: { event, head_sha: sha, path: ".github/workflows/ci.yml" },
    },
  };
  return {
    github,
    context,
    core: { info() {} },
    sleep: async () => {},
    responses,
    calls,
  };
}

test("CodeRabbit requests are deduplicated while waiting for exact-head approval", async () => {
  const f = fixture();
  f.sleep = async () => {
    f.responses.reviews = [
      {
        user: { login: "coderabbitai[bot]" },
        commit_id: sha,
        state: "APPROVED",
      },
    ];
  };
  await review(f);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 1);
  f.responses.comments = [
    {
      user: { login: "github-actions[bot]" },
      body: `<!-- coderabbit-after-checks:${sha} -->`,
    },
  ];
  f.responses.reviews = [];
  await review(f);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 1);
});

test("failed requests can retry while the number of requests remains bounded", async () => {
  const f = fixture();
  await assert.rejects(review(f), /40 minutes/);
  const failure = f.calls.find(([name]) => name === "updateComment")[1].body;
  assert.match(failure, /coderabbit-request-failed/);
  const comment = { user: { login: "github-actions[bot]" }, body: failure };
  f.responses.comments = [comment];
  f.sleep = async () => {
    f.responses.reviews = [
      {
        user: { login: "coderabbitai[bot]" },
        commit_id: sha,
        state: "APPROVED",
      },
    ];
  };
  await review(f);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 2);
  f.responses.reviews = [];
  f.responses.comments = [comment, comment, comment];
  await assert.rejects(review(f), /Three CodeRabbit requests failed/);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 2);
});

test("a changed PR head stops the review wait", async () => {
  const f = fixture();
  f.responses.current.head.sha = "b".repeat(40);
  await assert.rejects(review(f), /changed during review/);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 0);
});

test("an unreviewed direct push or old-head approval cannot pass main quality", async () => {
  const f = fixture("push");
  f.responses.associated = [];
  await assert.rejects(review(f), /direct pushes/);
  f.responses.associated = [
    { ...structuredClone(pr), merged_at: "2026-11-01", merge_commit_sha: sha },
  ];
  f.responses.reviews = [
    {
      user: { login: "coderabbitai[bot]" },
      commit_id: "b".repeat(40),
      state: "APPROVED",
    },
  ];
  await assert.rejects(review(f), /final head commit/);
  f.responses.reviews[0].commit_id = sha;
  await review(f);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 0);
});

test("first-time contributors cannot approve their own workflow runs", async () => {
  const f = fixture();
  f.context.payload.pull_request = structuredClone(pr);
  f.responses.runs = [
    {
      id: 123,
      head_sha: sha,
      path: ".github/workflows/ci.yml",
      conclusion: "action_required",
    },
  ];
  await approve(f);
  assert.equal(f.calls.filter(([name]) => name === "approveRun").length, 0);
});

test("only returning contributors and current CI runs are approved", async () => {
  const f = fixture();
  f.context.payload.pull_request = structuredClone(pr);
  f.responses.previous = [
    { ...structuredClone(pr), number: 1, merged_at: "2026-11-01" },
  ];
  f.responses.runs = [
    {
      id: 123,
      head_sha: sha,
      path: ".github/workflows/ci.yml",
      conclusion: "action_required",
    },
    {
      id: 124,
      head_sha: sha,
      path: ".github/workflows/other.yml",
      conclusion: "action_required",
    },
    {
      id: 125,
      head_sha: "b".repeat(40),
      path: ".github/workflows/ci.yml",
      conclusion: "action_required",
    },
  ];
  await approve(f);
  assert.deepEqual(
    f.calls
      .filter(([name]) => name === "approveRun")
      .map(([, args]) => args.run_id),
    [123],
  );
});

test("Quality provenance resolves the triggering CI run, not the default-branch SHA", async () => {
  const f = fixture();
  f.context.payload.workflow_run = {
    path: ".github/workflows/sonar.yml",
    conclusion: "success",
    event: "workflow_run",
    display_title: "Quality · CI 123",
  };
  f.responses.ci = {
    path: ".github/workflows/ci.yml",
    conclusion: "success",
    repository: { full_name: "owner/repo" },
    head_sha: sha,
  };
  assert.equal((await qualitySource(f)).head_sha, sha);
  f.responses.ci.conclusion = "failure";
  await assert.rejects(qualitySource(f), /Invalid source CI/);
});

test("Dependabot approval and auto-merge are bound to the validated head", async () => {
  const f = fixture();
  f.context.payload.workflow_run = {
    path: ".github/workflows/sonar.yml",
    conclusion: "success",
    event: "workflow_run",
    display_title: "Quality · CI 123",
  };
  f.responses.ci = {
    path: ".github/workflows/ci.yml",
    conclusion: "success",
    repository: { full_name: "owner/repo" },
    event: "pull_request",
    head_sha: sha,
  };
  f.responses.current.user.login = "dependabot[bot]";
  f.responses.current.head.repo.full_name = "owner/repo";
  await merge(f);
  assert.equal(f.calls.find(([name]) => name === "approve")[1].commit_id, sha);
  assert.equal(f.calls.find(([name]) => name === "graphql")[1][1].sha, sha);
  f.calls.length = 0;
  f.responses.current.head.sha = "b".repeat(40);
  await merge(f);
  assert.equal(
    f.calls.filter(([name]) => ["approve", "graphql", "merge"].includes(name))
      .length,
    0,
  );
});

test("release publication rejects PR-only, wrong-SHA, failed, and foreign Quality results", async () => {
  const f = fixture();
  f.context.serverUrl = "https://github.com";
  f.github.rest.repos.listCommitStatusesForRef = "statuses";
  f.responses.statuses = [
    {
      context: "Quality",
      state: "success",
      creator: { login: "github-actions[bot]" },
      target_url: "https://github.com/owner/repo/actions/runs/900",
    },
  ];
  const quality = {
    path: ".github/workflows/sonar.yml",
    conclusion: "success",
    event: "workflow_run",
    display_title: "Quality · CI 123",
  };
  f.responses.ci = {
    path: ".github/workflows/ci.yml",
    conclusion: "success",
    repository: { full_name: "owner/repo" },
    event: "pull_request",
    head_branch: "feature",
    head_sha: sha,
  };
  f.github.rest.actions.getWorkflowRun = async ({ run_id }) => ({
    data: run_id === 900 ? quality : f.responses.ci,
  });
  const check = () => qualitySource.requireMainQuality({ ...f, sha });
  await assert.rejects(check(), /main-push Quality/);
  f.responses.ci.event = "push";
  f.responses.ci.head_branch = "main";
  await check();
  f.responses.ci.head_sha = "b".repeat(40);
  await assert.rejects(check(), /exact SHA/);
  f.responses.ci.head_sha = sha;
  quality.conclusion = "failure";
  await assert.rejects(check(), /successful trusted Quality/);
  quality.conclusion = "success";
  f.responses.statuses[0].target_url =
    "https://github.com/another/repo/actions/runs/900";
  await assert.rejects(check(), /in this repository/);
});

test("a queued CI run is present and must not time out or be approved again", async () => {
  const f = fixture();
  f.context.payload.pull_request = structuredClone(pr);
  f.responses.permission = "write";
  f.responses.runs = [
    {
      id: 123,
      head_sha: sha,
      path: ".github/workflows/ci.yml",
      status: "queued",
      conclusion: null,
    },
  ];
  f.sleep = async () => {
    throw new Error("Queued CI must not wait for approval");
  };
  await approve(f);
  assert.equal(f.calls.filter(([name]) => name === "approveRun").length, 0);
});

test("CodeRabbit cannot start before all reported checks succeed", async () => {
  for (const state of ["pending", "failure", "error"]) {
    const f = fixture();
    f.responses.statuses.push({
      id: 99,
      context: "External documentation",
      state,
    });
    await assert.rejects(review(f), /40 minutes/);
    assert.equal(f.calls.filter(([name]) => name === "comment").length, 0);
  }
  const f = fixture();
  f.responses.statuses.push({
    id: 99,
    context: "External documentation",
    state: "pending",
  });
  f.sleep = async () => {
    f.responses.statuses.at(-1).state = "success";
    if (f.calls.some(([name]) => name === "comment"))
      f.responses.reviews = [
        {
          user: { login: "coderabbitai[bot]" },
          commit_id: sha,
          state: "APPROVED",
        },
      ];
  };
  await review(f);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 1);
});

test("review dismissal notifications withdraw Quality until current-head approval returns", async () => {
  const f = fixture("pull_request_review");
  f.responses.reviews = [
    {
      user: { login: "coderabbitai[bot]" },
      commit_id: sha,
      state: "DISMISSED",
    },
  ];
  await assert.rejects(review(f), /40 minutes/);
  f.responses.reviews.push({
    user: { login: "coderabbitai[bot]" },
    commit_id: sha,
    state: "APPROVED",
  });
  await review(f);
});

test("same-named checks from another App cannot hide validation failure", async () => {
  const f = fixture();
  f.responses.checks.push({id: 10, name: "External security", app: {slug: "trusted"}, status: "completed", conclusion: "failure"});
  f.responses.checks.push({id: 11, name: "External security", app: {slug: "other"}, status: "completed", conclusion: "success"});
  await assert.rejects(review(f), /40 minutes/);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 0);
});
test("the notifier policy needs its trusted commit status, not a PR CheckRun", async () => {
  const f = fixture();
  f.responses.statuses = [];
  f.responses.checks.push({id: 12, name: "Review notifier policy", app: {slug: "github-actions"}, status: "completed", conclusion: "success"});
  await assert.rejects(review(f), /40 minutes/);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 0);
});

test("retargeting away from main cannot trigger a review", async () => {
  const f = fixture();
  f.responses.current.base.ref = "other";
  await assert.rejects(review(f), /closed or changed/);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 0);
});

test("review results from another producer are not excluded", async () => {
  const f = fixture();
  f.responses.checks.push({id: 17, name: "CodeRabbit review", app: {slug: "other"}, status: "completed", conclusion: "failure"});
  await assert.rejects(review(f), /40 minutes/);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 0);
});
test("merge-only commit failures block native review", async () => {
  const f = fixture();
  const mergeSha = "b".repeat(40);
  f.responses.current.merge_commit_sha = mergeSha;
  const paginate = f.github.paginate;
  f.github.paginate = async (endpoint, args) => endpoint === "statuses" && args.ref === mergeSha
    ? [{id: 20, context: "Merge analysis", creator: {login: "external"}, state: "failure"}]
    : paginate(endpoint, args);
  await assert.rejects(review(f), /40 minutes/);
  assert.equal(f.calls.filter(([name]) => name === "comment").length, 0);
});
test("shared PR heads invalidate native Quality rather than reusing another PR's approval", async () => {
  const f = fixture();
  f.responses.associated.push({...structuredClone(pr), number: 43});
  await assert.rejects(review(f), /exactly one/);
  assert.ok(f.calls.some(([name, args]) => name === "status" && args.context === "Quality" && args.state === "failure"));
});

test("a native timeout cannot overwrite approval arriving during the final wait", async () => {
  const f = fixture();
  let sleeps = 0;
  f.sleep = async () => {if (++sleeps === 40) f.responses.reviews = [{user: {login: "coderabbitai[bot]"}, commit_id: sha, state: "APPROVED"}];};
  await review(f);
  assert.equal(f.calls.filter(([name]) => name === "updateComment").length, 0);
});

test("observer publishes Quality transitions and reaches the worker", async () => {
  const f = fixture();
  const context = {
    repo: { owner: "owner", repo: "repo" },
    payload: { workflow_run: {
      event: "pull_request", head_sha: sha,
      path: ".github/workflows/review-events.yml", conclusion: "success",
    } },
    serverUrl: "https://github.com", runId: 99,
  };
  let reached = 0;
  const load = (name) => {
    assert.equal(name, "./.github/scripts/review.cjs");
    return async () => { reached++; };
  };
  await runObserver(f.github, context, f.core, load);
  assert.equal(reached, 1);
  const states = f.calls.filter(([name]) => name === "status").map(([, args]) => args.state);
  assert.deepEqual(states, ["pending", "success"]);

  f.calls.length = 0;
  await assert.rejects(
    runObserver(f.github, context, f.core, () => async () => { throw new Error("approval withdrawn"); }),
    /approval withdrawn/,
  );
  const failed = f.calls.filter(([name]) => name === "status").map(([, args]) => args.state);
  assert.deepEqual(failed, ["pending", "failure"]);
});
