package io.github.ingnijm.baguhelper;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Locale;
import java.util.Map;

/** Pure policy boundary for the fixed native-owned interview-pack asset. */
final class BundledPackController {
    static final String ASSET_PATH = "question-pack/bundled.bagu-pack";
    static final String PROMPTED_HASH_KEY = "bundled_pack_auto_prompted_sha256";
    static final int MAX_BYTES = 20 * 1024 * 1024;

    enum Policy { CONFIRMABLE, READ_ONLY, BUSY, ERROR, SUPPRESSED }

    interface ByteSource {
        boolean available() throws Exception;
        byte[] read() throws Exception;
    }

    interface Inspector {
        Map<String,Object> inspect(byte[] bytes) throws Exception;
    }

    interface PreferenceStore {
        String get(String key);
        boolean put(String key, String value);
    }

    interface Lease { void release(); }
    interface LeaseGateway { Lease acquire(); }
    interface SessionGate { boolean hasOpenSession() throws Exception; }

    static final class Result {
        private Policy policy;
        private String code;
        private final PendingImport pending;
        private final PreferenceStore preferences;
        private String promptHash;
        private boolean activated;
        private Lease lease;

        private Result(Policy policy, String code, PendingImport pending, Lease lease,
                PreferenceStore preferences, String promptHash) {
            this.policy = policy;
            this.code = code;
            this.pending = pending;
            this.lease = lease;
            this.preferences = preferences;
            this.promptHash = promptHash;
        }

        Policy policy() { return policy; }
        String code() { return code; }
        PendingImport pending() { return pending; }

        synchronized boolean activate() {
            if (pending == null || (policy != Policy.CONFIRMABLE && policy != Policy.READ_ONLY)) return false;
            if (activated) return true;
            if (promptHash != null) {
                boolean stored;
                try { stored = preferences.put(PROMPTED_HASH_KEY, promptHash); }
                catch (RuntimeException ignored) { stored = false; }
                if (!stored) {
                    policy = Policy.ERROR;
                    code = "preference_write";
                    release();
                    return false;
                }
            }
            promptHash = null;
            activated = true;
            return true;
        }

        synchronized void release() {
            if (lease == null) return;
            Lease owned = lease;
            lease = null;
            owned.release();
        }

        @Override public String toString() {
            return "BundledPackResult{" + policy + "," + code + "}";
        }
    }

    private final ByteSource source;
    private final Inspector inspector;
    private final PreferenceStore preferences;
    private final LeaseGateway leases;
    private final SessionGate sessions;

    BundledPackController(ByteSource source, Inspector inspector, PreferenceStore preferences,
            LeaseGateway leases, SessionGate sessions) {
        if (source == null || inspector == null || preferences == null || leases == null || sessions == null) {
            throw new IllegalArgumentException("Bundled pack dependencies are required");
        }
        this.source = source;
        this.inspector = inspector;
        this.preferences = preferences;
        this.leases = leases;
        this.sessions = sessions;
    }

    boolean available() {
        try { return source.available(); }
        catch (Exception ignored) { return false; }
    }

    Result prepare(PendingImport.Source requestSource, boolean foregroundIdle) {
        if (requestSource != PendingImport.Source.BUNDLED_AUTO_PROMPT
                && requestSource != PendingImport.Source.BUNDLED_SETTINGS) {
            throw new IllegalArgumentException("Bundled controller requires a bundled source");
        }
        if (!foregroundIdle) return result(Policy.BUSY, "not_foreground");
        if (!available()) return result(Policy.ERROR, "unavailable");
        Lease lease = leases.acquire();
        if (lease == null) return result(Policy.BUSY, "native_busy");
        try {
            if (sessions.hasOpenSession()) return released(lease, Policy.BUSY, "open_session");
            byte[] loaded = source.read();
            if (loaded == null || loaded.length == 0) return released(lease, Policy.ERROR, "invalid");
            if (loaded.length > MAX_BYTES) return released(lease, Policy.ERROR, "too_large");
            byte[] retained = loaded.clone();
            String hash = sha256(retained);
            boolean automatic = requestSource == PendingImport.Source.BUNDLED_AUTO_PROMPT;
            if (automatic && hash.equals(preferences.get(PROMPTED_HASH_KEY))) {
                return released(lease, Policy.SUPPRESSED, "already_prompted");
            }
            Map<String,Object> preview = inspector.inspect(retained);
            String status = preview == null ? "" : String.valueOf(preview.get("status"));
            Policy policy = policy(status, automatic);
            if (policy == Policy.ERROR) return released(lease, Policy.ERROR, "invalid_status");
            if (policy == Policy.SUPPRESSED) return released(lease, policy, status);
            PendingImport pending = PendingImport.interviewPack(retained, preview, requestSource);
            return new Result(policy, status, pending, lease,
                automatic ? preferences : null, automatic ? hash : null);
        } catch (Exception ignored) {
            return released(lease, Policy.ERROR, "invalid");
        }
    }

    private static Policy policy(String status, boolean automatic) {
        if (automatic) {
            if ("new".equals(status) || "upgrade".equals(status)) return Policy.CONFIRMABLE;
            if ("installed".equals(status) || "downgrade".equals(status) || "conflict".equals(status)) {
                return Policy.SUPPRESSED;
            }
            return Policy.ERROR;
        }
        if ("new".equals(status) || "upgrade".equals(status) || "installed".equals(status)) {
            return Policy.CONFIRMABLE;
        }
        if ("downgrade".equals(status) || "conflict".equals(status)) return Policy.READ_ONLY;
        return Policy.ERROR;
    }

    private static Result result(Policy policy, String code) {
        return new Result(policy, code, null, null, null, null);
    }

    private static Result released(Lease lease, Policy policy, String code) {
        lease.release();
        return result(policy, code);
    }

    private static String sha256(byte[] bytes) {
        try {
            StringBuilder value = new StringBuilder(64);
            for (byte item : MessageDigest.getInstance("SHA-256").digest(bytes)) {
                value.append(String.format(Locale.ROOT, "%02x", item & 0xff));
            }
            return value.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }
}
