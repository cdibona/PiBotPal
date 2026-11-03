using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;

namespace PiBotPal.Windows;

public sealed class McpClient : IDisposable
{
    public const string DefaultUrl = "https://spiderbot.tail689a35.ts.net";
    public string BaseUrl { get; }
    private readonly HttpClient http = new(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = Timeout.InfiniteTimeSpan };
    private long nextId;
    private string protocol = "2025-03-26";
    private string? sessionId;

    public McpClient(string address) { BaseUrl = ValidateAddress(address); }
    public static string ValidateAddress(string address)
    {
        if (!Uri.TryCreate(address.Trim(), UriKind.Absolute, out var uri) || string.IsNullOrEmpty(uri.Host) ||
            !string.IsNullOrEmpty(uri.UserInfo) || !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment) ||
            uri.AbsolutePath != "/" || (uri.Scheme != "https" && !(uri.Scheme == "http" && uri.IsLoopback)))
            throw new ArgumentException("Use the robot's HTTPS base URL without /mcp, credentials, query, or fragment. HTTP is only allowed for loopback development.");
        return uri.GetLeftPart(UriPartial.Authority);
    }

    public async Task InitializeAsync(CancellationToken cancellationToken = default)
    {
        var result = await RpcAsync("initialize", new JsonObject {
            ["protocolVersion"] = protocol, ["capabilities"] = new JsonObject(),
            ["clientInfo"] = new JsonObject { ["name"] = "PiBotPal Windows", ["version"] = "0.1.0" }
        }, cancellationToken);
        protocol = result["protocolVersion"]?.GetValue<string>() ?? protocol;
        await RpcAsync("notifications/initialized", new JsonObject(), cancellationToken, true);
    }

    public async Task<JsonObject> CallAsync(string name, JsonObject? arguments = null, CancellationToken cancellationToken = default)
    {
        var result = await RpcAsync("tools/call", new JsonObject { ["name"] = name, ["arguments"] = arguments ?? new JsonObject() }, cancellationToken);
        var texts = result["content"]?.AsArray().OfType<JsonObject>()
            .Where(item => item["type"]?.GetValue<string>() == "text")
            .Select(item => item["text"]?.GetValue<string>() ?? "");
        var text = string.Join("\n", texts ?? []);
        if (result["isError"]?.GetValue<bool>() == true) throw new InvalidOperationException(text.Length > 500 ? text[..500] : text);
        if (result["structuredContent"] is JsonObject structured) return structured;
        return text.TrimStart().StartsWith('{') ? JsonNode.Parse(text)?.AsObject() ?? new JsonObject() : new JsonObject { ["text"] = text };
    }

    private async Task<JsonObject> RpcAsync(string method, JsonObject parameters, CancellationToken cancellationToken, bool notification = false)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(8));
        using var request = new HttpRequestMessage(HttpMethod.Post, BaseUrl + "/mcp");
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));
        request.Headers.Add("MCP-Protocol-Version", protocol);
        if (sessionId is not null) request.Headers.Add("Mcp-Session-Id", sessionId);
        var body = new JsonObject { ["jsonrpc"] = "2.0", ["method"] = method, ["params"] = parameters };
        if (!notification) body["id"] = Interlocked.Increment(ref nextId);
        request.Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json");
        using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, timeout.Token);
        response.EnsureSuccessStatusCode();
        if (response.Headers.TryGetValues("Mcp-Session-Id", out var sessions)) sessionId = sessions.FirstOrDefault();
        if (notification || response.StatusCode is System.Net.HttpStatusCode.Accepted or System.Net.HttpStatusCode.NoContent) return new JsonObject();
        await using var stream = await response.Content.ReadAsStreamAsync(timeout.Token);
        using var output = new MemoryStream();
        var buffer = new byte[8192];
        int count;
        while ((count = await stream.ReadAsync(buffer, timeout.Token)) > 0)
        {
            if (output.Length + count > 1024 * 1024) throw new InvalidDataException("MCP response is too large");
            output.Write(buffer, 0, count);
        }
        var text = Encoding.UTF8.GetString(output.ToArray());
        JsonObject? json;
        if (response.Content.Headers.ContentType?.MediaType == "text/event-stream")
        {
            json = text.Split('\n').Where(line => line.StartsWith("data:")).Select(line => {
                try { return JsonNode.Parse(line[5..].Trim()) as JsonObject; } catch { return null; }
            }).FirstOrDefault(item => item?.ContainsKey("result") == true || item?.ContainsKey("error") == true);
        }
        else json = JsonNode.Parse(text) as JsonObject;
        if (json?["error"] is JsonObject error) throw new InvalidOperationException(error["message"]?.GetValue<string>() ?? "MCP error");
        return json?["result"] as JsonObject ?? throw new InvalidDataException("Invalid MCP response");
    }

    public async Task<HttpResponseMessage> OpenVideoAsync(CancellationToken cancellationToken)
    {
        var response = await http.GetAsync(BaseUrl + "/video.mjpg", HttpCompletionOption.ResponseHeadersRead, cancellationToken);
        if (!response.IsSuccessStatusCode) { var code = response.StatusCode; response.Dispose(); throw new HttpRequestException($"Camera HTTP {(int)code}"); }
        return response;
    }
    public void Dispose() => http.Dispose();
}

public sealed class MjpegReader(Stream stream)
{
    public async Task<byte[]?> NextFrameAsync(CancellationToken token)
    {
        int? length = null;
        for (var lineCount = 0; lineCount < 64; lineCount++)
        {
            var line = await ReadLineAsync(token);
            if (line is null) return null;
            if (line.StartsWith("Content-Length:", StringComparison.OrdinalIgnoreCase) && int.TryParse(line[15..].Trim(), out var parsed)) length = parsed;
            if (line.Length == 0 && length.HasValue)
            {
                if (length.Value < 4 || length.Value > 1024 * 1024) throw new InvalidDataException("Invalid MJPEG frame size");
                var frame = new byte[length.Value];
                await stream.ReadExactlyAsync(frame, token);
                if (frame[0] != 0xff || frame[1] != 0xd8) throw new InvalidDataException("Invalid JPEG frame");
                return frame;
            }
        }
        throw new InvalidDataException("MJPEG headers are too long");
    }
    private async Task<string?> ReadLineAsync(CancellationToken token)
    {
        using var line = new MemoryStream();
        var one = new byte[1];
        while (line.Length < 2048)
        {
            if (await stream.ReadAsync(one, token) == 0) return line.Length == 0 ? null : Encoding.ASCII.GetString(line.ToArray()).TrimEnd('\r');
            if (one[0] == 10) return Encoding.ASCII.GetString(line.ToArray()).TrimEnd('\r');
            line.WriteByte(one[0]);
        }
        throw new InvalidDataException("MJPEG header is too long");
    }
}
