package io.github.ingnijm.baguhelper;

import java.io.IOException;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;
import javax.net.ssl.SSLException;

/** Fixed public error vocabulary. The cause is memory-only, never a UI/log payload. */
final class UpdateFailure extends IOException {
    static final int HTTP = 1001, DNS = 1002, TIMEOUT = 1003, TLS = 1004, CONNECTION = 1005;
    static final int JSON = 1101, MANIFEST = 1102, LIMIT = 1103, REDIRECT = 1104;
    static final int STORAGE = 1201, LENGTH = 1202, HASH = 1203, APK = 1204;
    static final int PERMISSION = 1301, INSTALLER = 1302, UNKNOWN = 1999;
    final int code;
    final Integer httpStatus;

    UpdateFailure(int code) { this(code, null, null); }
    UpdateFailure(int code, Integer httpStatus, Throwable cause) {
        super("Update failure " + code, cause);
        if (!validCode(code) || (httpStatus != null && (httpStatus < 100 || httpStatus > 599)))
            throw new IllegalArgumentException("Invalid update failure");
        this.code = code;
        this.httpStatus = httpStatus;
    }

    static boolean validCode(int code) {
        return (code >= HTTP && code <= CONNECTION) || (code >= JSON && code <= REDIRECT) ||
            (code >= STORAGE && code <= APK) || code == PERMISSION || code == INSTALLER || code == UNKNOWN;
    }
    static UpdateFailure at(int boundary, Throwable failure) {
        return failure instanceof UpdateFailure ? (UpdateFailure) failure : new UpdateFailure(boundary, null, failure);
    }
    static IOException network(IOException failure) {
        if (failure instanceof UpdateIO.Cancelled || failure instanceof UpdateFailure) return failure;
        int code = failure instanceof UnknownHostException ? DNS : failure instanceof SocketTimeoutException ? TIMEOUT :
            failure instanceof SSLException ? TLS : CONNECTION;
        return new UpdateFailure(code, null, failure);
    }
    static String reason(int code, Integer status) {
        switch (code) {
            case HTTP: return status == null ? "更新源 HTTP 请求失败" : "更新源返回 HTTP " + status;
            case DNS: return "无法解析更新源域名";
            case TIMEOUT: return "连接或下载超时";
            case TLS: return "安全连接校验失败";
            case CONNECTION: return "无法连接更新源";
            case JSON: return "更新清单不是有效的 UTF-8 JSON";
            case MANIFEST: return "更新清单校验未通过";
            case LIMIT: return "更新数据超过大小限制";
            case REDIRECT: return "更新请求的重定向被安全规则拒绝";
            case STORAGE: return "本地更新文件无法读写";
            case LENGTH: return "安装包长度与清单不符";
            case HASH: return "安装包 SHA-256 校验不符";
            case APK: return "安装包身份、签名或兼容性校验未通过";
            case PERMISSION: return "尚未获得安装来源权限或无法打开授权设置";
            case INSTALLER: return "无法打开系统安装器";
            default: return "更新操作发生未分类错误";
        }
    }
}
