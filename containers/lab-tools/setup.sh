#!/bin/bash
# Cladia's tools, installed in its own Linux (Cladia-tools, Debian bookworm) by
# linux.ensure: the same programs as the Dockerfile beside this file. Runs again
# when config.TOOLS_VERSION changes, so it must be safe to repeat.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

FASTTREE_TAG=v2.2.0
TABLE2ASN_TOOLS_TAG=0.6
MINIMAP2_COMMIT=3c28777e7e2dcc90f825de1b9f17a89cca7d4452
FASTTREE_COMMIT=29c5e62fbcd93230ee325f9c6a17b81f00e3c72a

apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates util-linux procps \
    libgomp1 zlib1g progressivemauve primer3

# Build the pinned upstream release: bookworm's minimap2 is older. The default
# make target dispatches SSE2/SSE4 on x86; use upstream's NEON flags on ARM64.
if [ "$(cat /usr/local/share/minimap2-commit 2>/dev/null)" != "$MINIMAP2_COMMIT" ] || [ "$(minimap2 --version 2>/dev/null || true)" != "2.31-r1302" ]; then
  apt-get install -y --no-install-recommends gcc git make libc6-dev zlib1g-dev
  source_dir=$(mktemp -d /tmp/cladia-minimap2.XXXXXX)
  git clone --depth 1 --branch v2.31 https://github.com/lh3/minimap2.git "$source_dir"
  test "$(git -C "$source_dir" rev-parse HEAD)" = "$MINIMAP2_COMMIT"
  if [ "$(uname -m)" = aarch64 ]; then
    make -C "$source_dir" -j2 arm_neon=1 aarch64=1
  else
    make -C "$source_dir" -j2
  fi
  install -m 755 "$source_dir/minimap2" /usr/local/bin/minimap2
  test "$(/usr/local/bin/minimap2 --version)" = "2.31-r1302"
  echo "$MINIMAP2_COMMIT" > /usr/local/share/minimap2-commit
  rm -rf "$source_dir"
  apt-get purge -y --auto-remove gcc git make libc6-dev zlib1g-dev
fi

# FastTree 2.2 from upstream (Debian's is older); built here, so -march=native
# suits this PC. Double precision by default, as upstream.
if [ "$(cat /usr/local/share/fasttree-commit 2>/dev/null)" != "$FASTTREE_COMMIT" ]; then
  apt-get install -y --no-install-recommends gcc git libc6-dev
  rm -rf /tmp/fasttree
  git clone --depth 1 --branch "$FASTTREE_TAG" https://github.com/morgannprice/fasttree.git /tmp/fasttree
  test "$(git -C /tmp/fasttree rev-parse HEAD)" = "$FASTTREE_COMMIT"
  gcc -O3 -fopenmp-simd -funsafe-math-optimizations -march=native \
      -o /usr/local/bin/FastTree /tmp/fasttree/FastTree.c -lm
  gcc -DOPENMP -O3 -fopenmp -fopenmp-simd -funsafe-math-optimizations -march=native \
      -o /usr/local/bin/FastTreeMP /tmp/fasttree/FastTree.c -lm
  ln -sf FastTree /usr/local/bin/fasttree
  ln -sf FastTreeMP /usr/local/bin/fasttreeMP
  echo "$FASTTREE_COMMIT" > /usr/local/share/fasttree-commit
  rm -rf /tmp/fasttree
  apt-get purge -y --auto-remove gcc git libc6-dev
fi

# NCBI's table2asn (validates a submission; NCBI publishes only the current build,
# so it is fetched when missing and kept; bump TABLE2ASN_TOOLS_TAG to refresh it)
if ! command -v table2asn >/dev/null || [ "$(cat /usr/local/share/table2asn-tools 2>/dev/null)" != "$TABLE2ASN_TOOLS_TAG" ]; then
  apt-get install -y --no-install-recommends curl
  curl -fsSL https://ftp.ncbi.nlm.nih.gov/asn1-converters/by_program/table2asn/linux64.table2asn.gz | gunzip > /usr/local/bin/table2asn
  chmod +x /usr/local/bin/table2asn
  echo "$TABLE2ASN_TOOLS_TAG" > /usr/local/share/table2asn-tools
  apt-get purge -y --auto-remove curl
fi

apt-get clean
rm -rf /var/lib/apt/lists/*
mkdir -p /data /var/lib/cladia/volumes
echo "Cladia tools ready"
