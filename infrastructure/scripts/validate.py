"""Offline YAML validation; safe to run locally and on pull requests."""

from pathlib import Path

import yaml


INFRASTRUCTURE_DIRECTORY = Path(__file__).resolve().parents[1]


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def main():
    files = [INFRASTRUCTURE_DIRECTORY / "serverless.yml"]
    files += sorted((INFRASTRUCTURE_DIRECTORY / "resources").glob("*.yml"))
    files += sorted((INFRASTRUCTURE_DIRECTORY / "bootstrap").glob("*.yml"))
    files += sorted((INFRASTRUCTURE_DIRECTORY.parent / ".github" / "workflows").glob("*.yml"))
    for path in files:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        if not isinstance(document, dict):
            raise ValueError(f"Expected a YAML mapping: {path}")
        print(f"Validated {path.relative_to(INFRASTRUCTURE_DIRECTORY.parent)}")


if __name__ == "__main__":
    main()
