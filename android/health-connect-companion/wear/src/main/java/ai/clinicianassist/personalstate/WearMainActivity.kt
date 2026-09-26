package ai.clinicianassist.personalstate

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.TextView

class WearMainActivity : Activity() {
    private lateinit var heartValue: TextView
    private lateinit var streamStatus: TextView
    private lateinit var streamButton: Button
    private val uiHandler = Handler(Looper.getMainLooper())

    private val refreshUi = object : Runnable {
        override fun run() {
            val snapshot = LiveHeartService.snapshot(this@WearMainActivity)
            val sampleIsCurrent = snapshot.lastSampleEpochMs > 0L &&
                System.currentTimeMillis() - snapshot.lastSampleEpochMs <= CURRENT_SAMPLE_MS
            heartValue.text = if (sampleIsCurrent && snapshot.lastBpm != null) snapshot.lastBpm.toString() else "--"
            streamStatus.text = snapshot.status
            streamButton.text = if (snapshot.monitoringRequested) "Stop monitoring" else "Start monitoring"
            uiHandler.postDelayed(this, UI_REFRESH_MS)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_wear_main)
        heartValue = findViewById(R.id.heartValue)
        streamStatus = findViewById(R.id.streamStatus)
        streamButton = findViewById(R.id.streamButton)
        streamButton.setOnClickListener {
            if (LiveHeartService.snapshot(this).monitoringRequested) {
                LiveHeartService.stop(this)
            } else {
                requestAndStart()
            }
        }
        requestAndStart()
    }

    override fun onStart() {
        super.onStart()
        uiHandler.post(refreshUi)
    }

    override fun onStop() {
        uiHandler.removeCallbacks(refreshUi)
        super.onStop()
    }

    override fun onResume() {
        super.onResume()
        if (LiveHeartService.hasLivePermission(this)) {
            LiveHeartService.start(this)
            requestBackgroundPermissionOnce()
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        when (requestCode) {
            LIVE_PERMISSION_REQUEST -> {
                if (LiveHeartService.hasLivePermission(this)) {
                    LiveHeartService.start(this)
                    requestBackgroundPermissionOnce()
                } else {
                    streamStatus.text = "Heart-rate permission is required"
                }
            }
            BACKGROUND_PERMISSION_REQUEST -> {
                if (!LiveHeartService.hasBackgroundPermission(this)) {
                    streamStatus.text = "Live now; allow all-time health access for restart recovery"
                }
            }
        }
    }

    private fun requestAndStart() {
        val missing = buildList {
            if (!LiveHeartService.hasLivePermission(this@WearMainActivity)) {
                add(LiveHeartService.livePermission())
            }
            if (Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) {
                add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        if (missing.isEmpty()) {
            LiveHeartService.start(this)
            requestBackgroundPermissionOnce()
        } else {
            requestPermissions(missing.toTypedArray(), LIVE_PERMISSION_REQUEST)
        }
    }

    private fun requestBackgroundPermissionOnce() {
        if (Build.VERSION.SDK_INT < 33 || LiveHeartService.hasBackgroundPermission(this)) return
        val preferences = getSharedPreferences(PERMISSION_PREFS, MODE_PRIVATE)
        if (preferences.getBoolean(BACKGROUND_PERMISSION_REQUESTED, false)) return
        preferences.edit().putBoolean(BACKGROUND_PERMISSION_REQUESTED, true).apply()
        requestPermissions(arrayOf(LiveHeartService.backgroundPermission()), BACKGROUND_PERMISSION_REQUEST)
    }

    companion object {
        private const val LIVE_PERMISSION_REQUEST = 41
        private const val BACKGROUND_PERMISSION_REQUEST = 42
        private const val UI_REFRESH_MS = 1_000L
        private const val CURRENT_SAMPLE_MS = 60_000L
        private const val PERMISSION_PREFS = "personal_state_permissions"
        private const val BACKGROUND_PERMISSION_REQUESTED = "background_permission_requested"
    }
}
