/** Verify the actual validator run; the shared Actions bot identity is insufficient. */
async function notifierProof(github, context, pull, statuses) {
  const status = statuses
    .filter(item => item.context === "Review notifier policy" && item.creator?.login === "github-actions[bot]")
    .sort((left, right) => right.id - left.id)[0];
  if (status?.state !== "success") return false;
  const prefix = `${context.serverUrl || "https://github.com"}/${context.repo.owner}/${context.repo.repo}/actions/runs/`;
  if (!status.target_url?.startsWith(prefix)) return false;
  const runId = status.target_url.slice(prefix.length);
  if (!/^[1-9][0-9]*$/.test(runId)) return false;
  const [{ data: run }, { data: workflow }, { data: repository }] = await Promise.all([
    github.rest.actions.getWorkflowRun({ ...context.repo, run_id: runId }),
    github.rest.actions.getWorkflow({ ...context.repo, workflow_id: "review-notifier-policy.yml" }),
    github.rest.repos.get({ ...context.repo }),
  ]);
  if (run.workflow_id !== workflow.id || workflow.path !== ".github/workflows/review-notifier-policy.yml" ||
      run.status !== "completed" || run.conclusion !== "success" ||
      run.display_title !== `Review notifier policy / ${pull.head.sha}`) return false;
  if (run.event === "pull_request_target") return true;
  if (run.event !== "workflow_dispatch") return false;
  if (run.head_branch === repository.default_branch) return true;
  // Explicit owner-reviewed bootstrap/recovery: writer access alone never authorizes this.
  return run.actor?.login === context.repo.owner && run.head_sha === pull.head.sha;
}
const checkKey = check => `${check.app?.id ?? check.app?.slug ?? "unknown"}:${check.name}`;
const statusKey = status => `${status.creator?.id ?? status.creator?.login ?? "unknown"}:${status.context}`;
/** Gate CodeRabbit on CI + Sonar, and require its approval for the exact PR head. */
module.exports = async ({
  github,
  context,
  core,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}) => {
  const { owner, repo } = context.repo;
  const run = context.payload.workflow_run;
  const triggerSha = run.head_sha;
  const pullRequestEvent = ["pull_request", "pull_request_review"].includes(
    run.event,
  );
  const associated = await github.paginate(
    github.rest.repos.listPullRequestsAssociatedWithCommit,
    { owner, repo, commit_sha: triggerSha, per_page: 100 },
  );
  const candidates = associated.filter(
    (pr) =>
      pr.base.repo.full_name === `${owner}/${repo}` &&
      pr.base.ref === "main" &&
      (pullRequestEvent
        ? pr.state === "open" &&
          (pr.head.sha === triggerSha || pr.merge_commit_sha === triggerSha)
        : pr.merged_at && pr.merge_commit_sha === triggerSha),
  );
  if (candidates.length !== 1)
    throw new Error(
      "Expected exactly one current PR or merged PR for the validated commit; direct pushes are not reviewed.",
    );
  const { data: pr } = await github.rest.pulls.get({
    owner,
    repo,
    pull_number: candidates[0].number,
  });
  if (pr.head.sha !== candidates[0].head.sha)
    throw new Error("PR closed or changed during review.");
  const sha = pr.head.sha;
  const approved = async () => {
    const reviews = await github.paginate(github.rest.pulls.listReviews, {
      owner,
      repo,
      pull_number: pr.number,
      per_page: 100,
    });
    const latest = reviews
      .filter(
        (review) =>
          review.user?.login === "coderabbitai[bot]" &&
          review.commit_id === pr.head.sha &&
          review.state !== "COMMENTED",
      )
      .at(-1);
    return latest?.state === "APPROVED";
  };
  if (!pullRequestEvent) {
    if (!(await approved()))
      throw new Error(
        "The merged PR must have CodeRabbit approval on its final head commit.",
      );
    return;
  }
  if (pr.state !== "open" || pr.head.sha !== sha)
    throw new Error("PR closed or changed during review.");
  if (pr.draft)
    throw new Error("Draft PRs are not eligible for CodeRabbit review.");
  const validationPassed = async (current) => {
    const refs = [
      ...new Set([current.merge_commit_sha, current.head.sha].filter(Boolean)),
    ];
    const groups = await Promise.all(
      refs.map((ref) =>
        github.paginate(github.rest.checks.listForRef, {
          owner,
          repo,
          ref,
          filter: "latest",
          per_page: 100,
        }),
      ),
    );
    const mergeNames = new Set(groups[0].map(checkKey));
    const checks = [
      ...groups[0],
      ...(groups[1] || []).filter((check) => !mergeNames.has(checkKey(check))),
    ];
    const statuses = await github.paginate(
      github.rest.repos.listCommitStatusesForRef,
      {
        owner,
        repo,
        ref: current.head.sha,
        per_page: 100,
      },
    );
    const excluded = new Set([
      "Quality",
      "CodeRabbit",
      "CodeRabbit review",
      "Quality result",
      "Review passed",
      "Re-evaluate CodeRabbit approval",
    ]);
    const latestChecks = new Map();
    for (const check of checks.sort((left, right) => left.id - right.id))
      latestChecks.set(checkKey(check), check);
    const latestStatuses = new Map();
    for (const status of statuses.sort((left, right) => left.id - right.id))
      latestStatuses.set(statusKey(status), status);
    if (!(await notifierProof(github, context, current, statuses))) return false;
    const required = ["CI passed", "SonarCloud", "CodeQL", "Review notifier policy"];
    const passed = required.every((name) => {
      const expectedApp = ["CI passed", "Review notifier policy"].includes(name) ? "github-actions" : "github-advanced-security";
      const check = [...latestChecks.values()].find(item => item.name === name && item.app?.slug === expectedApp);
      if (name !== "Review notifier policy" && check) return check.status === "completed" && check.conclusion === "success";
      const status = [...latestStatuses.values()].find(item => item.context === name && item.creator?.login === expectedApp + "[bot]");
      return status?.state === "success";
    });
    return (
      passed &&
      [...latestChecks.values()].every(
        (check) =>
          excluded.has(check.name) ||
          (check.status === "completed" &&
            ["success", "skipped"].includes(check.conclusion)),
      ) &&
      [...latestStatuses.values()].every(
        (status) => excluded.has(status.context) || status.state === "success",
      )
    );
  };
  const failed = `<!-- coderabbit-request-failed:${sha} -->`;
  let request;
  let requested = false;
  const requestReview = async () => {
    const marker = `<!-- coderabbit-after-checks:${sha} -->`;
    const comments = await github.paginate(github.rest.issues.listComments, {
      owner,
      repo,
      issue_number: pr.number,
      per_page: 100,
    });
    const requests = comments.filter(
      (comment) =>
        comment.user?.login === "github-actions[bot]" &&
        comment.body?.includes(marker),
    );
    request = requests.at(-1);
    // A timed-out worker records failure. A cancelled worker's request expires.
    const retry =
      request &&
      (request.body.includes(failed) ||
        Date.now() - Date.parse(request.created_at) >= 40 * 60 * 1000);
    if (!request || retry) {
      if (requests.length >= 3)
        throw new Error(
          "Three CodeRabbit requests failed for this revision; request review manually.",
        );
      const { data: current } = await github.rest.pulls.get({
        owner,
        repo,
        pull_number: pr.number,
      });
      if (current.state !== "open" || current.draft || current.head.sha !== sha || current.base.ref !== "main" || current.base.repo.full_name !== `${owner}/${repo}`)
        throw new Error("PR closed or changed during review.");
      const { data } = await github.rest.issues.createComment({
        owner,
        repo,
        issue_number: pr.number,
        body: `@coderabbitai review\n\n${marker}`,
      });
      request = data;
    }
    requested = true;
  };
  // Bounded waiting replaces the previous event/cooldown race. New commits invalidate this run.
  for (let attempt = 0; attempt < 40; attempt++) {
    const { data: current } = await github.rest.pulls.get({
      owner,
      repo,
      pull_number: pr.number,
    });
    if (current.state !== "open" || current.draft || current.head.sha !== sha || current.base.ref !== "main" || current.base.repo.full_name !== `${owner}/${repo}`)
      throw new Error("PR closed or changed during review.");
    if (await validationPassed(current)) {
      if (await approved()) return;
      if (!requested) await requestReview();
    }
    core.info(`Waiting for CodeRabbit approval of PR #${pr.number} at ${sha}`);
    await sleep(60000);
  }
  if (request?.id) {
    await github.rest.issues.updateComment({
      owner,
      repo,
      comment_id: request.id,
      body: `${request.body}\n${failed}`,
    });
  }
  throw new Error(
    "CodeRabbit approval did not arrive within 40 minutes; rerun Quality after resolving review findings.",
  );
};

module.exports.notifierProof = notifierProof;
