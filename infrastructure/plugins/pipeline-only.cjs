"use strict";

// Guard against accidental direct infrastructure mutations from developer machines.
const MUTATING_COMMANDS = new Set(["deploy", "remove", "rollback"]);
const EXPECTED_REPOSITORY = "rohitjain2021/e-learning-platform";
const EXPECTED_REF = "refs/heads/main";

class PipelineOnly {
  constructor(serverless) {
    this.hooks = {
      initialize: () => {
        const command = serverless.processedInput.commands[0];
        if (!MUTATING_COMMANDS.has(command)) return;
        if (
          process.env.GITHUB_ACTIONS !== "true" ||
          process.env.GITHUB_REPOSITORY !== EXPECTED_REPOSITORY ||
          process.env.GITHUB_REF !== EXPECTED_REF
        ) {
          throw new Error(
            "AWS deployment, removal, and rollback must run through this repository's main-branch GitHub Actions pipeline."
          );
        }
      },
    };
  }
}

module.exports = PipelineOnly;
