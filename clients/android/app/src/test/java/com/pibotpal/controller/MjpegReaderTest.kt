package com.pibotpal.controller

import org.junit.Assert.*
import org.junit.Test
import java.io.BufferedInputStream
import java.io.ByteArrayInputStream

class MjpegReaderTest {
    @Test fun readsConsecutiveMultipartFrames() {
        val frame = byteArrayOf(0xff.toByte(), 0xd8.toByte(), 0xff.toByte(), 0xd9.toByte())
        val part = "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n".toByteArray() + frame + "\r\n".toByteArray()
        val reader = MjpegReader(BufferedInputStream(ByteArrayInputStream(part + part)))
        assertArrayEquals(frame, reader.nextFrame())
        assertArrayEquals(frame, reader.nextFrame())
        assertNull(reader.nextFrame())
    }
    @Test(expected = IllegalArgumentException::class) fun rejectsOversizedFrameBeforeAllocation() {
        MjpegReader(BufferedInputStream(ByteArrayInputStream("--frame\r\nContent-Length: 999999999\r\n\r\n".toByteArray()))).nextFrame()
    }
    @Test fun rejectsRemoteCleartextAndEmbeddedCredentials() {
        for (url in listOf("http://robot.example", "https://user:password@robot.example", "https://robot.example/mcp", "https://robot.example?key=secret")) {
            assertTrue(runCatching { McpClient.validateAddress(url) }.isFailure)
        }
        assertEquals(McpClient.DEFAULT_URL, McpClient.validateAddress(McpClient.DEFAULT_URL + "/"))
    }
}
