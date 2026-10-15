const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const label = require("./component-labels.cjs");
const { classify } = label;

const root = path.resolve(__dirname, "..", "..");

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

test("every service rule points at files that exist in the repository", () => {
  const rules = require("../component-labels.json");
  for (const rule of rules.filter((entry) =>
    entry.label.startsWith("service:"),
  )) {
    const name = rule.label.slice("service: ".length);
    const source = `pyicloud/services/${name}`;
    const docs = "docs/services/";
    const position = rule.paths.indexOf(docs);
    assert.notEqual(position, -1, `${rule.label} must document a docs page`);
    const page = rule.paths.slice(position + docs.length).match(/[a-z]+/);
    assert.ok(page, rule.label);
    assert.ok(
      fs.existsSync(path.join(root, `${source}.py`)) ||
        fs.existsSync(path.join(root, source)),
      `${rule.label} source`,
    );
    assert.ok(
      fs.existsSync(path.join(root, `tests/services/test_${name}.py`)) ||
        fs.existsSync(path.join(root, `tests/test_${name}.py`)) ||
        fs.existsSync(path.join(root, `tests/test_cov_${name}.py`)),
      `${rule.label} tests`,
    );
    assert.ok(
      fs.existsSync(path.join(root, `${docs}${page[0]}.md`)),
      `${rule.label} docs`,
    );
  }
});

test("null issue bodies preserve title classification and triage", async () => {
  for (const action of ["opened", "reopened"]) {
    const calls = [];
    await label({
      github: {
        rest: { issues: { addLabels: async (args) => calls.push(args) } },
      },
      context: {
        repo: { owner: "owner", repo: "repo" },
        payload: {
          issue: { number: 8, title: "Reminders fail", body: null },
          action,
        },
      },
    });
    assert.deepEqual(calls[0].labels, ["service: reminders", "needs-triage"]);
    assert.equal(calls[0].issue_number, 8);
  }
  assert.deepEqual(classify({ issue: true, body: null }), []);
});

test("issue edits never relabel, so labels maintainers removed stay removed", async () => {
  const calls = [];
  await label({
    github: {
      rest: { issues: { addLabels: async (args) => calls.push(args) } },
    },
    context: {
      repo: { owner: "owner", repo: "repo" },
      payload: {
        issue: { number: 4, title: "Reminders fail", body: null },
        action: "edited",
      },
    },
  });
  assert.deepEqual(calls, []);
});

test("path rules classify the real files they are configured for", () => {
  const cases = {
    "tests/test_srp_password.py": ["component: authentication"],
    "tests/test_hsa2_bridge.py": ["component: authentication"],
    "tests/test_cov_state_cookiejar.py": ["component: authentication"],
    "tests/test_base.py": ["component: authentication"],
    "tests/test_utils.py": ["component: core"],
    "tests/test_exception_hierarchy.py": ["component: core"],
    "tests/test_https_service_endpoints.py": ["component: core"],
    "tests/test_output.py": ["component: cli"],
    "tests/test_example_reminders_delta.py": ["service: reminders"],
    "tests/test_cov_notes_ck_base.py": ["service: notes"],
    "docs/services/drive.md": [
      "service: drive",
      "service: ubiquity",
      "component: documentation",
    ],
  };
  for (const [file, expected] of Object.entries(cases)) {
    assert.deepEqual(classify({ files: [file] }), expected, file);
  }
});
