/** Approve only current CI runs for returning contributors to this repository. */
module.exports = async ({
  github,
  context,
  core,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}) => {
  const { owner, repo } = context.repo;
  let pr = context.payload.pull_request;
  if (!pr) {
    const run = context.payload.workflow_run;
    if (run.event !== "pull_request" || run.path !== ".github/workflows/ci.yml")
      return;
    const prs = await github.paginate(
      github.rest.repos.listPullRequestsAssociatedWithCommit,
      { owner, repo, commit_sha: run.head_sha, per_page: 100 },
    );
    const matches = prs.filter(
      (item) =>
        item.state === "open" &&
        item.head.sha === run.head_sha &&
        item.base.repo.full_name === `${owner}/${repo}` &&
        item.base.ref === "main",
    );
    if (matches.length !== 1) return;
    [pr] = matches;
  }
  if (pr.base.repo.full_name !== `${owner}/${repo}` || pr.base.ref !== "main")
    return;
  const { data: current } = await github.rest.pulls.get({
    owner,
    repo,
    pull_number: pr.number,
  });
  if (current.state !== "open" || current.head.sha !== pr.head.sha) return;
  let eligible =
    current.user.login === "dependabot[bot]" &&
    current.head.repo.full_name === `${owner}/${repo}`;
  if (!eligible) {
    try {
      const { data } = await github.rest.repos.getCollaboratorPermissionLevel({
        owner,
        repo,
        username: current.user.login,
      });
      eligible = ["admin", "maintain", "write"].includes(data.permission);
    } catch (error) {
      if (error.status !== 404) throw error;
    }
  }
  if (!eligible) {
    const previous = await github.paginate(github.rest.pulls.list, {
      owner,
      repo,
      state: "closed",
      per_page: 100,
    });
    eligible = previous.some(
      (item) =>
        item.user.id === current.user.id &&
        item.merged_at &&
        item.number !== current.number,
    );
  }
  if (!eligible) {
    core.info("First-time contributor: manual workflow approval required.");
    return;
  }
  // The PR event can precede creation of the approval-blocked run.
  for (let attempt = 0; attempt < 18; attempt++) {
    const { data: latest } = await github.rest.pulls.get({
      owner,
      repo,
      pull_number: current.number,
    });
    if (latest.state !== "open" || latest.head.sha !== current.head.sha) return;
    const runs = await github.paginate(
      github.rest.actions.listWorkflowRunsForRepo,
      {
        owner,
        repo,
        head_sha: current.head.sha,
        event: "pull_request",
        per_page: 100,
      },
    );
    const ci = runs.filter(
      (run) =>
        run.path === ".github/workflows/ci.yml" &&
        run.head_sha === current.head.sha,
    );
    for (const run of ci.filter(
      (item) =>
        item.status === "action_required" ||
        item.conclusion === "action_required",
    )) {
      await github.rest.actions.approveWorkflowRun({
        owner,
        repo,
        run_id: run.id,
      });
      core.info(
        `Approved CI run ${run.id} for returning contributor ${current.user.login}`,
      );
    }
    // A queued run already exists and needs no approval. Queue congestion must
    // not be reported as a missing CI run or consume the approval retry window.
    if (ci.length > 0) return;
    await sleep(10000);
  }
  throw new Error(
    "CI run did not appear within three minutes; rerun this approval workflow.",
  );
};
