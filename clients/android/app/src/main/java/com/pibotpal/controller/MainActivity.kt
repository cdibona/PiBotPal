package com.pibotpal.controller

import android.app.Activity
import android.content.Intent
import android.graphics.BitmapFactory
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.*
import org.json.JSONObject
import org.json.JSONArray
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
    @Volatile private var commandEpoch = 0L
    private var stopping = 0
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
    private lateinit var planStatus: TextView
    private lateinit var planName: EditText
    private lateinit var planPrompt: EditText
    private lateinit var usePlanLlm: CheckBox
    private lateinit var planSteps: EditText
    private lateinit var poseName: EditText
    private lateinit var planList: Spinner
    private lateinit var poseList: Spinner
    private lateinit var runPlanButton: Button
    private lateinit var planArmButton: Button
    private lateinit var actionList: Spinner
    private lateinit var planActionList: Spinner
    private lateinit var runActionButton: Button
    private lateinit var appendActionButton: Button
    private lateinit var actionLibraryLabel: TextView
    private var actionNames = emptyList<String>()
    private val planButtons = mutableListOf<Button>()
    private var savedPlans = JSONArray()
    private var savedPoses = JSONArray()
    private var editingPlanId: String? = null
    private var planRunning = false
    private var replacingPlanList = false

    private val ticker = object : Runnable {
        override fun run() {
            if (foreground && connected && polling.compareAndSet(false, true)) {
                val target = client
                val epoch = commandEpoch
                polls.execute {
                    try {
                        val state = target?.call("get_status")
                        ui.post { if (foreground && target === client && connected && epoch == commandEpoch && state != null) render(state) }
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
        val tabs = LinearLayout(this)
        val controllerPanel = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val planPanel = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; visibility = View.GONE }
        tabs.addView(button("Controller") { controllerPanel.visibility = View.VISIBLE; planPanel.visibility = View.GONE }, LinearLayout.LayoutParams(0, dp(52), 1f))
        tabs.addView(button("Plan") { controllerPanel.visibility = View.GONE; planPanel.visibility = View.VISIBLE }, LinearLayout.LayoutParams(0, dp(52), 1f))
        content.addView(tabs)
        content.addView(controllerPanel)
        content.addView(planPanel)
        video = ImageView(this).apply { setBackgroundColor(Color.BLACK); scaleType = ImageView.ScaleType.FIT_CENTER; contentDescription = "Live robot camera" }
        controllerPanel.addView(video, LinearLayout.LayoutParams(-1, dp(240)))
        videoStatus = text("Camera disconnected", 13); controllerPanel.addView(videoStatus)
        cameraButton = button("Enable camera") { command("camera", JSONObject().put("enabled", !cameraOn)) }
        controllerPanel.addView(cameraButton)
        armButton = button("Arm motion") { command("arm", JSONObject().put("armed", !armed)) }
        controllerPanel.addView(armButton)
        controllerPanel.addView(text("Each movement button runs one gait cycle.", 13))
        val speedLabel = text("Movement speed: 35%", 14)
        controllerPanel.addView(speedLabel)
        speed = SeekBar(this).apply { max = 50; progress = 25 }
        speed.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?, value: Int, fromUser: Boolean) { speedLabel.text = "Movement speed: ${value + 10}%" }
            override fun onStartTrackingTouch(bar: SeekBar?) {}
            override fun onStopTrackingTouch(bar: SeekBar?) {}
        })
        controllerPanel.addView(speed)
        val row1 = LinearLayout(this)
        listOf("↶ Left" to "turn left", "↑ Forward" to "forward", "Right ↷" to "turn right").forEach { (label, action) -> row1.addView(moveButton(label, action), LinearLayout.LayoutParams(0, dp(64), 1f)) }
        controllerPanel.addView(row1)
        val row2 = LinearLayout(this)
        listOf("Stand" to "stand", "↓ Back" to "backward", "Sit" to "sit").forEach { (label, action) -> row2.addView(moveButton(label, action), LinearLayout.LayoutParams(0, dp(64), 1f)) }
        controllerPanel.addView(row2)
        controllerPanel.addView(text("Shared actions", 18))
        actionLibraryLabel = text("Connect to load the robot’s action library.", 13); controllerPanel.addView(actionLibraryLabel)
        actionList = Spinner(this).apply { contentDescription = "Shared robot action"; onItemSelectedListener = selectionUpdates() }
        controllerPanel.addView(actionList)
        runActionButton = button("Run selected action") {
            actionNames.getOrNull(actionList.selectedItemPosition - 1)?.let { name ->
                command("move", JSONObject().put("name", name).put("speed", speed.progress + 10).put("steps", 1))
            }
        }; controllerPanel.addView(runActionButton)
        controllerPanel.addView(button("Refresh action library") { command("plan_list", JSONObject(), completed = ::renderPlans) }.also { planButtons.add(it) })
        controllerPanel.addView(text("Microphone capture is never enabled by this app.", 13))
        controllerPanel.addView(text("Stop holds the last servo command; it does not remove power. Leaving this app stops motion. The robot service also has a heartbeat timeout.", 12))
        createPlanPanel(planPanel)
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
        if (pending || stopping > 0) return
        val target = try { McpClient(address.text.toString()) } catch (e: Exception) { errorLabel.text = e.message; return }
        stopRobot()
        stopVideo()
        connected = false
        client = target
        connectionLabel.text = "Connecting…"
        errorLabel.text = ""
        pending = true; updateControls()
        commands.execute {
            val epoch = commandEpoch
            try {
                if (!foreground) return@execute
                target.initialize()
                val state = target.call("get_status")
                val catalog = target.call("plan_list")
                ui.post {
                    if (foreground && target === client && epoch == commandEpoch) {
                        connected = true
                        getPreferences(MODE_PRIVATE).edit().putString("robot_url", target.baseUrl).apply()
                        connectionLabel.text = "Connected · MCP"
                        render(state)
                        renderPlans(catalog)
                    }
                }
            } catch (e: Exception) { ui.post { if (target === client) disconnected(e.message ?: "Connection failed") } }
            finally { ui.post { pending = false; updateControls() } }
        }
    }

    private fun command(tool: String, arguments: JSONObject, refreshPlans: Boolean = false, completed: ((JSONObject) -> Unit)? = null) {
        val target = client ?: return
        if (!connected || pending || !foreground || stopping > 0) return
        pending = true; updateControls(); errorLabel.text = ""
        val epoch = commandEpoch
        commands.execute {
            try {
                if (!foreground || epoch != commandEpoch) return@execute
                val result = target.call(tool, arguments)
                val catalog = if (refreshPlans) target.call("plan_list") else null
                val state = target.call("get_status")
                ui.post { if (foreground && target === client && connected && epoch == commandEpoch) {
                    render(state)
                    if (catalog != null) renderPlans(catalog)
                    completed?.invoke(result)
                } }
            } catch (e: Exception) { ui.post { errorLabel.text = e.message ?: "Command failed" } }
            finally { ui.post { pending = false; updateControls() } }
        }
    }

    /** Send an immediate stop, then another after any already-submitted arm/move finishes. */
    private fun stopRobot() {
        val target = client ?: return
        commandEpoch++
        stopping++
        armed = false
        updateControls()
        Thread({ runCatching { target.call("stop") } }, "pibotpal-immediate-stop").start()
        if (!commands.isShutdown) commands.execute {
            runCatching { target.call("stop") }
            ui.post {
                if (target === client) {
                    commandEpoch++ // discard telemetry sampled before the final stop completed
                    armed = false
                }
                stopping--
                updateControls()
            }
        } else stopping--
    }

    private fun render(state: JSONObject) {
        armed = stopping == 0 && state.optBoolean("armed")
        busy = state.optBoolean("busy")
        val execution = state.optJSONObject("plan")
        planRunning = execution?.optBoolean("running") == true
        if (::planStatus.isInitialized) planStatus.text = if (execution == null) "Plan status unavailable" else
            "${execution.optString("phase", "idle")} · step ${execution.optInt("step_index", 0) + 1}" +
                (if (!execution.isNull("error")) " · ${execution.optString("error")}" else "")
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
        armButton.isEnabled = connected && foreground && !pending && !busy && stopping == 0
        cameraButton.isEnabled = connected && foreground && !pending && stopping == 0
        motionButtons.forEach { it.isEnabled = connected && foreground && armed && !busy && !planRunning && !pending && stopping == 0 }
        if (::runActionButton.isInitialized) runActionButton.isEnabled = connected && foreground && armed && !busy && !planRunning && !pending && stopping == 0 && actionList.selectedItemPosition > 0
        if (::planArmButton.isInitialized && ::runPlanButton.isInitialized) {
            planArmButton.text = if (armed) "Disarm motion" else "Arm motion"
            planArmButton.isEnabled = connected && foreground && !pending && !busy && stopping == 0
            planButtons.forEach { it.isEnabled = connected && foreground && !pending && stopping == 0 }
            runPlanButton.isEnabled = connected && foreground && armed && !busy && !planRunning && !pending && stopping == 0 && planList.selectedItemPosition > 0
            appendActionButton.isEnabled = connected && foreground && !pending && stopping == 0 && planActionList.selectedItemPosition > 0
        }
    }


    private fun createPlanPanel(panel: LinearLayout) {
        panel.addView(text("Saved poses & motion plans", 22))
        panel.addView(text("Save commanded servo angles, draft and review steps, then save a plan. Arm explicitly before running; plans never auto-arm.", 13))
        planStatus = text("No plan running", 14); panel.addView(planStatus)
        planArmButton = button("Arm motion") { command("arm", JSONObject().put("armed", !armed)) }; panel.addView(planArmButton)
        fun action(label: String, click: () -> Unit) = button(label, click).also { planButtons.add(it); panel.addView(it) }
        action("Refresh plans, poses and actions") { command("plan_list", JSONObject(), completed = ::renderPlans) }
        panel.addView(text("Actions use the robot’s shared keyframes, including custom actions saved in the web planner. Refresh after editing them elsewhere.", 13))
        action("Edit keyframes in web planner") {
            client?.let { target -> safely { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(target.baseUrl + "/#plan"))) } }
        }
        poseName = editor("Pose name", "My pose"); panel.addView(poseName)
        action("Save current servo positions") { command("pose_save", JSONObject().put("name", poseName.text.toString()), refreshPlans = true) }
        poseList = Spinner(this); panel.addView(poseList)
        action("Append selected pose to plan") {
            val pose = savedPoses.optJSONObject(poseList.selectedItemPosition - 1)
            if (pose != null) safely {
                val steps = JSONArray(planSteps.text.toString())
                steps.put(JSONObject().put("type", "pose").put("pose_id", pose.getString("id")).put("speed", 35))
                planSteps.setText(steps.toString(2))
            }
        }
        action("Delete selected pose") {
            savedPoses.optJSONObject(poseList.selectedItemPosition - 1)?.let {
                command("pose_delete", JSONObject().put("pose_id", it.getString("id")), refreshPlans = true)
            }
        }
        panel.addView(text("Describe a plan", 16))
        planPrompt = editor("Plan instructions", "Stand. Move forward 4 steps, turn left, move forward four steps.", true)
        panel.addView(planPrompt)
        usePlanLlm = CheckBox(this).apply { text = "Use configured LLM to draft"; setTextColor(Color.WHITE); isChecked = false }; panel.addView(usePlanLlm)
        action("Draft steps for review") {
            command("plan_draft", JSONObject().put("text", planPrompt.text.toString()).put("use_llm", usePlanLlm.isChecked)) { result ->
                editingPlanId = null
                planList.setSelection(0)
                planName.setText(result.optString("name", "Draft plan"))
                planSteps.setText((result.optJSONArray("steps") ?: JSONArray()).toString(2))
                planStatus.text = "Draft only · review and save before running"
            }
        }
        planName = editor("Plan name", "My plan"); panel.addView(planName)
        panel.addView(text("Append an action from the robot", 16))
        planActionList = Spinner(this).apply { contentDescription = "Action to append to plan"; onItemSelectedListener = selectionUpdates() }
        panel.addView(planActionList)
        appendActionButton = button("Append selected action") {
            actionNames.getOrNull(planActionList.selectedItemPosition - 1)?.let { name -> safely {
                val steps = JSONArray(planSteps.text.toString())
                steps.put(JSONObject().put("type", "action").put("name", name).put("speed", speed.progress + 10).put("steps", 1))
                planSteps.setText(steps.toString(2))
            } }
        }; panel.addView(appendActionButton)
        panel.addView(text("Editable steps (JSON): action, saved pose, or wait", 13))
        planSteps = editor("Editable plan steps", "[]", true).apply { minLines = 8; typeface = android.graphics.Typeface.MONOSPACE }
        panel.addView(planSteps)
        action("Save plan") {
            safely {
                val args = JSONObject().put("name", planName.text.toString()).put("steps", JSONArray(planSteps.text.toString()))
                editingPlanId?.let { args.put("plan_id", it) }
                command("plan_save", args, refreshPlans = true) { result -> editingPlanId = result.optString("id").takeIf { it.isNotEmpty() } }
            }
        }
        action("New plan") { editingPlanId = null; planList.setSelection(0); planName.setText("My plan"); planSteps.setText("[]") }
        panel.addView(text("Saved plans", 16))
        planList = Spinner(this)
        planList.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (!replacingPlanList) savedPlans.optJSONObject(position - 1)?.let {
                    editingPlanId = it.getString("id"); planName.setText(it.getString("name"))
                    planSteps.setText((it.optJSONArray("steps") ?: JSONArray()).toString(2))
                }
                updateControls()
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
        panel.addView(planList)
        runPlanButton = button("Run selected plan") {
            if (armed && !busy && !planRunning) savedPlans.optJSONObject(planList.selectedItemPosition - 1)?.let {
                command("plan_run", JSONObject().put("plan_id", it.getString("id")))
            }
        }; panel.addView(runPlanButton)
        panel.addView(button("Stop plan & disarm") { stopRobot() })
        action("Delete selected plan") {
            savedPlans.optJSONObject(planList.selectedItemPosition - 1)?.let {
                command("plan_delete", JSONObject().put("plan_id", it.getString("id")), refreshPlans = true)
            }
        }
        renderPlans(JSONObject())
    }

    private fun renderPlans(catalog: JSONObject) {
        renderActions(catalog.optJSONArray("actions"))
        savedPoses = catalog.optJSONArray("poses") ?: JSONArray()
        savedPlans = catalog.optJSONArray("plans") ?: JSONArray()
        fun entries(rows: JSONArray, placeholder: String): List<String> = listOf(placeholder) + (0 until rows.length()).map { rows.getJSONObject(it).optString("name") }
        poseList.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, entries(savedPoses, "Choose saved pose"))
        replacingPlanList = true
        planList.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, entries(savedPlans, "Choose saved plan"))
        planList.setSelection(0)
        replacingPlanList = false
        updateControls()
    }
    private fun renderActions(actions: JSONArray?) {
        val selected = actionNames.getOrNull(actionList.selectedItemPosition - 1)
        val planSelected = actionNames.getOrNull(planActionList.selectedItemPosition - 1)
        actionNames = if (actions == null) emptyList() else (0 until actions.length())
            .mapNotNull { actions.optJSONObject(it)?.optString("name")?.takeIf(String::isNotBlank) }.distinct()
        val entries = listOf("Choose an action") + actionNames
        actionList.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, entries)
        planActionList.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, entries)
        actionList.setSelection(if (selected == null) 0 else actionNames.indexOf(selected) + 1)
        planActionList.setSelection(if (planSelected == null) 0 else actionNames.indexOf(planSelected) + 1)
        actionLibraryLabel.text = if (actionNames.isEmpty()) "Action library unavailable. Connect or update the robot service; quick controls remain available."
            else "${actionNames.size} actions from the robot. Edits in the web planner apply to these actions and the quick controls above."
    }
    private fun selectionUpdates() = object : AdapterView.OnItemSelectedListener {
        override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) { updateControls() }
        override fun onNothingSelected(parent: AdapterView<*>?) { updateControls() }
    }
    private fun editor(label: String, value: String, multiline: Boolean = false) = EditText(this).apply {
        hint = label; contentDescription = label; setText(value); textSize = 14f; setTextColor(Color.WHITE)
        if (!multiline) setSingleLine() else { inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE; minLines = 2; gravity = Gravity.TOP }
    }
    private fun safely(action: () -> Unit) { try { action() } catch (e: Exception) { errorLabel.text = e.message ?: "Invalid plan" } }

    private fun moveButton(label: String, action: String): Button = button(label) { command("move", JSONObject().put("name", action).put("speed", speed.progress + 10).put("steps", 1)) }.also { motionButtons.add(it) }
    private fun button(label: String, click: () -> Unit) = Button(this).apply { text = label; isAllCaps = false; setOnClickListener { click() } }
    private fun text(label: String, size: Int) = TextView(this).apply { text = label; textSize = size.toFloat(); setTextColor(Color.rgb(220,230,234)); setPadding(0, dp(8), 0, dp(8)) }
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    override fun onResume() { super.onResume(); foreground = true; updateControls() }
    override fun onPause() { foreground = false; stopRobot(); connected = false; stopVideo(); connectionLabel.text = "Paused · reconnect to continue"; updateControls(); super.onPause() }
    override fun onDestroy() { ui.removeCallbacks(ticker); polls.shutdownNow(); commands.shutdown(); super.onDestroy() }
}
