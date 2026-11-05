# syntax=docker/dockerfile:1
FROM alpine:3.23.6@sha256:85fe1e81d6758c208f3e1eed4338a1997e19d4be002d4dd32d3100c9a8c010a0 AS build
RUN apk add --no-cache meson libgcc musl-dev gcc g++ mingw-w64-gcc
ARG BUILD_JOBS=6
ARG WARTHOG_COMMIT=f7b9dfcd4b9dfc088894c354bf9f2eacea5e19d2
ENV WARTHOG_COMMIT=${WARTHOG_COMMIT}
COPY . /code
WORKDIR /code
RUN --mount=type=cache,id=warthog-windows-sources,target=/code/subprojects/packagecache,sharing=locked \
    LDFLAGS='-static' meson setup /build-mingw --cross-file crosscompile/x86_64-w64-mingw32.txt --default-library static --buildtype=release \
    && meson compile -C /build-mingw -j "${BUILD_JOBS}" \
    && DESTDIR=/install meson install -C /build-mingw --no-rebuild \
    && mkdir /testexport \
    && find /build-mingw/src -type f -name '*.exe' -exec cp '{}' /testexport/ ';'
RUN apk info -v > /toolchain.txt && gcc --version >> /toolchain.txt && meson --version >> /toolchain.txt
FROM scratch AS export
COPY --from=build /install/usr/local/bin/wart-node.exe /wart-node-windows.exe
COPY --from=build /install/usr/local/bin/wart-wallet.exe /wart-wallet-windows.exe
COPY --from=build /testexport/ /tests/
COPY --from=build /toolchain.txt /toolchain.txt
