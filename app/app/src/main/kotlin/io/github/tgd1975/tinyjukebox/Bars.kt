package io.github.tgd1975.tinyjukebox

import android.app.Activity
import android.view.View
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

/**
 * Shows [view] as the content of the activity, clear of the status bar, the navigation bar, the cut-out of the camera
 * and the keyboard. Since Android 15 an app that targets it is drawn behind these bars: without this, the buttons at the
 * top lie under the status bar and cannot be pressed. The background of the view still fills the screen.
 */
fun Activity.showClearOfBars(view: View) {
    val left = view.paddingLeft
    val top = view.paddingTop
    val right = view.paddingRight
    val bottom = view.paddingBottom
    ViewCompat.setOnApplyWindowInsetsListener(view) { target, insets ->
        val bars = insets.getInsets(
            WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout() or WindowInsetsCompat.Type.ime(),
        )
        target.setPadding(left + bars.left, top + bars.top, right + bars.right, bottom + bars.bottom)
        WindowInsetsCompat.CONSUMED
    }
    setContentView(view)
    ViewCompat.requestApplyInsets(view)
}
