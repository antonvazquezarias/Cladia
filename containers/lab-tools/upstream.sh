#!/bin/bash
# Pinned upstream analysis releases shared by native WSL and container installs.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl bzip2 perl \
    libgomp1 zlib1g libbz2-1.0 liblzma5 libdeflate0 libcurl4 libssl3 libsqlite3-0 \
    gcc make libc6-dev zlib1g-dev libbz2-dev liblzma-dev libdeflate-dev libcurl4-openssl-dev libssl-dev

work=$(mktemp -d /tmp/cladia-upstream.XXXXXX)
trap 'rm -rf "$work"' EXIT
fetch() {
    local url=$1 hash=$2 target=$3
    curl -fL --retry 3 "$url" -o "$target"
    echo "$hash  $target" | sha256sum -c -
}

if ! mafft --version 2>&1 | grep -q 'v7.526'; then
    fetch https://gitlab.com/sysimm/mafft/-/archive/v7.526/mafft-v7.526.tar.gz \
        2d81ba74ad6181df3c662b882f529734f3eb78af9b205d6840ca345612bf6410 "$work/mafft.tgz"
    mkdir "$work/mafft"
    tar -xzf "$work/mafft.tgz" -C "$work/mafft" --strip-components=1
    make -C "$work/mafft/core" -j2
    make -C "$work/mafft/core" install
fi

case $(uname -m) in
    x86_64)
        iq_arch=intel; iq_hash=d422cb2b8f04825faea753afda25c60de6611537d07ec8fcd0033e70cb042839
        blast_arch=x64; blast_hash=3888112d8207831aa47371d93583c601f058f88b5db22dc782438b039a3a411b ;;
    aarch64)
        iq_arch=arm; iq_hash=48fbbee1a5b5eb5b905d3bb9b88ed7651feaa83e9239f66559e3cc3a03455e37
        blast_arch=aarch64; blast_hash=cb9ac252a1ac8767d90b0bf0a38486f3cb94f71ef9b6b8d194ded19b30250daf ;;
    *) echo 'Unsupported analysis-tool architecture' >&2; exit 1 ;;
esac

if ! iqtree3 --version 2>&1 | grep -q 'version 3.1.4'; then
    fetch "https://github.com/iqtree/iqtree3/releases/download/v3.1.4/iqtree-3.1.4-Linux-${iq_arch}.tar.gz" \
        "$iq_hash" "$work/iqtree.tgz"
    mkdir "$work/iqtree"
    tar -xzf "$work/iqtree.tgz" -C "$work/iqtree" --strip-components=1
    install -m755 "$work/iqtree/bin/iqtree3" /usr/local/bin/iqtree3
    ln -sf iqtree3 /usr/local/bin/iqtree2
fi

if ! samtools --version 2>/dev/null | head -1 | grep -q '^samtools 1.25$'; then
    fetch https://github.com/samtools/samtools/releases/download/1.25/samtools-1.25.tar.bz2 \
        8bbd2b3cb447953149a74ab0b00e3ec4e88a789a27b8373a31fa6b5b2dee02b1 "$work/samtools.tar.bz2"
    mkdir "$work/samtools"
    tar -xjf "$work/samtools.tar.bz2" -C "$work/samtools" --strip-components=1
    # The release archive bundles matching HTSlib, linked into samtools.
    (cd "$work/samtools" && ./configure --prefix=/usr/local --without-curses && make -j2 && make install)
fi

if ! blastn -version 2>/dev/null | grep -q '^blastn: 2.17.0+'; then
    fetch "https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/2.17.0/ncbi-blast-2.17.0+-${blast_arch}-linux.tar.gz" \
        "$blast_hash" "$work/blast.tgz"
    mkdir "$work/blast"
    tar -xzf "$work/blast.tgz" -C "$work/blast" --strip-components=1
    cp -a "$work/blast/bin/." /usr/local/bin/
fi

mafft --version 2>&1 | grep 'v7.526'
iqtree3 --version | grep 'version 3.1.4'
samtools --version | grep '^samtools 1.25$'
samtools --version | grep '^Using htslib 1.25$'
blastn -version | grep '^blastn: 2.17.0+'
apt-get purge -y --auto-remove gcc make libc6-dev zlib1g-dev libbz2-dev liblzma-dev libdeflate-dev libcurl4-openssl-dev libssl-dev
apt-get clean
rm -rf /var/lib/apt/lists/*
