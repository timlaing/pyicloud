/** Resolve a successful Quality run back to its exact successful CI run. */
module.exports = async ({ github, context }) => {
  const quality = context.payload.workflow_run;
  if (
    quality.path !== ".github/workflows/sonar.yml" ||
    quality.conclusion !== "success" ||
    quality.event !== "workflow_run"
  )
    throw new Error("Expected a successful trusted Quality run.");
  const match = /^Quality · CI (\d+)$/.exec(quality.display_title);
  if (!match) throw new Error("Quality run has no CI run identity.");
  const { data: ci } = await github.rest.actions.getWorkflowRun({
    ...context.repo,
    run_id: Number(match[1]),
  });
  if (
    ci.path !== ".github/workflows/ci.yml" ||
    ci.conclusion !== "success" ||
    ci.repository.full_name !== `${context.repo.owner}/${context.repo.repo}`
  )
    throw new Error("Invalid source CI run.");
  return ci;
};

/** Require the tagged SHA's status to originate from successful main-push CI. */
module.exports.requireMainQuality = async ({ github, context, sha }) => {
  const statuses = await github.paginate(
    github.rest.repos.listCommitStatusesForRef,
    { ...context.repo, ref: sha, per_page: 100 },
  );
  const status = statuses.find(
    (item) =>
      item.context === "Quality" &&
      item.creator?.login === "github-actions[bot]",
  );
  if (status?.state !== "success")
    throw new Error(
      "Release commit must pass main Quality before publication.",
    );
  const prefix = `${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/`;
  const id = status.target_url?.startsWith(prefix)
    ? status.target_url.slice(prefix.length)
    : "";
  if (!/^\d+$/.test(id))
    throw new Error(
      "Quality status must link to a workflow run in this repository.",
    );
  const { data: quality } = await github.rest.actions.getWorkflowRun({
    ...context.repo,
    run_id: Number(id),
  });
  const ci = await module.exports({
    github,
    context: { repo: context.repo, payload: { workflow_run: quality } },
  });
  if (ci.event !== "push" || ci.head_branch !== "main" || ci.head_sha !== sha)
    throw new Error(
      "Release commit requires successful main-push Quality for the exact SHA.",
    );
};
