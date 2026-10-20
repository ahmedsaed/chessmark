# 0071. A release is cut by CI, from the changelog

**Status:** Accepted
**Date:** 2026-10-20

## Context

Releases were tagged and published by hand. 0.9.0 and 0.9.1 both reached `main` with their changelog
sections written, and no tag or GitHub release followed either. For a week the newest release on
GitHub said 0.8.0 while production ran 0.9.0 and `main` was on 0.9.1. Nothing could see the gap: CI
is green whether or not a person remembers to run `gh release create`.

There was also no way to name a release when deploying. Images were tagged `latest` and with a
commit SHA, so running a particular version, or going back to one, meant finding its merge commit.

## Decision

**A version on `main` that has no release gets one, from CI.** `.github/workflows/release.yml` runs
after CI succeeds on a push to `main`. If the tag `v<version>` does not exist, the workflow creates
it on the commit CI tested. It then publishes a GitHub release whose notes are that version's
`CHANGELOG.md` section (`scripts/release.py`). If the release exists, it does nothing.

**After CI, not on push.** A release names a commit as good, so it is only ever made from one whose
tests passed.

**The checks run before the merge.** The workflow can only publish what is written down, and it runs
after the merge, when a missing section can no longer be fixed in the same change.
`tests/docs/test_release.py` runs in `make check` and fails if `pyproject.toml` and `package.json`
state different versions, or if the current version has no dated changelog section.

**Release images are retagged, not rebuilt.** *Publish images* already pushed the commit's images
under its SHA. The release adds `:<version>` to those same images, so a release names exactly what
was tested. The tag is taken from the commit the release tag points at, never from the commit that
happens to be running the workflow.

**`./chessmark deploy v<version>` pins a release** by writing `CHESSMARK_VERSION` to `.env`, which
`docker-compose.prod.yml` reads into the image tags. `deploy` with no version removes the pin.

**Not pre-releases**, as `CHANGELOG.md` already says: 0.x means beta, and a repository whose every
release is a pre-release advertises no current version at all.

## Alternatives considered

- **Releasing on a tag push.** This still needs a person to remember to push the tag, which is the
  step that was forgotten.
- **A release tool such as release-please, which writes the changelog from commit messages.** This
  changelog is written prose with reasons in it. Generating it would replace the thing worth reading.
- **Rebuilding images for the release.** That would give two sets of bytes with one name, only one of
  which CI tested.
- **Pinning with an exported variable during `deploy`.** The next `restart` would fall back to
  `latest` without saying so.

## Consequences

- **Releasing is a pull request**: bump both versions and date the section. Merging it is the whole
  ceremony.
- **The `[Unreleased]` section has to be moved by hand** in the bump. A bump with no dated section
  fails `make check`, which is the point.
- **Releases from before this workflow have no image version tags.** v0.9.0 and v0.9.1 were published
  by hand when this landed, and their images are tagged by running the workflow by hand. Older
  releases are left as they are.
- **A version's release points at the first commit on `main` whose CI passed with that version.** If
  the bump's own CI fails, the release lands on the fix after it.
