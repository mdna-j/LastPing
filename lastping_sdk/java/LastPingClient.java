package lastping;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class LastPingClient {
    private final String baseUrl;
    private final String apiKey;

    public LastPingClient(String baseUrl, String apiKey) {
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.apiKey = apiKey;
    }

    public void sendHeartbeat(int projectId, String name) throws Exception {
        String url = this.baseUrl + "/projects/" + projectId + "/heartbeat/" + name;
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod("POST");
        conn.setRequestProperty("Authorization", "Bearer " + this.apiKey);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setDoOutput(true);
        byte[] body = "{}".getBytes(StandardCharsets.UTF_8);
        try (OutputStream os = conn.getOutputStream()) {
            os.write(body);
        }
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            throw new RuntimeException("Heartbeat failed: " + code);
        }
    }
}
