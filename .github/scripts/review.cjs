/** Gate CodeRabbit on CI + Sonar, and require its approval for the exact PR head. */
module.exports = async ({
  github,
  context,
  core,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}) => {
  const { owner, repo } = context.repo;
  const run = context.payload.workflow_run;
  const sha = run.head_sha;
  let associated = await github.paginate(
    github.rest.repos.listPullRequestsAssociatedWithCommit,
    { owner, repo, commit_sha: sha, per_page: 100 },
  );
  if (run.event === "pull_request" && !associated.some((pr) => pr.head?.sha === sha)) {
    // Fork commits are not in the base repository, so the associated-commit
    // API cannot resolve them. Match the open PR head directly instead.
    const open = await github.paginate(github.rest.pulls.list, {
      owner,
      repo,
      state: "open",
      base: "main",
      per_page: 100,
    });
    associated = [
      ...associated,
      ...open.filter((pr) => pr.head?.sha === sha && !associated.some((a) => a.number === pr.number)),
    ];
  }
  const candidates = associated.filter(
    (pr) =>
      pr.base.repo.full_name === `${owner}/${repo}` &&
      pr.base.ref === "main" &&
      (run.event === "pull_request"
        ? pr.state === "open" && pr.head.sha === sha
        : pr.merged_at && pr.merge_commit_sha === sha),
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
          review.commit_id === pr.head.sha,
      )
      .at(-1);
    return latest?.state === "APPROVED";
  };
  if (run.event !== "pull_request") {
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
  if (await approved()) return;
  const marker = `<!-- coderabbit-after-checks:${sha} -->`;
  const failed = `<!-- coderabbit-request-failed:${sha} -->`;
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
  let request = requests.at(-1);
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
    if (current.state !== "open" || current.head.sha !== sha)
      throw new Error("PR closed or changed during review.");
    const { data } = await github.rest.issues.createComment({
      owner,
      repo,
      issue_number: pr.number,
      body: `@coderabbitai review\n\n${marker}`,
    });
    request = data;
  }
  // Bounded waiting replaces the previous event/cooldown race. New commits invalidate this run.
  for (let attempt = 0; attempt < 120; attempt++) {
    const { data: current } = await github.rest.pulls.get({
      owner,
      repo,
      pull_number: pr.number,
    });
    if (current.state !== "open" || current.head.sha !== sha)
      throw new Error("PR closed or changed during review.");
    if (await approved()) return;
    core.info(`Waiting for CodeRabbit approval of PR #${pr.number} at ${sha}`);
    await sleep(20000);
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
