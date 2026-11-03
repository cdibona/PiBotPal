using System.ComponentModel;
using System.IO;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;

namespace PiBotPal.Windows;

public partial class MainWindow : Window
{
    private McpClient? client;
    private readonly SemaphoreSlim commandGate = new(1, 1);
    private readonly CancellationTokenSource lifetime = new();
    private CancellationTokenSource? videoCancellation;
    private bool foreground, connected, pending, armed, busy, cameraOn, closing, closeCompleted;
    private long commandEpoch;
    private int stopping;
    private readonly string settingsPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PiBotPal", "client.json");

    public MainWindow()
    {
        InitializeComponent();
        AddressBox.Text = LoadAddress();
        UpdateControls();
        Loaded += (_, _) => { _ = PollAsync(); };
        Activated += (_, _) => { foreground = true; UpdateControls(); };
        Deactivated += async (_, _) => {
            foreground = false;
            connected = false;
            StopVideo();
            UpdateControls();
            ConnectionText.Text = "Paused · reconnect to continue";
            if (!closing) await StopRobotAsync();
        };
        Closing += ClosingWindow;
    }

    private string LoadAddress()
    {
        try { return JsonNode.Parse(File.ReadAllText(settingsPath))?["url"]?.GetValue<string>() ?? McpClient.DefaultUrl; }
        catch { return McpClient.DefaultUrl; }
    }

    private async void Connect_Click(object sender, RoutedEventArgs e)
    {
        if (pending || stopping > 0) return;
        McpClient target;
        try { target = new McpClient(AddressBox.Text); }
        catch (Exception error) { ErrorText.Text = error.Message; return; }
        pending = true;
        connected = false;
        UpdateControls();
        StopVideo();
        await StopRobotAsync();
        client?.Dispose();
        client = target;
        ConnectionText.Text = "Connecting…";
        ErrorText.Text = "";
        var epoch = commandEpoch;
        try
        {
            await target.InitializeAsync(lifetime.Token);
            var state = await target.CallAsync("get_status", cancellationToken: lifetime.Token);
            if (!foreground || epoch != commandEpoch) { await target.CallAsync("stop"); return; }
            connected = true;
            Directory.CreateDirectory(Path.GetDirectoryName(settingsPath)!);
            File.WriteAllText(settingsPath, new JsonObject { ["url"] = target.BaseUrl }.ToJsonString());
            ConnectionText.Text = "Connected · MCP";
            Render(state);
        }
        catch (Exception error) { await DisconnectedAsync(error.Message); }
        finally { pending = false; UpdateControls(); }
    }

    private async Task PollAsync()
    {
        try
        {
            while (!lifetime.IsCancellationRequested)
            {
                await Task.Delay(1000, lifetime.Token);
                var target = client;
                var epoch = commandEpoch;
                if (!foreground || !connected || target is null) continue;
                try
                {
                    var state = await target.CallAsync("get_status", cancellationToken: lifetime.Token);
                    if (target == client && connected && foreground && epoch == commandEpoch) Render(state);
                }
                catch (Exception error) when (!lifetime.IsCancellationRequested)
                {
                    if (target == client) await DisconnectedAsync(error.Message);
                }
            }
        }
        catch (OperationCanceledException) { }
    }

    private async Task CommandAsync(string tool, JsonObject arguments)
    {
        var target = client;
        if (!connected || !foreground || pending || stopping > 0 || target is null) return;
        pending = true; UpdateControls(); ErrorText.Text = "";
        var epoch = commandEpoch;
        await commandGate.WaitAsync();
        try
        {
            if (!foreground || !connected || epoch != commandEpoch) return;
            await target.CallAsync(tool, arguments, lifetime.Token);
            var state = await target.CallAsync("get_status", cancellationToken: lifetime.Token);
            if (target == client && connected && foreground && epoch == commandEpoch) Render(state);
        }
        catch (Exception error) { ErrorText.Text = error.Message; }
        finally { commandGate.Release(); pending = false; UpdateControls(); }
    }

    private async Task StopRobotAsync()
    {
        var target = client;
        if (target is null) return;
        commandEpoch++;
        stopping++;
        armed = false; UpdateControls();
        // Immediate stop is followed by a second stop after already-submitted commands.
        try { await target.CallAsync("stop"); } catch { }
        await commandGate.WaitAsync();
        try { await target.CallAsync("stop"); } catch { }
        finally {
            commandGate.Release();
            commandEpoch++; // discard telemetry sampled before final stop acknowledgement
            stopping--;
            armed = false;
            UpdateControls();
        }
    }

