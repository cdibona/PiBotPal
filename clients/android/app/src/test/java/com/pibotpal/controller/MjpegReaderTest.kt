package com.pibotpal.controller

import org.junit.Assert.*
import org.junit.Test
import java.io.BufferedInputStream
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.Base64
import javax.imageio.ImageIO

class MjpegReaderTest {
    @Test fun readsConsecutiveMultipartFrames() {
        val frame = byteArrayOf(0xff.toByte(), 0xd8.toByte(), 0xff.toByte(), 0xd9.toByte())
        val part = "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\n".toByteArray() + frame + "\r\n".toByteArray()
        val reader = MjpegReader(BufferedInputStream(ByteArrayInputStream(part + part)))
        assertArrayEquals(frame, reader.nextFrame())
        assertArrayEquals(frame, reader.nextFrame())
        assertNull(reader.nextFrame())
    }
    @Test fun readsLargeJpegAndFollowingFrameAcrossShortReads() {
        // A real 8x8 JPEG with legal COM segments grows past the previous
        // 1 MiB ceiling without a large checked-in image or a codec dependency.
        val seed = Base64.getDecoder().decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAAIAAgBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAAP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AP//Z")
        val output = ByteArrayOutputStream()
        output.write(seed, 0, 2)
        val comment = byteArrayOf(0xff.toByte(), 0xfe.toByte(), 0xff.toByte(), 0xff.toByte()) + ByteArray(65533)
        repeat(17) { output.write(comment) }
        output.write(seed, 2, seed.size - 2)
        val large = output.toByteArray()
        assertTrue(large.size > 1024 * 1024)
        assertEquals(8, ImageIO.read(ByteArrayInputStream(large)).width)
        fun part(bytes: ByteArray) = "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${bytes.size}\r\n\r\n".toByteArray() + bytes + "\r\n".toByteArray()
        val fragmented = object : ByteArrayInputStream(part(large) + part(seed)) {
            override fun read(bytes: ByteArray, offset: Int, length: Int): Int = super.read(bytes, offset, minOf(length, 4093))
        }
        val reader = MjpegReader(BufferedInputStream(fragmented))
        assertArrayEquals(large, reader.nextFrame())
        assertArrayEquals(seed, reader.nextFrame())
        assertNull(reader.nextFrame())
    }
    @Test fun rejectsOversizedFrameBeforeAllocation() {
        for (size in listOf(16 * 1024 * 1024 + 1, 999999999)) {
            val result = runCatching {
                MjpegReader(BufferedInputStream(ByteArrayInputStream("--frame\r\nContent-Length: $size\r\n\r\n".toByteArray()))).nextFrame()
            }
            assertTrue("Frame limit must reject $size before reading a body", result.exceptionOrNull() is IllegalArgumentException)
        }
    }
    @Test fun rejectsRemoteCleartextAndEmbeddedCredentials() {
        for (url in listOf("http://robot.example", "https://user:password@robot.example", "https://robot.example/mcp", "https://robot.example?key=secret")) {
            assertTrue(runCatching { McpClient.validateAddress(url) }.isFailure)
        }
        assertEquals(McpClient.DEFAULT_URL, McpClient.validateAddress(McpClient.DEFAULT_URL + "/"))
    }
}
