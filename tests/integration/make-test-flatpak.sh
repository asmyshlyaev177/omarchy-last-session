#!/bin/bash
# Builds a runtime org.test.Platform and an app org.test.Hello into a local repository, offline,
# and installs both into $1/user, the FLATPAK_USER_DIR of the live tests. It is what Flatpak's own
# tests/make-test-runtime.sh and make-test-app.sh do: the runtime holds only the programs the app
# runs and the libraries ldd finds for them, copied from this machine.
#
#   tests/integration/make-test-flatpak.sh <dir> <branch>
set -euo pipefail

out=$1
branch=$2
export FLATPAK_USER_DIR=$out/user
repo=$out/repo
runtime=$out/runtime
app=$out/app
rm -rf "$repo" "$runtime" "$app" "$FLATPAK_USER_DIR" "$out/libs"

# A runtime is exported from usr/, a build directory needs files/, and they are one directory: the
# sandbox's /usr, to which Flatpak links /bin, /lib and /lib64.
mkdir -p "$runtime/usr/bin" "$runtime/usr/lib" "$runtime/usr/etc" "$runtime/usr/share/fonts"
ln -s lib "$runtime/usr/lib64"
ln -s usr "$runtime/files"
# ldconfig because Flatpak builds the sandbox's ld.so.cache with the runtime's own at every run.
for program in bash foot sleep ldconfig; do
    path=$(command -v "$program")
    cp -L "$path" "$runtime/usr/bin/"
    # A static binary has no libraries: glibc's ldd says "statically linked" with status 0, or in
    # older releases "not a dynamic executable" with status 1. Any other failure stops the build.
    libs=$(ldd "$path" 2>&1) || [[ $libs == *"not a dynamic executable"* ]] || {
        echo "ldd $path failed: $libs" >&2
        exit 1
    }
    grep -o '/[^ ]*' <<< "$libs" >> "$out/libs" || true
done
sort -u "$out/libs" | while read -r lib; do cp -L "$lib" "$runtime/usr/lib/"; done
# foot runs its command through sh, and refuses a locale that is not UTF-8.
ln -s bash "$runtime/usr/bin/sh"
mkdir -p "$runtime/usr/lib/locale"
cp -r /usr/lib/locale/C.utf8 "$runtime/usr/lib/locale/"
# foot opens no window without a font, and finds one through fontconfig.
cp -rL /etc/fonts "$runtime/usr/etc/"
cp /usr/share/fonts/TTF/DejaVuSansMono.ttf "$runtime/usr/share/fonts/"
cat > "$runtime/metadata" <<EOF
[Runtime]
name=org.test.Platform
runtime=org.test.Platform/$(flatpak --default-arch)/$branch
sdk=org.test.Platform/$(flatpak --default-arch)/$branch
EOF
flatpak build-export --runtime --disable-sandbox "$repo" "$runtime" "$branch"
flatpak --user remote-add --no-gpg-verify ols-test "$repo"
flatpak --user install -y --noninteractive ols-test "org.test.Platform//$branch"

flatpak build-init "$app" org.test.Hello org.test.Platform org.test.Platform "$branch"
mkdir -p "$app/files/bin"
# Its window's process line names /app/bin/hello-term, under the /app only the sandbox has, so
# the host has no program to replay it with. With --as-host-command it names a host program instead, as
# any sandboxed app can make its own argv say whatever it likes.
cat > "$app/files/bin/hello" <<'EOF'
#!/bin/bash
name=/app/bin/hello-term
if [ "${1:-}" = --as-host-command ]; then
    name=/usr/bin/foot
fi
exec -a "$name" foot --app-id=org.test.Hello --title=Hello sleep 86400
EOF
chmod +x "$app/files/bin/hello"
flatpak build-finish --command=hello --socket=wayland "$app"
flatpak build-export --disable-sandbox "$repo" "$app" "$branch"
flatpak --user install -y --noninteractive ols-test "org.test.Hello//$branch"
