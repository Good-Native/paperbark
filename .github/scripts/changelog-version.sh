#!/usr/bin/env bash
# changelog-version.sh — shared changelog parsing and version calculation.
#
# Outputs (via $GITHUB_OUTPUT or stdout):
#   release_type      — major, minor, or patch
#   should_release    — true if [Unreleased] has content
#   changelog_content — the unreleased content (heredoc-safe)
#   current_version   — current git tag (e.g. v0.1.3)
#   next_version      — bumped version (e.g. v0.1.4)
#
# Usage:
#   bash .github/scripts/changelog-version.sh
#
# Ported from Good-Native/hover with the CLI-tag split removed (paperbark
# is a single PyPI package).

set -euo pipefail

if ! grep -q "^## \[Unreleased" CHANGELOG.md; then
  echo "should_release=false" >> "${GITHUB_OUTPUT:-/dev/null}"
  echo "No [Unreleased] section found — skipping release"
  if [ -z "${GITHUB_OUTPUT:-}" ]; then
    echo "should_release=false"
  fi
  exit 0
fi

CHANGELOG_HEADER=$(grep "^## \[Unreleased" CHANGELOG.md | head -1)

if echo "$CHANGELOG_HEADER" | grep -qi "\[Unreleased:major\]"; then
  RELEASE_TYPE="major"
elif echo "$CHANGELOG_HEADER" | grep -qi "\[Unreleased:minor\]"; then
  RELEASE_TYPE="minor"
else
  RELEASE_TYPE="patch"
fi

UNRELEASED_CONTENT=$(awk '
/^## \[Unreleased/ {flag=1; next}
/^## Full changelog history$/ {flag=0}
/^## \[[0-9]/ {flag=0}
flag
' CHANGELOG.md)
UNRELEASED_CONTENT=$(printf "%s\n" "$UNRELEASED_CONTENT" | sed '/^_Add unreleased changes here\._$/d')

if [ -z "$(echo "$UNRELEASED_CONTENT" | grep -v '^[[:space:]]*$')" ]; then
  SHOULD_RELEASE="false"
else
  SHOULD_RELEASE="true"
fi

CURRENT_TAG=$(git tag -l 'v[0-9]*' --sort=-version:refname | head -1)
CURRENT_TAG=${CURRENT_TAG:-v0.0.0}
CURRENT_VERSION=${CURRENT_TAG#v}
CURRENT_VERSION=${CURRENT_VERSION%%-*}

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

if [ "$RELEASE_TYPE" = "major" ]; then
  MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0
elif [ "$RELEASE_TYPE" = "minor" ]; then
  MINOR=$((MINOR + 1)); PATCH=0
else
  PATCH=$((PATCH + 1))
fi

NEXT_VERSION="v${MAJOR}.${MINOR}.${PATCH}"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "release_type=$RELEASE_TYPE"
    echo "should_release=$SHOULD_RELEASE"
    echo "current_version=$CURRENT_TAG"
    echo "next_version=$NEXT_VERSION"
  } >> "$GITHUB_OUTPUT"

  if [ "$SHOULD_RELEASE" = "true" ]; then
    # Per-invocation delimiter so a literal "CHANGELOG_EOF" in the
    # unreleased content can't terminate the heredoc early and corrupt
    # GITHUB_OUTPUT parsing.
    DELIM="ghadelim_$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
    {
      echo "changelog_content<<$DELIM"
      echo "$UNRELEASED_CONTENT"
      echo "$DELIM"
    } >> "$GITHUB_OUTPUT"
  fi
else
  echo "release_type=$RELEASE_TYPE"
  echo "should_release=$SHOULD_RELEASE"
  echo "current_version=$CURRENT_TAG"
  echo "next_version=$NEXT_VERSION"
fi
