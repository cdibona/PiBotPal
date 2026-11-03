using System.Text;
using PiBotPal.Windows;

var frame = new byte[] { 0xff, 0xd8, 0xff, 0xd9 };
var header = Encoding.ASCII.GetBytes("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n");
var part = header.Concat(frame).Concat(Encoding.ASCII.GetBytes("\r\n")).ToArray();
var reader = new MjpegReader(new MemoryStream(part.Concat(part).ToArray()));
if (!(await reader.NextFrameAsync(default))!.SequenceEqual(frame) || !(await reader.NextFrameAsync(default))!.SequenceEqual(frame) || await reader.NextFrameAsync(default) is not null)
    throw new Exception("Multipart frame sequence failed");
try {
    await new MjpegReader(new MemoryStream(Encoding.ASCII.GetBytes("--frame\r\nContent-Length: 999999999\r\n\r\n"))).NextFrameAsync(default);
    throw new Exception("Oversized frame was accepted");
} catch (InvalidDataException) { }
foreach (var url in new[] { "http://robot.example", "https://user:password@robot.example", "https://robot.example/mcp", "https://robot.example?key=secret" }) {
    try { McpClient.ValidateAddress(url); throw new Exception("Invalid URL was accepted"); }
    catch (ArgumentException) { }
}
if (McpClient.ValidateAddress(McpClient.DefaultUrl + "/") != McpClient.DefaultUrl) throw new Exception("URL normalization failed");
Console.WriteLine("Transport tests passed: multipart framing, size limits, URL validation.");

if (args.Length == 2 && args[0] == "--smoke") {
    using var robot = new McpClient(args[1]);
    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(20));
    await robot.InitializeAsync(timeout.Token);
    var state = await robot.CallAsync("get_status", cancellationToken: timeout.Token);
    Console.WriteLine($"Read-only MCP smoke: armed={state["armed"]}, battery={state["battery_voltage"]}, mic={state["microphone"]?["enabled"]}");
    using var response = await robot.OpenVideoAsync(timeout.Token);
    await using var stream = await response.Content.ReadAsStreamAsync(timeout.Token);
    var jpeg = await new MjpegReader(new BufferedStream(stream)).NextFrameAsync(timeout.Token);
    if (jpeg is null || jpeg.Length < 4) throw new Exception("No live JPEG frame");
    Console.WriteLine($"Native MJPEG decoder received {jpeg.Length} bytes.");
}
