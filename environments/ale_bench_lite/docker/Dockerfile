#
# ALE-Bench Lite worker image — agent container for ale_bench_lite tasks.
#
# Mirrors the toolchains in ALE-Bench/dockerfiles exactly:
#   Python:  Dockerfile_python_202301_base  (CPython 3.11.4 + pinned libs)
#   Rust:    Dockerfile_rust_202301_base    (rustc 1.70.0 + cached crates)
#   C++20:   Dockerfile_cpp20_202301_base   (ACL, Boost 1.82.0, GMP, Eigen)
#
# ale_bench is intentionally NOT installed here. The agent calls
# `ale-bench-eval` which forwards the request to the host-side server.py
# over HTTP (ALE_BENCH_SERVER_URL, default: http://host.docker.internal:8765).
#
FROM python:3.11.4-slim-bookworm

LABEL org.opencontainers.image.source=https://github.com/SakanaAI/ALE-Bench

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CARGO_HOME=/opt/cargo \
    RUSTUP_HOME=/opt/rustup \
    PATH=/opt/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# System deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      bash \
      build-essential \
      tmux \
      libcairo2 \
      libgdk-pixbuf-2.0-0 \
      libpango-1.0-0 \
      libpangocairo-1.0-0 \
      cmake \
      curl \
      docker.io \
      git \
      g++ \
      gcc \
      pkg-config \
      time \
      unzip \
      wget \
      libgmp-dev \
      libmpfr-dev \
      libmpc-dev \
      libeigen3-dev \
    && rm -rf /var/lib/apt/lists/*

# Python packages (AtCoder 202301 toolchain)
RUN python3.11 -m pip install --no-cache-dir \
      numpy==1.24.1 \
      scipy==1.10.1 \
      networkx==3.0 \
      sympy==1.11.1 \
      sortedcontainers==2.4.0 \
      more-itertools==9.0.0 \
      shapely==2.0.0 \
      bitarray==2.6.2 \
      PuLP==2.7.0 \
      mpmath==1.2.1 \
      pandas==1.5.2 \
      z3-solver==4.12.1.0 \
      scikit-learn==1.2.0 \
      ortools==9.5.2237 \
      polars==0.15.15 \
      lightgbm==3.3.1 \
      gmpy2==2.1.5 \
      numba==0.57.0 \
      git+https://github.com/not522/ac-library-python \
    && python3.11 -m pip install --no-cache-dir torch==1.13.1+cpu --extra-index-url https://download.pytorch.org/whl/cpu \
    && python3.11 -m pip install --no-cache-dir -U setuptools==66.0.0 \
    && python3.11 -m pip install --no-cache-dir cppyy==2.4.1 \
    && python3.11 -c "import numpy, scipy, networkx, sympy, sortedcontainers, more_itertools, shapely, bitarray, pulp, mpmath, pandas, z3, sklearn, ortools, polars, lightgbm, gmpy2, numba, atcoder, torch, setuptools, cppyy"

# Rust toolchain: rustc 1.70.0 + AtCoder 202301 crate set
COPY cargo_config.toml /opt/cargo/config.toml
COPY atcoder-rust-202301/ /opt/atcoder-rust-202301/
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain 1.70.0 \
    && rustc --version \
    && cargo --version \
    && mkdir -p /opt/atcoder-rust-202301/src \
    && printf 'fn main() {}\n' > /opt/atcoder-rust-202301/src/main.rs \
    && cd /opt/atcoder-rust-202301 \
    && cargo fetch -vv --locked

# C++: AC Library + Boost 1.82.0
WORKDIR /tmp
RUN mkdir -p /opt/ac-library && \
    wget https://github.com/atcoder/ac-library/releases/download/v1.5.1/ac-library.zip -O ac-library.zip && \
    unzip /tmp/ac-library.zip -d /opt/ac-library && \
    rm -f /tmp/ac-library.zip && \
    mkdir -p /usr/local/include && \
    if [ -d /opt/ac-library/atcoder ]; then ACL_ATCODER_DIR=/opt/ac-library/atcoder; \
    elif [ -d /opt/ac-library/ac-library/atcoder ]; then ACL_ATCODER_DIR=/opt/ac-library/ac-library/atcoder; \
    else echo "ERROR: ACL headers not found under /opt/ac-library" >&2; ls -la /opt/ac-library >&2; exit 1; fi && \
    ln -sfn "${ACL_ATCODER_DIR}" /usr/local/include/atcoder && \
    test -f /usr/local/include/atcoder/all

RUN wget https://archives.boost.io/release/1.82.0/source/boost_1_82_0.tar.gz -O boost_1_82_0.tar.gz && \
    tar xf boost_1_82_0.tar.gz && \
    cd boost_1_82_0 && \
    ./bootstrap.sh --with-toolset=gcc --without-libraries=mpi,graph_parallel && \
    ./b2 -j"$(nproc)" toolset=gcc variant=release link=static runtime-link=static cxxflags="-std=c++20" stage && \
    ./b2 -j"$(nproc)" toolset=gcc variant=release link=static runtime-link=static cxxflags="-std=c++20" --prefix=/opt/boost/gcc install && \
    cd /tmp && rm -rf /tmp/boost_1_82_0 /tmp/boost_1_82_0.tar.gz

# Node.js 20 LTS + Codex CLI
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @openai/codex

# Install the eval CLI client as a standalone command (stdlib only, no ale_bench)
COPY ale_bench_eval_client.py /usr/local/bin/ale-bench-eval
RUN chmod +x /usr/local/bin/ale-bench-eval

WORKDIR /testbed
CMD ["sleep", "infinity"]
