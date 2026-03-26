# builder-base ################################################################

FROM harbor.peinser.com/ghcr/astral-sh/uv:python3.14-bookworm-slim AS builder-base

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
        pkg-config

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

# Install 'act' for local GitHub Actions testing
RUN curl --proto '=https' --tlsv1.2 -sSf https://raw.githubusercontent.com/nektos/act/master/install.sh | bash
