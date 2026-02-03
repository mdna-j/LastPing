package lastping;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertTrue;

public class LastPingClientTest {
    @Test
    public void testEscapeJson() {
        String raw = "quote\" backslash\\ newline\n";
        String escaped = LastPingClient.escapeJson(raw);
        assertTrue(escaped.contains("\\\""));
        assertTrue(escaped.contains("\\\\"));
        assertTrue(escaped.contains("\\n"));
    }

    @Test
    public void testFormatException() {
        Exception exc = new RuntimeException("boom");
        String msg = LastPingClient.formatException(exc, false);
        assertTrue(msg.contains("RuntimeException"));
        assertTrue(msg.contains("boom"));
    }
}
