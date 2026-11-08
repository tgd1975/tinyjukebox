package io.github.tgd1975.tinyjukebox.sync

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.time.LocalDate

/** The open days of a calendar on the phone - as on a box (tests/unit/test_calendar.py). */
class CalendarTest {
    private val calendar = json(
        "target-id" to "31", "target-type" to "calendar", "name" to "Adventskalender", "uri" to "spotify:album:a",
        "mode" to "chapters", "year" to 2026, "locked" to true,
    )

    private fun day(n: Int) = LocalDate.of(2026, 12, n)

    @Test
    fun `a day opens on its date and stays open`() {
        assertEquals(false to null, Calendar.open(calendar, 17, day(16), Calendar.Kept(2026, 16)))
        val (open, keep) = Calendar.open(calendar, 17, day(17), Calendar.Kept(2026, 16))
        assertTrue(open)
        assertEquals(Calendar.Kept(2026, 17), keep)
        // the clock of the phone set back: the kept day stays open
        assertEquals(true to null, Calendar.open(calendar, 17, day(5), keep))
        assertFalse(Calendar.open(calendar, 18, day(5), keep).first)
    }

    @Test
    fun `before and after December, another year, unlocked`() {
        assertEquals(0, Calendar.reached(2026, LocalDate.of(2026, 11, 30)))
        assertEquals(24, Calendar.reached(2026, LocalDate.of(2027, 1, 1)))
        assertEquals(10, Calendar.reached(2026, day(10)))
        // what was kept for another year does not count
        assertFalse(Calendar.open(calendar, 1, LocalDate.of(2026, 11, 30), Calendar.Kept(2025, 24)).first)
        val open = json("year" to 2026, "locked" to false)
        assertEquals(true to null, Calendar.open(open, 24, LocalDate.of(2026, 1, 1), null))
        assertNull(Calendar.open(json("locked" to true), 1, day(1), null).second) // no year: never open
        assertFalse(Calendar.open(json("locked" to true), 1, day(1), null).first)
    }

    @Test
    fun `a growing calendar has one position`() {
        assertEquals("calendar:31", Calendar.key("31"))
        assertFalse(Calendar.growing(calendar))
        assertTrue(Calendar.growing(json("mode" to "growing")))
        assertTrue(Calendar.locked(json()))
    }
}
