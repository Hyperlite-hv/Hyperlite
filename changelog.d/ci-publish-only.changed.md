- The CI only publishes: a push to `test` or `master` builds and publishes the package, the ISO and the APT
  repository, and nothing runs on a pull request. The tests, the VM lab and the workflow checks are run by hand
  (CONTRIBUTING.md); the branches no longer require them to merge.
