const test = require("node:test");
const assert = require("node:assert/strict");
const review = require("./review.cjs");
const approve = require("./approve-workflows.cjs");
const merge = require("./dependabot.cjs");
const qualitySource = require("./quality-source.cjs");

const sha = "a".repeat(40);
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
    statuses: [],
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
        listPullRequestsAssociatedWithCommit: "associated",
        listCommitStatusesForRef: "statuses",
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
        getWorkflowRun: route("source", () => responses.ci),
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
