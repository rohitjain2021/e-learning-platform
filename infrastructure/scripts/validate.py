"""Offline YAML validation; safe to run locally and on pull requests."""

from pathlib import Path

import yaml
from cfnlint.api import lint

from deploy import ARTIFACT_DIRECTORY, REGION, build_template


# Global project location: validation works regardless of the caller's current directory.
INFRASTRUCTURE_DIRECTORY = Path(__file__).resolve().parents[1]


class UniqueKeyLoader(yaml.SafeLoader):
    """Read YAML safely while detecting repeated settings that could silently override each other."""
    pass


def unique_mapping(loader, node, deep=False):
    """Reject duplicate YAML keys so a repeated value cannot hide an earlier setting."""
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


# Apply the duplicate-key check whenever a YAML mapping is read by this validator.
UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def main():
    """Check source YAML and complete CloudFormation templates without deploying anything."""
    # Check every authored infrastructure and workflow file for basic YAML mistakes.
    files = [INFRASTRUCTURE_DIRECTORY / "serverless.yml"]
    files += sorted((INFRASTRUCTURE_DIRECTORY / "resources").glob("*.yml"))
    files += sorted((INFRASTRUCTURE_DIRECTORY / "bootstrap").glob("*.yml"))
    files += sorted((INFRASTRUCTURE_DIRECTORY.parent / ".github" / "workflows").glob("*.yml"))
    for path in files:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        if not isinstance(document, dict):
            raise ValueError(f"Expected a YAML mapping: {path}")
        print(f"Validated {path.relative_to(INFRASTRUCTURE_DIRECTORY.parent)}")
    # Cross-file parameters and resources are visible only after composition.
    template = yaml.safe_dump(build_template(), sort_keys=False)
    ARTIFACT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIRECTORY / "cloudformation-template-update-stack.yml"
    path.write_text(template, encoding="utf-8")
    # Check the application as one complete template and the bootstrap as its own stack.
    templates = [(path, template)] + [
        (path, path.read_text(encoding="utf-8"))
        for path in sorted((INFRASTRUCTURE_DIRECTORY / "bootstrap").glob("*.yml"))]
    # Collect actionable file locations and messages; CI stops if any full-template check fails.
    failures = []
    for path, body in templates:
        matches = lint(body, config={"regions": [REGION]})
        failures.extend(f"{path}:{m.linenumber}:{m.columnnumber}: {m.rule.id} {m.message}" for m in matches)
        if not matches:
            print(f"CloudFormation lint passed: {path.relative_to(INFRASTRUCTURE_DIRECTORY.parent)}")
    if failures:
        raise ValueError("CloudFormation lint failed:\n" + "\n".join(failures))


if __name__ == "__main__":
    main()
