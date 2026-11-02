/*
 * The main executable of ACP.app (spec section 4.2).
 *
 * Notarisation needs the hardened runtime, and that attaches to a Mach-O file,
 * not a script. So this stub finds its own path, builds the absolute path to the
 * bundled Python and the launcher, and replaces itself with Python through execv.
 *
 * LaunchServices starts a Mac app with "/" as the working folder, so no path
 * here is relative. Built on the runner with:
 *     clang -O2 -arch arm64 -mmacosx-version-min=11.0 -o ACP stub.c
 */
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define PYTHON_REL "/../Resources/python/bin/python3"
#define LAUNCHER_REL "/../Resources/app/launcher.py"

static int join(char *out, size_t size, const char *dir, const char *rel) {
    int n = snprintf(out, size, "%s%s", dir, rel);
    return n > 0 && (size_t)n < size ? 0 : -1;
}

int main(int argc, char **argv) {
    char raw[PATH_MAX];
    char self[PATH_MAX];
    char dir[PATH_MAX];
    char python_raw[PATH_MAX];
    char launcher_raw[PATH_MAX];
    char python[PATH_MAX];
    char launcher[PATH_MAX];
    uint32_t size = sizeof(raw);

    if (_NSGetExecutablePath(raw, &size) != 0 || !realpath(raw, self)) {
        fprintf(stderr, "ACP: cannot find its own path\n");
        return 1;
    }
    /* self is .../ACP.app/Contents/MacOS/ACP; dir is its folder. */
    strncpy(dir, self, sizeof(dir) - 1);
    dir[sizeof(dir) - 1] = '\0';
    char *slash = strrchr(dir, '/');
    if (!slash) {
        fprintf(stderr, "ACP: unexpected executable path %s\n", self);
        return 1;
    }
    *slash = '\0';

    if (join(python_raw, sizeof(python_raw), dir, PYTHON_REL) ||
        join(launcher_raw, sizeof(launcher_raw), dir, LAUNCHER_REL) ||
        !realpath(python_raw, python) ||
        !realpath(launcher_raw, launcher)) {
        fprintf(stderr, "ACP: the bundled Python or launcher is missing under %s\n", dir);
        return 1;
    }

    /* -X utf8 and -B on the command line, as Start ACP.bat does on Windows. -B
     * keeps a signed bundle unchanged, since a new .pyc file would break the
     * signature. LaunchServices adds -psn_* arguments on old systems;
     * they are dropped. */
    char *args[argc + 6];
    int n = 0;
    args[n++] = python;
    args[n++] = "-X";
    args[n++] = "utf8";
    args[n++] = "-B";
    args[n++] = launcher;
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) == 0) continue;
        args[n++] = argv[i];
    }
    args[n] = NULL;

    execv(python, args);
    perror("ACP: could not start the bundled Python");
    return 1;
}
