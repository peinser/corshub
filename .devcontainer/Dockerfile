# builder-base ################################################################

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder-base

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=0
ENV UV_PROJECT_ENVIRONMENT=/usr/local/
ENV PYTHONDONTWRITEBYTECODE=1

LABEL artifact=false

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        # Core build tools
        build-essential \
        cmake \
        ninja-build \
        make \
        git \
        curl \
        wget \
        jq \
        gnupg \
        vim \
        apache2-utils \
        autoconf \
        automake \
        libtool \
        pkg-config \
        openssh-client

        # Libraries
        # zlib1g-dev \
        # libzstd-dev \
        # libssl-dev \
        # libpcap-dev \
        # libusb-1.0-0-dev \
        # libboost-all-dev \
        # fakeroot \
        # help2man \
        # libncurses-dev \
        # librtlsdr-dev \
        # libsoapysdr-dev \
        # libhackrf-dev \
        # libbladerf-dev \
        # libad9361-dev \
        # libiio-dev \

        # socat
        # socat \
        # minicom \

        # AVR
        # avrdude \
        # avrdude-doc \
        # gcc-avr \
        # avr-libc \
        # binutils-avr \
        # gdb-avr \

        # Debuggers & profilers
        # gdb \
        # lldb \
        # valgrind \
        # strace \

        # C/C++ linters & formatters
        # clang \
        # clang-tools \
        # clang-tidy \
        # clang-format \
        # cppcheck \

        # HackRF dependencies
        # hackrf \
        # libhackrf-dev \
        # libfftw3-dev \
        # dfu-util \
        # && apt-get clean && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# devcontainer ################################################################

FROM builder-base AS devcontainer

LABEL artifact=false

ENV EDITOR=vim
ENV PATH=/workspace/bin:$PATH

ARG ARCH=$(dpkg --print-architecture)

# Install 'act' for local GitHub Actions testing
RUN ACT_VERSION=0.2.74 && \
    curl -fsSL "https://github.com/nektos/act/releases/download/v${ACT_VERSION}/act_Linux_${ARCH}.tar.gz" \
        | tar xz -C /usr/local/bin act

# Install `helm'
RUN HELM_VERSION=3.17.3 && \
    curl -fsSL "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${ARCH}.tar.gz" \
        | tar xz -C /tmp && \
    mv /tmp/linux-${ARCH}/helm /usr/local/bin/helm && \
    rm -rf /tmp/linux-${ARCH}

#  GitOps / policy toolchain: sops, age, cosign
ARG SOPS_VERSION=3.12.2
ARG AGE_VERSION=1.3.1
ARG COSIGN_VERSION=3.0.6
ARG CRANE_VERSION=0.21.5

RUN set -eux; \
    CRANE_ARCH="$([ "${ARCH}" = "amd64" ] && echo "x86_64" || echo "${ARCH}")"; \
    sudo curl -fsSL -o /usr/local/bin/sops \
      "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.${ARCH}" && \
    sudo chmod +x /usr/local/bin/sops; \
    curl -fsSL "https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/age-v${AGE_VERSION}-linux-${ARCH}.tar.gz" \
      | sudo tar -xz -C /usr/local/bin --strip-components=1 age/age age/age-keygen; \
    sudo curl -fsSL -o /usr/local/bin/cosign \
      "https://github.com/sigstore/cosign/releases/download/v${COSIGN_VERSION}/cosign-linux-${ARCH}" && \
    sudo chmod +x /usr/local/bin/cosign && \
    curl -sL "https://github.com/google/go-containerregistry/releases/download/v${CRANE_VERSION}/go-containerregistry_Linux_${CRANE_ARCH}.tar.gz" \
      | sudo tar -xz -C /usr/local/bin crane && \
    sudo chmod +x /usr/local/bin/crane
