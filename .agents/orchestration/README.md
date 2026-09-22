# Orchestration template scaffold

`manifest.yaml` intentionally uses JSON-compatible YAML so the offline validator has no third-party dependency.

Run the scaffold checks:

```sh
python3 templates/orchestration/v1/scripts/validate.py --schema-smoke-test
python3 templates/orchestration/v1/scripts/validate.py --package
python3 -m unittest templates/orchestration/v1/tests/test_validate.py
python3 scripts/sync-orchestration-package.py --check
```

`sync-orchestration-package.py` is read-only by default. Use its explicit
`--write` flag to mirror this canonical package into
`ageroot_template/.agents/orchestration/`; it preserves any unexpected target
files and reports them as drift.

In a consumer repository, validate the portable package with:

```sh
python3 .agents/orchestration/scripts/validate.py --package
```
