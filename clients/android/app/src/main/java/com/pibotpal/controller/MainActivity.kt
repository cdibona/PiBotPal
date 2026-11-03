package com.pibotpal.controller

import android.app.Activity
import android.graphics.BitmapFactory
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.*
import org.json.JSONObject
import java.io.BufferedInputStream
import java.net.HttpURLConnection
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private val commands = Executors.newSingleThreadExecutor()
    private val polls = Executors.newSingleThreadExecutor()
    private val polling = AtomicBoolean(false)
    private var client: McpClient? = null
    @Volatile private var foreground = false
    @Volatile private var connected = false
    @Volatile private var pending = false
    private var armed = false
    private var busy = false
    private var cameraOn = false
    @Volatile private var videoConnection: HttpURLConnection? = null
    @Volatile private var videoGeneration = 0
    private val motionButtons = mutableListOf<Button>()
    private lateinit var address: EditText
    private lateinit var connectionLabel: TextView
    private lateinit var powerLabel: TextView
    private lateinit var rangeLabel: TextView
    private lateinit var stateLabel: TextView
    private lateinit var videoStatus: TextView
    private lateinit var errorLabel: TextView
    private lateinit var video: ImageView
    private lateinit var armButton: Button
    private lateinit var cameraButton: Button
    private lateinit var speed: SeekBar

    private val ticker = object : Runnable {
        override fun run() {
            if (foreground && connected && polling.compareAndSet(false, true)) {
                val target = client
                polls.execute {
                    try {
                        val state = target?.call("get_status")
                        ui.post { if (foreground && target === client && connected && state != null) render(state) }
                    } catch (e: Exception) {
                        ui.post { if (target === client) disconnected(e.message ?: "Connection lost") }
                    } finally { polling.set(false) }
                }
            }
            ui.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(18), dp(16), dp(12)); setBackgroundColor(Color.rgb(12,18,23)) }
        val title = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        title.addView(text("PiBotPal", 25), LinearLayout.LayoutParams(0, dp(64), 1f))
        title.addView(button("■ STOP") { stopRobot() }.apply { setTextColor(Color.WHITE); setBackgroundColor(Color.rgb(159,49,57)) }, LinearLayout.LayoutParams(dp(132), dp(56)))
        root.addView(title)
        val scroll = ScrollView(this)
        val content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        scroll.addView(content)
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        connectionLabel = text("Disconnected · connect through Tailscale", 14)
        content.addView(connectionLabel)
        address = EditText(this).apply {
            setSingleLine(); setTextColor(Color.WHITE); textSize = 14f
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI
            setText(getPreferences(MODE_PRIVATE).getString("robot_url", McpClient.DEFAULT_URL))
            hint = "https://your-robot.ts.net"; contentDescription = "Robot URL"
        }
        content.addView(address)
        content.addView(button("Connect") { connect() })
        powerLabel = text("BATTERY / POWER   — V", 22)
        powerLabel.setTextColor(Color.rgb(109,223,194)); content.addView(powerLabel)
        content.addView(text("Powered while connected · charging and runtime unknown", 12))
        stateLabel = text("Motion disarmed", 18); content.addView(stateLabel)
        rangeLabel = text("Front range — cm", 18); content.addView(rangeLabel)
        video = ImageView(this).apply { setBackgroundColor(Color.BLACK); scaleType = ImageView.ScaleType.FIT_CENTER; contentDescription = "Live robot camera" }
        content.addView(video, LinearLayout.LayoutParams(-1, dp(240)))
        videoStatus = text("Camera disconnected", 13); content.addView(videoStatus)
        cameraButton = button("Enable camera") { command("camera", JSONObject().put("enabled", !cameraOn)) }
        content.addView(cameraButton)
        armButton = button("Arm motion") { command("arm", JSONObject().put("armed", !armed)) }
        content.addView(armButton)
        content.addView(text("Each movement button runs one gait cycle.", 13))
        val speedLabel = text("Movement speed: 35%", 14)
        content.addView(speedLabel)
        speed = SeekBar(this).apply { max = 50; progress = 25 }
        speed.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?, value: Int, fromUser: Boolean) { speedLabel.text = "Movement speed: ${value + 10}%" }
            override fun onStartTrackingTouch(bar: SeekBar?) {}
            override fun onStopTrackingTouch(bar: SeekBar?) {}
        })
        content.addView(speed)
        val row1 = LinearLayout(this)
        listOf("↶ Left" to "turn left", "↑ Forward" to "forward", "Right ↷" to "turn right").forEach { (label, action) -> row1.addView(moveButton(label, action), LinearLayout.LayoutParams(0, dp(64), 1f)) }
        content.addView(row1)
        val row2 = LinearLayout(this)
        listOf("Stand" to "stand", "↓ Back" to "backward", "Sit" to "sit").forEach { (label, action) -> row2.addView(moveButton(label, action), LinearLayout.LayoutParams(0, dp(64), 1f)) }
        content.addView(row2)
        content.addView(text("Microphone capture is never enabled by this app.", 13))
        content.addView(text("Stop holds the last servo command; it does not remove power. Leaving this app stops motion. The robot service also has a heartbeat timeout.", 12))
        errorLabel = text("", 14).apply { setTextColor(Color.rgb(250,145,145)) }; content.addView(errorLabel)
        root.setOnApplyWindowInsetsListener { view, insets ->
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars())
                view.setPadding(dp(16) + bars.left, dp(18) + bars.top, dp(16) + bars.right, dp(12) + bars.bottom)
            } else {
                @Suppress("DEPRECATION")
                view.setPadding(dp(16) + insets.systemWindowInsetLeft, dp(18) + insets.systemWindowInsetTop, dp(16) + insets.systemWindowInsetRight, dp(12) + insets.systemWindowInsetBottom)
            }
            insets
        }
        setContentView(root)
        updateControls()
        ui.post(ticker)
    }

    private fun connect() {
        val target = try { McpClient(address.text.toString()) } catch (e: Exception) { errorLabel.text = e.message; return }
        stopRobot()
        stopVideo()
        connected = false
        client = target
        connectionLabel.text = "Connecting…"
        errorLabel.text = ""
        pending = true; updateControls()
        commands.execute {
            try {
                target.initialize()
                val state = target.call("get_status")
                ui.post {
                    if (foreground && target === client) {
                        connected = true
                        getPreferences(MODE_PRIVATE).edit().putString("robot_url", target.baseUrl).apply()
                        connectionLabel.text = "Connected · MCP"
                        render(state)
                    }
                }
            } catch (e: Exception) { ui.post { if (target === client) disconnected(e.message ?: "Connection failed") } }
            finally { ui.post { pending = false; updateControls() } }
        }
    }

    private fun command(tool: String, arguments: JSONObject) {
        val target = client ?: return
        if (!connected || pending || !foreground) return
        pending = true; updateControls(); errorLabel.text = ""
        commands.execute {
            try {
                if (!foreground) return@execute
                target.call(tool, arguments)
                val state = target.call("get_status")
                ui.post { if (foreground && target === client && connected) render(state) }
            } catch (e: Exception) { ui.post { errorLabel.text = e.message ?: "Command failed" } }
            finally { ui.post { pending = false; updateControls() } }
        }
    }

    /** Send an immediate stop, then another after any already-submitted arm/move finishes. */
    private fun stopRobot() {
        val target = client ?: return
        armed = false
        updateControls()
        Thread({ runCatching { target.call("stop") } }, "pibotpal-immediate-stop").start()
        if (!commands.isShutdown) commands.execute { runCatching { target.call("stop") } }
    }

    private fun render(state: JSONObject) {
        armed = state.optBoolean("armed")
        busy = state.optBoolean("busy")
        cameraOn = state.optJSONObject("camera")?.optBoolean("enabled") == true
        val power = state.optJSONObject("power")
        val voltage = if (power != null && !power.optBoolean("valid")) Double.NaN else state.optDouble("battery_voltage", Double.NaN)
        powerLabel.text = if (voltage.isFinite()) "BATTERY / POWER   %.2f V".format(voltage) else "BATTERY / POWER   unavailable"
        stateLabel.text = if (state.optBoolean("estop")) "Stopped · arm to reset" else if (busy) "Moving: ${state.optString("action")}" else if (armed) "Motion armed" else "Motion disarmed"
        val sonar = state.optJSONObject("ultrasonic")
        rangeLabel.text = if (sonar?.optBoolean("valid") == true) "Front range %.1f cm".format(sonar.optDouble("distance_cm")) else "Front range unavailable"
        armButton.text = if (armed) "Disarm motion" else "Arm motion"
        cameraButton.text = if (cameraOn) "Disable camera" else "Enable camera"
        if (cameraOn && foreground && videoConnection == null) startVideo()
        if (!cameraOn) { stopVideo(); videoStatus.text = "Camera off" }
        updateControls()
    }

    private fun disconnected(message: String) {
        val wasConnected = connected
        connected = false; armed = false
        if (wasConnected) stopRobot()
        stopVideo()
        connectionLabel.text = "Disconnected · reconnect to continue"
        powerLabel.text = "BATTERY / POWER   unavailable"
        rangeLabel.text = "Front range unavailable"
        stateLabel.text = "Motion state unavailable"
        errorLabel.text = message.take(500)
        updateControls()
    }

    private fun startVideo() {
        val target = client ?: return
        val generation = ++videoGeneration
        val connection = target.open("/video.mjpg")
        videoConnection = connection
        Thread({
            try {
                if (connection.responseCode != 200) throw IllegalStateException("Camera HTTP ${connection.responseCode}")
                BufferedInputStream(connection.inputStream).use { input ->
                    val reader = MjpegReader(input)
                    while (foreground && connected && generation == videoGeneration) {
                        val bytes = reader.nextFrame() ?: break
                        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: continue
                        ui.post { if (generation == videoGeneration && foreground) { video.setImageBitmap(bitmap); videoStatus.text = "Live camera · ${bitmap.width} × ${bitmap.height}" } }
                    }
                }
            } catch (e: Exception) {
                ui.post { if (generation == videoGeneration) { video.setImageDrawable(null); videoStatus.text = "Camera unavailable: ${e.message?.take(100)}" } }
            } finally {
                connection.disconnect()
                if (generation == videoGeneration) videoConnection = null
            }
        }, "pibotpal-video").start()
    }

    private fun stopVideo() {
        videoGeneration++
        videoConnection?.disconnect()
        videoConnection = null
        video.setImageDrawable(null)
        videoStatus.text = "Camera disconnected"
    }

    private fun updateControls() {
        if (!::armButton.isInitialized) return
        armButton.isEnabled = connected && foreground && !pending && !busy
        cameraButton.isEnabled = connected && foreground && !pending
        motionButtons.forEach { it.isEnabled = connected && foreground && armed && !busy && !pending }
    }

    private fun moveButton(label: String, action: String): Button = button(label) { command("move", JSONObject().put("name", action).put("speed", speed.progress + 10).put("steps", 1)) }.also { motionButtons.add(it) }
    private fun button(label: String, click: () -> Unit) = Button(this).apply { text = label; isAllCaps = false; setOnClickListener { click() } }
    private fun text(label: String, size: Int) = TextView(this).apply { text = label; textSize = size.toFloat(); setTextColor(Color.rgb(220,230,234)); setPadding(0, dp(8), 0, dp(8)) }
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    override fun onResume() { super.onResume(); foreground = true; updateControls() }
    override fun onPause() { foreground = false; stopRobot(); connected = false; stopVideo(); connectionLabel.text = "Paused · reconnect to continue"; updateControls(); super.onPause() }
    override fun onDestroy() { ui.removeCallbacks(ticker); polls.shutdownNow(); commands.shutdown(); super.onDestroy() }
}
