#!/usr/bin/env bash
# Register THIS machine as a self-hosted GitHub Actions runner for the eval repo.
#
# The only required secret is RUNNER_TOKEN (the registration --token), short-lived
# (~1h). Grab a fresh one from:
#   <repo> -> Settings -> Actions -> Runners -> New self-hosted runner (macOS).
# It is passed at runtime and NEVER stored in this file.
#
# Usage (one token, stable everything else):
#   RUNNER_TOKEN="<registration token>" ./scripts/setup_self_hosted_runner.sh
#
# The runner package is fetched from the public actions/runner release (no auth) — the
# binary is identical to the one your Enterprise instance serves. For locked-down orgs
# that require the instance package, set RUNNER_PKG_URL + RUNNER_PKG_BEARER (the curl
# URL and the Bearer *token value only*, without the "Authorization: Bearer " prefix).
#
# After it configures, start the runner with:
#   (cd "$RUNNER_DIR" && ./run.sh)                            # foreground
#   (cd "$RUNNER_DIR" && ./svc.sh install && ./svc.sh start)  # background service

set -euo pipefail

RUNNER_URL="${RUNNER_URL:-https://github.com/jmhuer/HiAgentResearch}"
: "${RUNNER_TOKEN:?set RUNNER_TOKEN to the registration --token value (expires ~1h)}"

# Defaults (override via env). Arch is auto-detected.
RUNNER_VERSION="${RUNNER_VERSION:-2.328.0}"
case "$(uname -m)" in
  arm64)  DETECTED_ARCH="osx-arm64" ;;
  *)      DETECTED_ARCH="osx-x64" ;;
esac
RUNNER_ARCH="${RUNNER_ARCH:-$DETECTED_ARCH}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,macos}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
# Default: public release (no auth). Optional instance override for locked-down orgs.
RUNNER_PKG_URL="${RUNNER_PKG_URL:-https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz}"
RUNNER_PKG_BEARER="${RUNNER_PKG_BEARER:-}"

printf '==> Runner dir: %s\n==> Version: %s  Arch: %s  Labels: %s\n==> Repo: %s\n' \
  "$RUNNER_DIR" "$RUNNER_VERSION" "$RUNNER_ARCH" "$RUNNER_LABELS" "$RUNNER_URL"

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"
tarball="actions-runner-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"

if [[ ! -x ./config.sh ]]; then
  if [[ ! -f "$tarball" ]]; then
    echo "==> Downloading runner package from: $RUNNER_PKG_URL"
    if [[ -n "$RUNNER_PKG_BEARER" ]]; then
      curl -fL -H "Authorization: Bearer ${RUNNER_PKG_BEARER}" -o "$tarball" "$RUNNER_PKG_URL"
    else
      curl -fL -o "$tarball" "$RUNNER_PKG_URL"
    fi
  fi
  echo "==> Extracting $tarball"
  tar xzf "./$tarball"
fi

# --unattended: no prompts; --replace: re-register cleanly if a runner with this name exists.
echo "==> Configuring runner against $RUNNER_URL"
./config.sh --unattended --url "$RUNNER_URL" --token "$RUNNER_TOKEN" --labels "$RUNNER_LABELS" --replace

# actions/setup-python's prebuilt macOS Python hardcodes its install prefix to
# /Users/runner/hostedtoolcache (the hosted-runner user). On a self-hosted Mac with a
# different user that mkdir fails ("Permission denied"), so create it once. Skipped if
# it already exists or a real `runner` user owns it.
if [[ "$(uname)" == "Darwin" && ! -d /Users/runner/hostedtoolcache ]]; then
  echo "==> Preparing setup-python tool cache at /Users/runner/hostedtoolcache (needs sudo once)"
  sudo mkdir -p /Users/runner/hostedtoolcache
  sudo chown -R "$(whoami)" /Users/runner
fi

cat <<EOF

==> Runner configured.
    Start (foreground):  (cd "$RUNNER_DIR" && ./run.sh)
    Or as a service:     (cd "$RUNNER_DIR" && ./svc.sh install && ./svc.sh start)

    Then set the repo's runner label so workflows target it:
      gh variable set HIAGENTRESEARCH_RUNNER --body self-hosted \\
        --repo jmhuer/HiAgentResearch
    (or Settings -> Secrets and variables -> Actions -> Variables)
EOF
