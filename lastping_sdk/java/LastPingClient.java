package lastping;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.Callable;

public class LastPingClient {
    private final String baseUrl;
    private final String apiKey;
    private int timeoutMillis = 5000;

    public LastPingClient(String baseUrl, String apiKey) {
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.apiKey = apiKey;
    }

    public void setTimeoutMillis(int timeoutMillis) {
        this.timeoutMillis = timeoutMillis;
    }

    public void sendHeartbeat(int projectId, String name) throws Exception {
        String url = this.baseUrl + "/projects/" + projectId + "/heartbeat/" + name;
        sendJson(url, "{}");
    }

    public void sendEvent(int projectId, String checkName, String event, String message) throws Exception {
        String url = this.baseUrl + "/projects/" + projectId + "/webhook";
        StringBuilder payload = new StringBuilder();
        payload.append("{\"check_name\":\"").append(escapeJson(checkName)).append("\",");
        payload.append("\"event\":\"").append(escapeJson(event)).append("\"");
        if (message != null) {
            payload.append(",\"message\":\"").append(escapeJson(message)).append("\"");
        }
        payload.append("}");
        sendJson(url, payload.toString());
    }

    public void runWithHeartbeat(int projectId, String name, Runnable job, boolean captureErrors, String errorEvent) throws Exception {
        sendHeartbeat(projectId, name);
        try {
            job.run();
        } catch (Exception exc) {
            if (captureErrors) {
                String msg = "exception: " + exc.getClass().getSimpleName() + ": " + exc.getMessage();
                sendEvent(projectId, name, errorEvent, msg);
            }
            throw exc;
        }
    }

    public <T> T callWithHeartbeat(int projectId, String name, Callable<T> job, boolean captureErrors, String errorEvent) throws Exception {
        sendHeartbeat(projectId, name);
        try {
            return job.call();
        } catch (Exception exc) {
            if (captureErrors) {
                String msg = "exception: " + exc.getClass().getSimpleName() + ": " + exc.getMessage();
                sendEvent(projectId, name, errorEvent, msg);
            }
            throw exc;
        }
    }

    private void sendJson(String url, String json) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(this.timeoutMillis);
        conn.setReadTimeout(this.timeoutMillis);
        conn.setRequestMethod("POST");
        conn.setRequestProperty("Authorization", "Bearer " + this.apiKey);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setDoOutput(true);
        byte[] body = json.getBytes(StandardCharsets.UTF_8);
        try (OutputStream os = conn.getOutputStream()) {
            os.write(body);
        }
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            throw new RuntimeException("Request failed: " + code);
        }
    }

    private static String escapeJson(String value) {
        if (value == null) {
            return "";
        }
        return value.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r");
    }
}