    private async void Stop_Click(object sender, RoutedEventArgs e)
    {
        await StopRobotAsync();
        MotionText.Text = "Stopped";
    }
    private async void Arm_Click(object sender, RoutedEventArgs e) => await CommandAsync("arm", new JsonObject { ["armed"] = !armed });
    private async void Camera_Click(object sender, RoutedEventArgs e) => await CommandAsync("camera", new JsonObject { ["enabled"] = !cameraOn });
    private async void Move_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button button && button.Tag is string name)
            await CommandAsync("move", new JsonObject { ["name"] = name, ["speed"] = (int)SpeedSlider.Value, ["steps"] = 1 });
    }
    private void Speed_Changed(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (SpeedText is not null) SpeedText.Text = $"Movement speed: {(int)e.NewValue}% · one gait cycle per press";
    }

    private void Render(JsonObject state)
    {
        armed = stopping == 0 && state["armed"]?.GetValue<bool>() == true;
        busy = state["busy"]?.GetValue<bool>() == true;
        cameraOn = state["camera"]?["enabled"]?.GetValue<bool>() == true;
        var voltage = state["battery_voltage"]?.GetValue<double>();
        var fresh = state["power"]?["valid"]?.GetValue<bool>() != false;
        PowerText.Text = voltage is double v && double.IsFinite(v) && fresh ? $"{v:F2} V" : "Unavailable";
        MotionText.Text = state["estop"]?.GetValue<bool>() == true ? "Stopped" : busy ? state["action"]?.GetValue<string>() ?? "Moving" : armed ? "Armed" : "Disarmed";
        RangeText.Text = state["ultrasonic"]?["valid"]?.GetValue<bool>() == true ? $"{state["ultrasonic"]?["distance_cm"]?.GetValue<double>():F1} cm" : "Unavailable";
        ArmButton.Content = armed ? "Disarm motion" : "Arm motion";
        CameraButton.Content = cameraOn ? "Disable camera" : "Enable camera";
        if (cameraOn && foreground && videoCancellation is null) StartVideo();
        if (!cameraOn) { StopVideo(); VideoText.Text = "Camera off"; }
        UpdateControls();
    }

    private async Task DisconnectedAsync(string message)
    {
        var wasConnected = connected;
        connected = false;
        armed = false;
        StopVideo();
        ConnectionText.Text = "Disconnected · reconnect to continue";
        MotionText.Text = "Unknown";
        PowerText.Text = RangeText.Text = "Unavailable";
        ErrorText.Text = message.Length > 500 ? message[..500] : message;
        UpdateControls();
        if (wasConnected) await StopRobotAsync();
    }

    private void StartVideo()
    {
        var target = client;
        if (target is null) return;
        var cancellation = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
        videoCancellation = cancellation;
        _ = Task.Run(async () =>
        {
            try
            {
                using var connectTimeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation.Token);
                connectTimeout.CancelAfter(TimeSpan.FromSeconds(8));
                using var response = await target.OpenVideoAsync(connectTimeout.Token);
                await using var raw = await response.Content.ReadAsStreamAsync(cancellation.Token);
                await using var input = new BufferedStream(raw, 16384);
                var reader = new MjpegReader(input);
                while (!cancellation.IsCancellationRequested)
                {
                    using var frameTimeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation.Token);
                    frameTimeout.CancelAfter(TimeSpan.FromSeconds(8));
                    var bytes = await reader.NextFrameAsync(frameTimeout.Token);
                    if (bytes is null) break;
                    using var memory = new MemoryStream(bytes);
                    var frame = new BitmapImage();
                    frame.BeginInit(); frame.CacheOption = BitmapCacheOption.OnLoad; frame.StreamSource = memory; frame.EndInit(); frame.Freeze();
                    await Dispatcher.InvokeAsync(() => {
                        if (videoCancellation == cancellation && foreground && connected) { VideoImage.Source = frame; VideoText.Text = $"Live camera · {frame.PixelWidth} × {frame.PixelHeight}"; }
                    });
                }
            }
            catch (Exception error) when (!cancellation.IsCancellationRequested)
            {
                await Dispatcher.InvokeAsync(() => { if (videoCancellation == cancellation) { VideoImage.Source = null; VideoText.Text = $"Camera unavailable: {error.Message}"; } });
            }
            finally
            {
                await Dispatcher.InvokeAsync(() => { if (videoCancellation == cancellation) videoCancellation = null; });
                cancellation.Dispose();
            }
        });
    }

    private void StopVideo()
    {
        videoCancellation?.Cancel();
        videoCancellation = null;
        VideoImage.Source = null;
        VideoText.Text = "Camera disconnected";
    }
    private void UpdateControls()
    {
        if (ArmButton is null) return;
        ArmButton.IsEnabled = connected && foreground && !pending && !busy && stopping == 0;
        CameraButton.IsEnabled = connected && foreground && !pending && stopping == 0;
        ConnectButton.IsEnabled = !pending && stopping == 0;
        foreach (var button in new[] { LeftButton, RightButton, ForwardButton, BackwardButton, StandButton, SitButton })
            button.IsEnabled = connected && foreground && armed && !busy && !pending && stopping == 0;
    }
    private async void ClosingWindow(object? sender, CancelEventArgs e)
    {
        if (closing) { e.Cancel = !closeCompleted; return; }
        e.Cancel = true;
        closing = true;
        foreground = connected = false;
        StopVideo();
        // Bound app shutdown; server heartbeat is the fallback if the network is gone.
        await Task.WhenAny(StopRobotAsync(), Task.Delay(2000));
        lifetime.Cancel();
        client?.Dispose();
        closeCompleted = true;
        Close();
    }
}
