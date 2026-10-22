# Changelog fragments

A pull request does not edit `CHANGELOG.md`: it adds a file here, so two pull requests never touch the same file and
never conflict over the changelog. The release pull request turns the fragments into the version's section of
`CHANGELOG.md` and removes them.

Name a fragment `<issue>-<slug>.<type>.md`, where `type` is one of `added`, `changed`, `deprecated`, `removed`,
`fixed` or `security`:

```text
changelog.d/440-damaged-database.added.md
changelog.d/440-claim-content-type.fixed.md
```

Write one or more bullets, as they will read in `CHANGELOG.md`, with no heading: the release writes the version's
and the type's.

```markdown
- A damaged database is detected and said (#440). ...
```

```bash
python3 scripts/changelog.py check          # the fragments are well formed (the test suite runs it too)
python3 scripts/changelog.py unreleased     # what the next version's section will read
python3 scripts/changelog.py release 1.1.4  # in the release pull request only
```
