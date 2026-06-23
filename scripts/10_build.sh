#!/usr/bin/env bash
set -Eeuo pipefail

# Clone + patch + build libIEC61850, then compile the FreeTASE2 Server tools.
#
# libIEC61850 has no TASE.2 server. We reuse its MMS engine, but it needs two
# non-default changes that this script applies automatically:
#   1. CONFIG_MMS_SUPPORT_VMD_SCOPE_NAMED_VARIABLES=1  (TASE.2 reads
#      TASE2_Version / Supported_Features at VMD scope; off by default).
#   2. A one-line fix to the VMD-scope read path in mms_read_service.c, which is
#      never compiled by default and otherwise fails to build once (1) is on.
# Optionally it also adds FreeTase2 client helper wrappers to the pyiec61850
# Python binding.

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPS_DIR="${DEPS_DIR:-$PROJECT/deps}"
LIB="$DEPS_DIR/libiec61850"

# Pinned dependency versions for reproducible builds. Override LIB61850_REF only
# if you know why. mbedtls is pinned in the fetch step below.
LIB61850_REF="${LIB61850_REF:-v1.6.1}"
MBEDTLS_VER="${MBEDTLS_VER:-3.6.0}"

mkdir -p "$DEPS_DIR"

if [[ ! -d "$LIB" ]]; then
  git clone https://github.com/mz-automation/libiec61850.git "$LIB"
fi
echo "[deps] checking out libIEC61850 $LIB61850_REF"
git -C "$LIB" fetch --tags --quiet origin || true
# Force to the pinned ref so re-runs are reproducible. This discards the in-tree
# patches below (which are then re-applied), and leaves the downloaded mbedtls
# (untracked) in place.
git -C "$LIB" checkout -f --quiet "$LIB61850_REF"

# --- fetch mbedtls so the server links with TLS / Secure ICCP support ---
# libIEC61850 only compiles its TLS layer if the mbedtls source is present under
# third_party/mbedtls. Without it the server fails to link (TLSConfiguration_*).
MBEDTLS_DIR="$LIB/third_party/mbedtls/mbedtls-${MBEDTLS_VER}"
if [[ ! -d "$MBEDTLS_DIR" ]]; then
  echo "[deps] downloading mbedtls ${MBEDTLS_VER} for TLS support"
  ( cd "$LIB/third_party/mbedtls" \
    && curl -fsSL -o mbedtls.tgz "https://github.com/Mbed-TLS/mbedtls/archive/refs/tags/v${MBEDTLS_VER}.tar.gz" \
    && tar xzf mbedtls.tgz \
    && rm -f mbedtls.tgz )
fi

# --- patch 1: enable VMD-scope named variables ---
CMAKE_CFG="$LIB/config/stack_config.h.cmake"
if grep -q '#define CONFIG_MMS_SUPPORT_VMD_SCOPE_NAMED_VARIABLES 0' "$CMAKE_CFG"; then
  echo "[patch] enabling CONFIG_MMS_SUPPORT_VMD_SCOPE_NAMED_VARIABLES"
  sed -i 's/#define CONFIG_MMS_SUPPORT_VMD_SCOPE_NAMED_VARIABLES 0/#define CONFIG_MMS_SUPPORT_VMD_SCOPE_NAMED_VARIABLES 1/' "$CMAKE_CFG"
fi

# --- patch 2: fix VMD-scope read path (missing 7th argument) ---
READSVC="$LIB/src/mms/iso_mms/server/mms_read_service.c"
if grep -Pzoq 'MmsServer_getDevice\(connection->server\), nameIdStr,\s*\n\s*values, connection, alternateAccess\);' "$READSVC"; then
  echo "[patch] fixing addNamedVariableToResultList VMD-scope call"
  perl -0pi -e 's/(MmsServer_getDevice\(connection->server\), nameIdStr,\s*\n\s*values, connection, alternateAccess)\)/$1, variableCount == 1)/' "$READSVC"
fi

# --- patch 3 (optional): FreeTase2 client helper wrappers for pyiec61850 ---
SWIG_IF="$LIB/pyiec61850/iec61850.i"
WRAP="$PROJECT/bindings/pyiec61850_tase2_wrappers.i"
if [[ -f "$SWIG_IF" && -f "$WRAP" ]] && ! grep -q 'pyiec61850_tase2_wrappers.i' "$SWIG_IF"; then
  echo "[patch] adding FreeTase2 helper wrappers to pyiec61850 binding"
  printf '\n%%include "%s"\n' "$WRAP" >> "$SWIG_IF"
fi

# --- build libIEC61850 (Python bindings optional but cheap) ---
cd "$LIB"
cmake -S . -B build -DBUILD_PYTHON_BINDINGS=ON
cmake --build build -j"$(nproc)"

# --- build FreeTASE2 Server tools ---
make -C "$PROJECT/src" LIB61850_HOME="$LIB"

echo
echo "[OK] Built:"
ls -l "$PROJECT/src/tase2_server" "$PROJECT/src/tase2_client" "$PROJECT/src/tase2_probe"
