package io.github.ingnijm.baguhelper;

/** Pure mapping from PackageInstaller public status values to the fixed update vocabulary. */
final class UpdateInstallStatusPolicy {
    enum Kind { PENDING, SUCCESS, CANCELLED, FAILURE }

    static final class Result {
        final Kind kind;
        final int errorCode;
        private Result(Kind kind, int errorCode) { this.kind=kind; this.errorCode=errorCode; }
    }

    private UpdateInstallStatusPolicy() {}

    static Result map(int status, boolean developerVerificationFailure) {
        if (status == -1) return new Result(Kind.PENDING, 0);
        if (status == 0) return new Result(Kind.SUCCESS, 0);
        if (developerVerificationFailure && (status == 2 || status == 3))
            return new Result(Kind.FAILURE, UpdateFailure.VERIFICATION);
        if (status == 3) return new Result(Kind.CANCELLED, 0);
        if (status == 2) return new Result(Kind.FAILURE, UpdateFailure.PERMISSION);
        if (status == 4 || status == 5 || status == 7)
            return new Result(Kind.FAILURE, UpdateFailure.APK);
        if (status == 6) return new Result(Kind.FAILURE, UpdateFailure.STORAGE);
        return new Result(Kind.FAILURE, UpdateFailure.INSTALLER);
    }
}
