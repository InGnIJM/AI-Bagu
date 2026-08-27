package io.github.ingnijm.baguhelper;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import java.util.ArrayList;
import java.util.function.Consumer;

/** Only the installed Android recognition service is used; no app-owned audio or cloud client. */
final class AndroidSpeechBackend implements SpeechInput.Backend {
    interface Permissions { void request(Consumer<Boolean> reply); }
    private final Activity activity;
    private final Permissions permissions;

    AndroidSpeechBackend(Activity activity, Permissions permissions) {
        this.activity = activity; this.permissions = permissions;
    }

    @Override public boolean available() { return SpeechRecognizer.isRecognitionAvailable(activity); }
    @Override public boolean hasPermission() {
        return activity.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }
    @Override public void requestPermission(Consumer<Boolean> reply) { permissions.request(reply); }

    @Override public SpeechInput.Engine create(SpeechInput.Listener listener) {
        SpeechRecognizer recognizer = SpeechRecognizer.createSpeechRecognizer(activity);
        try {
            recognizer.setRecognitionListener(new RecognitionListener() {
                @Override public void onReadyForSpeech(Bundle params) { listener.ready(); }
                @Override public void onBeginningOfSpeech() {}
                @Override public void onRmsChanged(float value) {}
                @Override public void onBufferReceived(byte[] buffer) {}
                @Override public void onEndOfSpeech() { listener.ended(); }
                @Override public void onError(int error) { listener.error(errorMessage(error)); }
                @Override public void onResults(Bundle results) { listener.result(firstResult(results)); }
                @Override public void onPartialResults(Bundle results) { listener.partial(firstResult(results)); }
                @Override public void onEvent(int eventType, Bundle params) {}
            });
        } catch (RuntimeException failure) {
            recognizer.destroy();
            throw failure;
        }
        return new SpeechInput.Engine() {
            @Override public void start() {
                Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
                intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
                intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN");
                intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
                intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
                recognizer.startListening(intent);
            }
            @Override public void stop() { recognizer.stopListening(); }
            @Override public void cancel() { recognizer.cancel(); }
            @Override public void destroy() { recognizer.destroy(); }
        };
    }

    private static String firstResult(Bundle results) {
        if (results == null) return null;
        ArrayList<String> values = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        return values == null || values.isEmpty() ? null : values.get(0);
    }

    static String errorMessage(int error) {
        switch (error) {
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS: return "未获得麦克风权限，请允许权限后重试。";
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT: return "系统语音识别网络失败，请检查网络后重试。";
            case SpeechRecognizer.ERROR_NO_MATCH: return "未识别到语音，请重试。";
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT: return "未听到语音或识别超时，请重试。";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY: return "系统语音识别正忙，请稍后重试。";
            case SpeechRecognizer.ERROR_AUDIO: return "麦克风不可用，请检查占用或权限。";
            case SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED:
            case SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE: return "系统语音识别暂不支持中文，请检查系统语音服务。";
            default: return "系统语音识别失败，请稍后重试。";
        }
    }
}
