#!/bin/bash
# Builds a fast-discovery-server statically linked against this Fast DDS source tree.
# Only glibc (libc, libm, libpthread, libdl) remains dynamic, so the binary runs on any Linux
# of the same architecture with a glibc at least as new as the one of the build machine.
#
# usage: build_static_fds.sh <fast-dds-source-dir> <work-dir> [output-name]
# requires: cmake >= 3.20, g++, git, libssl-dev
set -euo pipefail
SRC=$(realpath "$1"); W=$(realpath -m "$2"); OUT=${3:-fast-discovery-server-patched}
P=$W/prefix
JOBS=${JOBS:-$(nproc)}
mkdir -p "$W" "$P/include"; cd "$W"

# Header-only asio
[ -d asio ] || git clone -q --depth 1 --branch asio-1-30-2 https://github.com/chriskohlhoff/asio.git
cp -r asio/asio/include/* "$P/include/"

# Static foonathan_memory, same version as ROS Jazzy's foonathan_memory_vendor
[ -d memory ] || git clone -q --depth 1 --branch v0.7-3 https://github.com/foonathan/memory.git
cmake -S memory -B memory-build -DCMAKE_INSTALL_PREFIX="$P" -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_BUILD_TYPE=Release \
    -DFOONATHAN_MEMORY_BUILD_EXAMPLES=OFF -DFOONATHAN_MEMORY_BUILD_TESTS=OFF -DFOONATHAN_MEMORY_BUILD_TOOLS=ON
cmake --build memory-build -j"$JOBS" --target install

# Static Fast-CDR, same version as ROS Jazzy's ros-jazzy-fastcdr
[ -d fastcdr ] || git clone -q --depth 1 --branch v2.2.8 https://github.com/eProsima/Fast-CDR.git fastcdr
cmake -S fastcdr -B fastcdr-build -DCMAKE_INSTALL_PREFIX="$P" -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_BUILD_TYPE=Release
cmake --build fastcdr-build -j"$JOBS" --target install

# Static TinyXML2
[ -d tinyxml2 ] || git clone -q --depth 1 --branch 10.0.0 https://github.com/leethomason/tinyxml2.git
cmake -S tinyxml2 -B tinyxml2-build -DCMAKE_INSTALL_PREFIX="$P" -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_BUILD_TYPE=Release -Dtinyxml2_BUILD_TESTING=OFF
cmake --build tinyxml2-build -j"$JOBS" --target install

# Static Fast DDS + tool. TINYXML2_* must be explicit: with PREFER_CONFIG the FindTinyXML2 module
# never sets TINYXML2_LIBRARY and the link fails.
LIBDIR=/usr/lib/$(gcc -dumpmachine)
cmake -S "$SRC" -B fastdds-build -DCMAKE_INSTALL_PREFIX="$P" \
    -DCMAKE_PREFIX_PATH="$P" -DCMAKE_FIND_PACKAGE_PREFER_CONFIG=ON \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
    -DTHIRDPARTY_Asio=OFF -DTHIRDPARTY_TinyXML2=OFF -DTHIRDPARTY_fastcdr=OFF \
    -DTINYXML2_LIBRARY="$P/lib/libtinyxml2.a" -DTINYXML2_INCLUDE_DIR="$P/include" \
    -DOPENSSL_USE_STATIC_LIBS=TRUE -DOPENSSL_SSL_LIBRARY="$LIBDIR/libssl.a" \
    -DOPENSSL_CRYPTO_LIBRARY="$LIBDIR/libcrypto.a" \
    -DCOMPILE_EXAMPLES=OFF -DBUILD_TESTING=OFF -DCOMPILE_TOOLS=ON -DSECURITY=OFF \
    -DCMAKE_EXE_LINKER_FLAGS="-static-libstdc++ -static-libgcc"
cmake --build fastdds-build -j"$JOBS" --target fast-discovery-server

BIN=$(find fastdds-build/tools/fds -name 'fast-discovery-server-*' -type f -perm -u+x | head -1)
cp "$BIN" "$W/$OUT"
strip "$W/$OUT"
echo "built: $W/$OUT"
ldd "$W/$OUT"
