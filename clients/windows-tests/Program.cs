using System.Text;
using PiBotPal.Windows;

var frame = new byte[] { 0xff, 0xd8, 0xff, 0xd9 };
var header = Encoding.ASCII.GetBytes("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n");
var part = header.Concat(frame).Concat(Encoding.ASCII.GetBytes("\r\n")).ToArray();
var reader = new MjpegReader(new MemoryStream(part.Concat(part).ToArray()));
if (!(await reader.NextFrameAsync(default))!.SequenceEqual(frame) || !(await reader.NextFrameAsync(default))!.SequenceEqual(frame) || await reader.NextFrameAsync(default) is not null)
    throw new Exception("Multipart frame sequence failed");
// Real JPEG with legal COM segments: exercises >1 MiB frames without storing
// a large image in the repository. The image remains a decodable 8x8 JPEG.
var seed = Convert.FromBase64String("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAAIAAgBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAAP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AP//Z");
using var expanded = new MemoryStream();
expanded.Write(seed, 0, 2);
var comment = new byte[] { 0xff, 0xfe, 0xff, 0xff }.Concat(new byte[65533]).ToArray();
for (var i = 0; i < 17; i++) expanded.Write(comment);
expanded.Write(seed, 2, seed.Length - 2);
var largeFrame = expanded.ToArray();
if (largeFrame.Length <= 1024 * 1024) throw new Exception("Large frame fixture is too small");
static byte[] Multipart(byte[] jpeg) => Encoding.ASCII.GetBytes($"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {jpeg.Length}\r\n\r\n").Concat(jpeg).Concat(Encoding.ASCII.GetBytes("\r\n")).ToArray();
var largeReader = new MjpegReader(new FragmentedStream(Multipart(largeFrame).Concat(Multipart(seed)).ToArray()));
if (!(await largeReader.NextFrameAsync(default))!.SequenceEqual(largeFrame) || !(await largeReader.NextFrameAsync(default))!.SequenceEqual(seed) || await largeReader.NextFrameAsync(default) is not null)
    throw new Exception("Large multipart frame or following boundary was corrupted");
foreach (var length in new[] { 16 * 1024 * 1024 + 1, 999999999 }) {
    try {
        await new MjpegReader(new MemoryStream(Encoding.ASCII.GetBytes($"--frame\r\nContent-Length: {length}\r\n\r\n"))).NextFrameAsync(default);
        throw new Exception("Oversized frame was accepted");
    } catch (InvalidDataException) { }
}
foreach (var url in new[] { "http://robot.example", "https://user:password@robot.example", "https://robot.example/mcp", "https://robot.example?key=secret" }) {
    try { McpClient.ValidateAddress(url); throw new Exception("Invalid URL was accepted"); }
    catch (ArgumentException) { }
}
if (McpClient.ValidateAddress(McpClient.DefaultUrl + "/") != McpClient.DefaultUrl) throw new Exception("URL normalization failed");
Console.WriteLine("Transport tests passed: multipart framing, fragmented large JPEGs, 16 MiB frame limit, URL validation.");

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

sealed class FragmentedStream(byte[] bytes) : MemoryStream(bytes)
{
    public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default) =>
        base.ReadAsync(buffer[..Math.Min(buffer.Length, 4093)], cancellationToken);
}
