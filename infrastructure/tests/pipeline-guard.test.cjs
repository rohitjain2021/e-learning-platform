"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const PipelineOnly = require("../plugins/pipeline-only.cjs");
// Save the process settings so each simulated workflow starts and finishes independently.
const ORIGINAL_ENV = { ...process.env };
const GUARDED_ENV_KEYS = ["GITHUB_ACTIONS", "GITHUB_REPOSITORY", "GITHUB_REF"];

// Simulate a local or GitHub run and always restore the caller's environment afterward.
function check(command, environment) {
  for (const name of GUARDED_ENV_KEYS) delete process.env[name];
  Object.assign(process.env, environment);
  try {
    new PipelineOnly({ processedInput: { commands: [command] } }).hooks.initialize();
  } finally {
    for (const name of GUARDED_ENV_KEYS) {
      if (ORIGINAL_ENV[name] === undefined) delete process.env[name];
      else process.env[name] = ORIGINAL_ENV[name];
    }
  }
}

test("local infrastructure mutations are blocked", () => {
  for (const command of ["deploy", "remove", "rollback"]) {
    assert.throws(() => check(command, {}), /GitHub Actions pipeline/);
  }
});

test("local packaging and configuration inspection remain available", () => {
  for (const command of ["package", "print"]) assert.doesNotThrow(() => check(command, {}));
});

test("pull-request refs cannot deploy", () => {
  assert.throws(() => check("deploy", {
    GITHUB_ACTIONS: "true",
    GITHUB_REPOSITORY: "rohitjain2021/e-learning-platform",
    GITHUB_REF: "refs/pull/1/merge",
  }));
});

test("trusted repository main-branch workflow can deploy", () => {
  assert.doesNotThrow(() => check("deploy", {
    GITHUB_ACTIONS: "true",
    GITHUB_REPOSITORY: "rohitjain2021/e-learning-platform",
    GITHUB_REF: "refs/heads/main",
  }));
});
