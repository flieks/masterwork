#!/usr/bin/env bash
# Print the Homebrew formula for a released tag, with the tarball's sha256 filled in.
# Usage: scripts/brew-formula.sh v0.2.1 > Formula/masterwork.rb   (in the flieks/homebrew-masterwork tap)
set -euo pipefail
tag="${1:?usage: brew-formula.sh <tag>}"
version="${tag#v}"
url="https://github.com/flieks/masterwork/archive/refs/tags/${tag}.tar.gz"
sha="$(curl -sL "$url" | shasum -a 256 | cut -d' ' -f1)"
cat <<RUBY
class Masterwork < Formula
  desc "Workbench for the skills and subagents your AI coding agents use, with scored simulations"
  homepage "https://masterwork-site.vercel.app"
  url "${url}"
  sha256 "${sha}"
  license "Elastic-2.0"

  depends_on "node"
  depends_on "uv"

  def install
    libexec.install Dir["*"]
    # The launcher installs its own backend and frontend dependencies on first run.
    (bin/"masterwork").write <<~SH
      #!/bin/bash
      exec "#{Formula["node"].opt_bin}/node" "#{libexec}/bin/masterwork.mjs" "\$@"
    SH
  end

  test do
    # The launcher has no --help; starting servers is not a brew test.
    assert_predicate libexec/"bin/masterwork.mjs", :exist?
  end
end
RUBY
