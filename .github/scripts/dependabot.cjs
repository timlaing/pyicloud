/** Approve the validated Dependabot revision and let native auto-merge enforce rules. */
const qualitySource = require("./quality-source.cjs");
module.exports = async ({ github, context, core }) => {
  const ci = await qualitySource({ github, context });
  if (ci.event !== "pull_request") return;
  const { owner, repo } = context.repo;
  const prs = await github.paginate(
    github.rest.repos.listPullRequestsAssociatedWithCommit,
    { owner, repo, commit_sha: ci.head_sha, per_page: 100 },
  );
  for (const item of prs) {
    const { data: pr } = await github.rest.pulls.get({
      owner,
      repo,
      pull_number: item.number,
    });
    if (
      pr.state !== "open" ||
      pr.draft ||
      pr.user.login !== "dependabot[bot]" ||
      pr.head.repo.full_name !== `${owner}/${repo}` ||
      pr.base.ref !== "main" ||
      pr.head.sha !== ci.head_sha
    )
      continue;
    await github.rest.pulls.createReview({
      owner,
      repo,
      pull_number: pr.number,
      commit_id: ci.head_sha,
      event: "APPROVE",
    });
    const { data: current } = await github.rest.pulls.get({
      owner,
      repo,
      pull_number: pr.number,
    });
    if (current.head.sha !== ci.head_sha || current.state !== "open") continue;
    if (current.mergeable_state === "clean") {
      const { data: merged } = await github.rest.pulls.merge({
        owner,
        repo,
        pull_number: pr.number,
        sha: ci.head_sha,
        merge_method: "squash",
      });
      if (!merged.merged)
        throw new Error(`GitHub withheld merge for PR #${pr.number}`);
      continue;
    }
    if (current.auto_merge) continue;
    // expectedHeadOid closes the race if Dependabot updates the PR while approving.
    await github.graphql(
      `mutation($id: ID!, $sha: GitObjectID!) {
      enablePullRequestAutoMerge(input: {pullRequestId: $id, expectedHeadOid: $sha, mergeMethod: SQUASH}) {
        pullRequest { number }
      }
    }`,
      { id: pr.node_id, sha: ci.head_sha },
    );
    core.info(
      `Approved and enabled auto-merge for Dependabot PR #${pr.number}`,
    );
  }
};
