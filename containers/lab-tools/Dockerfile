# Shared, version-pinned installation for Cladia's WSL and container runners.
# Build: wslc build -t cladia-tools:0.8 .
FROM debian:bookworm-slim
COPY setup.sh /tmp/cladia-install/setup.sh
RUN bash /tmp/cladia-install/setup.sh
COPY upstream.sh /tmp/cladia-install/upstream.sh
RUN bash /tmp/cladia-install/upstream.sh && rm -rf /tmp/cladia-install
WORKDIR /data
