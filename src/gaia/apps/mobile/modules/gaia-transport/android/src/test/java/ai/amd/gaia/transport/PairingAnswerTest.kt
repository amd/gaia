// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ai.amd.gaia.transport

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class PairingAnswerTest {
  @Test
  fun removesTheTokenAndKeepsTheRestOnEveryStatus() {
    val paired = JSONObject(withoutToken("""{"token":"gaiad_FAKE","device_id":"d1","pc_name":"Desk"}"""))
    assertFalse(paired.has("token"))
    assertEquals("d1", paired.getString("device_id"))
    assertEquals("Desk", paired.getString("pc_name"))
    assertEquals(2, paired.length())

    val refused = JSONObject(withoutToken("""{"token":"gaiad_FAKE","detail":"Invitation expired","retry":3}"""))
    assertFalse(refused.has("token"))
    assertEquals("Invitation expired", refused.getString("detail"))
    assertEquals(3, refused.getInt("retry"))
    assertEquals(2, refused.length())
  }

  @Test
  fun dropsAnAnswerThatIsNotAJsonObject() {
    for (answer in listOf("<html>gaiad_FAKE</html>", """["gaiad_FAKE"]""", "")) {
      assertEquals(answer, "", withoutToken(answer))
    }
  }
}
