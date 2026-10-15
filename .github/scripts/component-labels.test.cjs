const assert = require("node:assert/strict");
const { test } = require("node:test");
const label = require("./component-labels.cjs");
const { classify } = label;

test("explicit issue selection overrides incidental log keywords", () => {
  assert.deepEqual(
    classify({
      issue: true,
      body: "### Service / component\n\nservice: reminders, component: cli\n\n### Logs\nphotos notes",
    }),
    ["service: reminders", "component: cli"],
  );
});

test("direct reports and Unknown selection fall back to bounded keywords", () => {
  assert.deepEqual(classify({ title: "Find my iPhone login failed" }), [
    "service: findmyiphone",
    "component: authentication",
  ]);
  assert.deepEqual(
    classify({
      issue: true,
      body: "### Service / component\n\nUnknown\n\nHide My Email fails",
    }),
    ["service: hidemyemail"],
  );
  assert.deepEqual(classify({ title: "noteworthy photographs" }), []);
});

test("source, tests and documentation classify multiple services", () => {
  assert.deepEqual(
    classify({
      files: [
        "pyicloud/services/photos_cloudkit/service.py",
        "tests/test_reminders_crdt_edit.py",
        "docs/services/devices.md",
      ],
    }),
    [
      "service: findmyiphone",
      "service: photos",
      "service: reminders",
      "component: documentation",
    ],
  );
});

test("PR API pagination and rename paths add labels without replacing existing ones", async () => {
  const calls = [];
  const listFiles = () => {};
  const github = {
    paginate: async (method, args) => {
      assert.equal(method, listFiles);
      assert.equal(args.per_page, 100);
      return [
        {
          filename: "docs/new.md",
          previous_filename: "pyicloud/services/notes/_reads.py",
        },
      ];
    },
    rest: {
      pulls: { listFiles },
      issues: { addLabels: async (args) => calls.push(args) },
    },
  };
  await label({
    github,
    context: {
      repo: { owner: "owner", repo: "repo" },
      payload: { pull_request: { number: 7 }, action: "synchronize" },
    },
  });
  assert.deepEqual(calls[0].labels, [
    "service: notes",
    "component: documentation",
  ]);
  assert.equal(calls[0].issue_number, 7);
});

test("triage is added on open/reopen, never re-added on edits", async () => {
  for (const action of ["opened", "reopened", "edited"]) {
    const calls = [];
    await label({
      github: {
        rest: { issues: { addLabels: async (args) => calls.push(args) } },
      },
      context: { repo: {}, payload: { issue: { number: 1 }, action } },
    });
    assert.deepEqual(
      calls.map((call) => call.labels),
      action === "edited" ? [] : [["needs-triage"]],
    );
  }
});

test("every service maps source, test and documentation paths", () => {
  const rules = require("../component-labels.json");
  for (const rule of rules.filter((entry) =>
    entry.label.startsWith("service:"),
  )) {
    const name = rule.label.slice("service: ".length);
    for (const file of [
      `pyicloud/services/${name}.py`,
      `pyicloud/services/${name}/_reads.py`,
      `tests/services/test_${name}.py`,
      `tests/fixtures/${name}/response.json`,
      `docs/services/${name === "findmyiphone" ? "devices" : name}.md`,
    ]) {
      assert.ok(classify({ files: [file] }).includes(rule.label), file);
    }
  }
});
