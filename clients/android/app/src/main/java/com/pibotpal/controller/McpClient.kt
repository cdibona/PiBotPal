package com.pibotpal.controller

import org.json.JSONObject
import java.io.BufferedInputStream
import java.net.HttpURLConnection
import java.net.URI
import java.util.concurrent.atomic.AtomicLong

/** Streamable HTTP MCP client for PiBotPal's stateless JSON transport. */
class McpClient(address: String) {
    val baseUrl: String = validateAddress(address)
    private val nextId = AtomicLong()
    @Volatile private var sessionId: String? = null
    @Volatile private var protocolVersion = "2025-03-26"

    fun initialize() {
        val result = rpc("initialize", JSONObject()
            .put("protocolVersion", protocolVersion)
            .put("capabilities", JSONObject())
            .put("clientInfo", JSONObject().put("name", "PiBotPal Android").put("version", "0.1.0")))
        protocolVersion = result.optString("protocolVersion", protocolVersion)
        // Stateless PiBotPal does not require notification state, but send it for protocol clients.
        rpc("notifications/initialized", JSONObject(), notification = true)
    }

    fun call(name: String, arguments: JSONObject = JSONObject()): JSONObject {
        val result = rpc("tools/call", JSONObject().put("name", name).put("arguments", arguments))
        val content = result.optJSONArray("content")
        val texts = buildList {
            if (content != null) for (i in 0 until content.length()) {
                val item = content.optJSONObject(i) ?: continue
                if (item.optString("type") == "text") add(item.optString("text"))
            }
        }.joinToString("\n")
        if (result.optBoolean("isError")) throw IllegalStateException(texts.take(500).ifBlank { "Robot rejected the command" })
        return result.optJSONObject("structuredContent") ?: if (texts.trim().startsWith("{")) JSONObject(texts) else JSONObject().put("text", texts)
    }

    private fun rpc(method: String, params: JSONObject, notification: Boolean = false): JSONObject {
        val connection = open("/mcp")
        if (method == "tools/call" && params.optString("name") == "plan_draft") connection.readTimeout = 120000
        try {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Accept", "application/json, text/event-stream")
            connection.setRequestProperty("MCP-Protocol-Version", protocolVersion)
            sessionId?.let { connection.setRequestProperty("Mcp-Session-Id", it) }
            val body = JSONObject().put("jsonrpc", "2.0").put("method", method).put("params", params)
            if (!notification) body.put("id", nextId.incrementAndGet())
            connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = connection.responseCode
            if (code !in 200..299) throw IllegalStateException("Robot HTTP $code")
            connection.getHeaderField("Mcp-Session-Id")?.let { sessionId = it }
            if (notification || code == 202 || code == 204) return JSONObject()
            val response = connection.inputStream.use { input ->
                val bytes = input.readBytesLimited(1024 * 1024)
                bytes.toString(Charsets.UTF_8)
            }
            val json = if (connection.contentType?.contains("text/event-stream") == true) {
                // PiBotPal uses JSON responses; accept one finite SSE result too.
                response.lineSequence().filter { it.startsWith("data:") }.map { it.substring(5).trim() }
                    .mapNotNull { runCatching { JSONObject(it) }.getOrNull() }
                    .firstOrNull { it.has("result") || it.has("error") }
                    ?: throw IllegalStateException("MCP returned no result")
            } else JSONObject(response)
            json.optJSONObject("error")?.let { throw IllegalStateException(it.optString("message", "MCP error").take(500)) }
            return json.optJSONObject("result") ?: throw IllegalStateException("Invalid MCP response")
        } finally { connection.disconnect() }
    }

    fun open(path: String): HttpURLConnection = (URI(baseUrl + path).toURL().openConnection() as HttpURLConnection).apply {
        connectTimeout = 5000
        readTimeout = 8000
        instanceFollowRedirects = false
        useCaches = false
    }

    companion object {
        const val DEFAULT_URL = "https://spiderbot.tail689a35.ts.net"
        fun validateAddress(value: String): String {
            val uri = URI(value.trim())
            require(uri.host != null && uri.userInfo == null && uri.query == null && uri.fragment == null) { "Enter a robot base URL without credentials, query, or fragment" }
            require(uri.path.isNullOrEmpty() || uri.path == "/") { "Use the base URL, without /mcp" }
            require(uri.scheme == "https" || (uri.scheme == "http" && uri.host in listOf("localhost", "127.0.0.1", "::1", "10.0.2.2"))) { "Use HTTPS; HTTP is allowed only for local development" }
            require(uri.port == -1 || uri.port in 1..65535) { "Invalid port" }
            return uri.toString().trimEnd('/')
        }
    }
}

private fun java.io.InputStream.readBytesLimited(limit: Int): ByteArray {
    val out = java.io.ByteArrayOutputStream()
    val buffer = ByteArray(8192)
    while (true) {
        val count = read(buffer)
        if (count < 0) break
        require(out.size() + count <= limit) { "Response too large" }
        out.write(buffer, 0, count)
    }
    return out.toByteArray()
}

/** Multipart reader for /video.mjpg. A maximum frame size prevents unbounded allocation. */
class MjpegReader(private val input: BufferedInputStream) {
    fun nextFrame(): ByteArray? {
        var length: Int? = null
        var lines = 0
        while (true) {
            val line = readLine() ?: return null
            require(++lines <= 64) { "Invalid MJPEG headers" }
            if (line.startsWith("Content-Length:", ignoreCase = true)) length = line.substringAfter(':').trim().toIntOrNull()
            if (line.isEmpty() && length != null) break
        }
        val size = length ?: throw IllegalStateException("MJPEG Content-Length missing")
        require(size in 4..1024 * 1024) { "Invalid JPEG frame size" }
        val bytes = ByteArray(size)
        var offset = 0
        while (offset < size) {
            val count = input.read(bytes, offset, size - offset)
            if (count < 0) return null
            offset += count
        }
        require(bytes[0] == 0xff.toByte() && bytes[1] == 0xd8.toByte()) { "Invalid JPEG frame" }
        return bytes
    }

    private fun readLine(): String? {
        val bytes = java.io.ByteArrayOutputStream()
        while (bytes.size() < 2048) {
            val b = input.read()
            if (b < 0) return if (bytes.size() == 0) null else bytes.toString("US-ASCII").trimEnd('\r')
            if (b == 10) return bytes.toString("US-ASCII").trimEnd('\r')
            bytes.write(b)
        }
        throw IllegalStateException("MJPEG header too large")
    }
}
